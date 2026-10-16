package dev.natro

import android.annotation.SuppressLint
import android.content.res.AssetManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.KeywordSpotter
import com.k2fsa.sherpa.onnx.KeywordSpotterConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import java.io.ByteArrayOutputStream
import java.io.File
import kotlin.math.abs
import kotlin.math.exp

/**
 * The wake word on the phone, checked on the phone: sherpa-onnx keyword spotting with a small
 * English/Chinese phoneme model (zipformer, 3M parameters), and the phrases written out as phonemes
 * (assets/wake/keywords.txt): "OK Natro", "Hey Natro" and "Natro, okay", in English spellings and
 * as the owner says them ("okay/hey NetRÓ", the last syllable stressed, the r sometimes a
 * syllable of its own). Of his 52 tries in the wake-word test on this phone it catches 34 (26 with
 * the first of these; the English spellings alone caught 8 of the first 25); of his 8 PC
 * recordings of "OK Natro" 6, with no false wake-ups in their Georgian; of Kokoro's 11 voices
 * every "OK" and "Hey Natro". Look-alikes rarely wake it ("Hey Nathan", "OK, not now" don't),
 * but "okay, natural…" does, as it did before. ~5% of one PC core. Nothing leaves the phone before
 * it.
 */
class WakeWord(assets: AssetManager) {
    private val spotter = KeywordSpotter(assets, KeywordSpotterConfig(
        featConfig = FeatureConfig(sampleRate = Recorder.RATE, featureDim = 80),
        modelConfig = OnlineModelConfig(
            transducer = OnlineTransducerModelConfig(
                encoder = "$MODEL/encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx",
                decoder = "$MODEL/decoder-epoch-13-avg-2-chunk-8-left-64.onnx",
                joiner = "$MODEL/joiner-epoch-13-avg-2-chunk-8-left-64.int8.onnx",
            ),
            tokens = "$MODEL/tokens.txt",
            numThreads = 1,
        ),
        maxActivePaths = 4,
        keywordsFile = "wake/keywords.txt",
        keywordsScore = 2.5f,
        keywordsThreshold = 0.05f,
    ))
    private val stream = spotter.createStream("")
    private val input = WakeInput()

    /** The phrase that ended in these samples (raw phone levels), as the owner says it, or null. */
    fun heard(samples: ShortArray, count: Int): String? {
        stream.acceptWaveform(input.of(samples, count), Recorder.RATE)
        var found: String? = null
        while (spotter.isReady(stream)) {
            spotter.decode(stream)
            val keyword = spotter.getResult(stream).keyword
            if (keyword.isNotBlank()) {
                spotter.reset(stream)
                found = PHRASES[keyword] ?: keyword
            }
        }
        return found
    }

    fun release() {
        stream.release()
        spotter.release()
    }

    companion object {
        private const val MODEL = "kws"
        /** The phrases' names in keywords.txt (after the @), and as the owner says them. */
        val PHRASES = mapOf("OK_NATRO" to "OK Natro", "HEY_NATRO" to "Hey Natro", "NATRO_OKAY" to "Natro, okay")
    }
}

/**
 * The microphone as the spotter hears it: turned up until the voice's peaks reach TARGET, at most
 * MAX_GAIN times; down at once when it gets louder, back up slowly. The fixed 8 times it replaces
 * suited the phone across the room, but held close the phone hears as loud as the PC, and 8 times
 * clipped: of the owner's clips made 3 times louder it caught 3 of 8, this 6. (The rumble filter
 * of Loudness made no difference here.)
 */
class WakeInput {
    private var envelope = 0.0

    fun of(samples: ShortArray, count: Int = samples.size): FloatArray = FloatArray(count) {
        val x = samples[it] / 32768.0
        envelope = maxOf(abs(x), envelope * RELEASE)
        (x * minOf(MAX_GAIN, TARGET / maxOf(envelope, 1e-9))).toFloat()
    }

    companion object {
        const val TARGET = 0.5
        const val MAX_GAIN = 10.0
        /** What the envelope keeps of itself each sample: a third is left after 2 s. */
        val RELEASE = exp(-1.0 / (2.0 * Recorder.RATE))
    }
}

/**
 * Listens for the wake word while the owner has it on (NatroService runs it, as a microphone
 * service; Android also shows its microphone dot). After it, it beeps, records the request until
 * he stops talking (the 1.5 s before the beep included, as on the PC), and sends it. It doesn't
 * listen while Natro speaks or while another recording is on. While the wake-word test runs, it
 * only tells the test what it heard, with the microphone mode the test chose.
 */
class WakeListener(private val assets: AssetManager) {
    @Volatile private var running = false
    private var worker: Thread? = null

    fun start() {
        running = true
        worker = Thread(::listen, "wake-word").also { it.start() }
    }

    fun stop() {
        running = false
        worker?.join(1000)
        worker = null
    }

    @SuppressLint("MissingPermission")  // only started with the microphone allowed
    private fun listen() {
        val wake = try {
            WakeWord(assets)
        } catch (e: Exception) {
            Natro.notice.value = "The wake word couldn't start: ${e.message}"
            return
        }
        val block = ShortArray(Recorder.RATE / 10)  // 100 ms
        var mic = wantedMic()
        var record = open(mic, block.size)
        val before = ArrayDeque<ShortArray>()  // the last 1.5 s
        var request: ByteArrayOutputStream? = null
        var end = SpeechEnd()
        var loudness = Loudness()
        var which = 0
        try {
            while (running) {
                if (wantedMic() != mic && request == null) {  // the test switched the microphone mode
                    record.stop()
                    record.release()
                    mic = wantedMic()
                    record = open(mic, block.size)
                    before.clear()
                }
                val count = record.read(block, 0, block.size)
                if (count <= 0) continue
                val chunk = block.copyOf(count)
                if (request != null) {
                    if (!Natro.stillRecording(which)) {  // cancelled
                        request = null
                        before.clear()
                        continue
                    }
                    request.write(Recorder.bytes(chunk))
                    val level = loudness.of(chunk)
                    Natro.hear(level)
                    end.add(level)
                    val finished = Natro.finishWakeAsked()
                    if (end.done || finished) {
                        if (end.heard || finished) Natro.sendRecording(request.toByteArray()) else Natro.nothingHeard()
                        request = null
                        before.clear()
                    }
                    continue
                }
                if (Natro.recording.value || Natro.speaking) {
                    before.clear()
                    continue
                }
                val phrase = wake.heard(chunk, count)
                if (WakeTest.running.value) {
                    WakeTest.hear(chunk, phrase, mic)
                    continue
                }
                before.addLast(chunk)
                if (before.size > 15) before.removeFirst()
                if (phrase != null) {
                    which = Natro.wakeHeard()
                    request = ByteArrayOutputStream().also { out -> before.forEach { out.write(Recorder.bytes(it)) } }
                    end = SpeechEnd()
                    loudness = Loudness()
                }
            }
        } finally {
            record.stop()
            record.release()
            wake.release()
        }
    }

    private fun wantedMic() = if (WakeTest.running.value) WakeTest.mic.value else WakeTest.Mic.Standard

    @SuppressLint("MissingPermission")
    private fun open(mic: WakeTest.Mic, block: Int): AudioRecord {
        val record = AudioRecord(mic.source, Recorder.RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, block * 2 * 4)
        try {
            record.startRecording()
        } catch (e: IllegalStateException) {  // this phone has no such mode: the test goes back to the standard one
            record.release()
            if (mic == WakeTest.Mic.Standard) throw e
            Natro.notice.value = "This phone has no \"${mic.label}\" microphone."
            WakeTest.use(WakeTest.Mic.Standard)
            return open(WakeTest.Mic.Standard, block)
        }
        return record
    }
}

/**
 * The wake-word test (Settings): the owner says a wake phrase a few times, pausing after each, and
 * sees which tries were caught. Nothing is recorded for Natro or sent while it runs. Each try is
 * kept in the cache until he starts over (also when he leaves the screen or the app restarts), so
 * the phrases and the microphone can be tuned to his voice on this phone (debug builds:
 * adb exec-out run-as dev.natro cat cache/wake-test/try-01-standard-hey-natro.pcm).
 */
object WakeTest {
    /**
     * The microphone modes to compare: the standard one, and the one Android tunes for speech
     * recognition (no noise reduction or automatic volume, and quieter). Requests are recorded
     * with the standard one; the other is for trying.
     */
    enum class Mic(val source: Int, val label: String) {
        Standard(MediaRecorder.AudioSource.MIC, "Standard"),
        Recognition(MediaRecorder.AudioSource.VOICE_RECOGNITION, "Speech recognition"),
    }

    /** A try: the phrase caught in it (or null), how loud the owner was (Loudness, its loudest 100 ms), and the mode. */
    data class Try(val number: Int, val phrase: String?, val loudness: Double, val mic: Mic)

    val running = MutableStateFlow(false)
    val mic = MutableStateFlow(Mic.Standard)
    val tries = MutableStateFlow<List<Try>>(emptyList())
    /** How loud the microphone is now, 0 to 1. */
    val level = MutableStateFlow(0f)
    @Volatile private var folder: File? = null
    @Volatile private var split = Tries()
    @Volatile private var meter = Loudness()

    fun start(cache: File) {
        val saved = File(cache, "wake-test").apply { mkdirs() }
        folder = saved
        if (tries.value.isEmpty()) tries.value = saved.listFiles().orEmpty().mapNotNull(::read).sortedBy { it.number }
        restart()
        running.value = true
    }

    fun stop() {
        running.value = false
        level.value = 0f
    }

    fun clear() {
        folder?.listFiles()?.forEach { it.delete() }
        tries.value = emptyList()
        restart()
    }

    fun use(mode: Mic) {
        mic.value = mode
        restart()
    }

    private fun restart() {
        split = Tries()
        meter = Loudness()
    }

    /** 100 ms from the wake listener, the phrase heard in it, and the mode it was recorded with (on the listener's thread). */
    fun hear(block: ShortArray, phrase: String?, mode: Mic) {
        val loudness = meter.of(block)
        level.value = SpeechEnd.level(loudness)
        val done = split.add(block, loudness, phrase) ?: return
        val number = (tries.value.maxOfOrNull { it.number } ?: 0) + 1
        folder?.let { File(it, "try-%02d-%s-%s.pcm".format(number, mode.name.lowercase(), slug(done.phrase))).writeBytes(done.pcm) }
        tries.update { it + Try(number, done.phrase, done.loudness, mode) }
    }

    /** A phrase in a file name: "hey-natro", or "missed". */
    private fun slug(phrase: String?) = phrase?.lowercase()?.replace(Regex("[^a-z]+"), "-")?.trim('-') ?: "missed"

    /** A try saved before: try-NN-mode-phrase.pcm. */
    private fun read(file: File): Try? {
        val (number, mode, said) = Regex("""try-(\d+)-([a-z]+)-([a-z-]+)\.pcm""").matchEntire(file.name)?.destructured ?: return null
        val mic = Mic.entries.firstOrNull { it.name.lowercase() == mode } ?: return null
        val pcm = file.readBytes()
        val samples = ShortArray(pcm.size / 2) { ((pcm[2 * it + 1].toInt() shl 8) or (pcm[2 * it].toInt() and 0xFF)).toShort() }
        val meter = Loudness()
        val loudest = (samples.indices step Recorder.RATE / 10).maxOfOrNull { start ->
            meter.of(samples.copyOfRange(start, minOf(start + Recorder.RATE / 10, samples.size)))
        } ?: 0.0
        return Try(number.toInt(), WakeWord.PHRASES.values.firstOrNull { slug(it) == said }, loudest, mic)
    }
}

/**
 * Splits the microphone into the owner's tries at the wake word: from speech (louder than the room,
 * as in SpeechEnd), with the half second before it, until a second of quiet. A try caught the
 * phrase if the spotter heard it any time in between (it hears it a moment after it's said).
 */
class Tries {
    class Done(val pcm: ByteArray, val phrase: String?, val loudness: Double)

    private val before = ArrayDeque<ShortArray>()
    private var audio: ByteArrayOutputStream? = null
    private var phrase: String? = null
    private var loudest = 0.0
    private var floor = Double.MAX_VALUE
    private var quiet = 0
    private var blocks = 0

    /** A 100 ms block, its Loudness and the phrase heard in it: the try this ended, if it ended one. */
    fun add(block: ShortArray, loudness: Double, heard: String?): Done? {
        if (loudness >= 1.0) floor = minOf(floor, loudness)
        val room = if (floor == Double.MAX_VALUE) 1.0 else floor
        val speech = loudness > maxOf(SpeechEnd.MIN_SPEECH, SpeechEnd.SPEECH_OVER_NOISE * room)
        val out = audio ?: if (speech || heard != null) {
            ByteArrayOutputStream().also { out ->
                before.forEach { out.write(Recorder.bytes(it)) }
                before.clear()
                audio = out
                phrase = null
                loudest = 0.0
                quiet = 0
                blocks = 0
            }
        } else {
            before.addLast(block)
            if (before.size > LEAD) before.removeFirst()
            return null
        }
        out.write(Recorder.bytes(block))
        blocks++
        if (heard != null) phrase = heard
        loudest = maxOf(loudest, loudness)
        quiet = if (speech) 0 else quiet + 1
        if (quiet < PAUSE && blocks < LONGEST) return null
        audio = null
        return Done(out.toByteArray(), phrase, loudest)
    }

    private companion object {
        const val LEAD = 5  // 0.5 s
        const val PAUSE = 10  // 1 s
        const val LONGEST = 80  // 8 s
    }
}
