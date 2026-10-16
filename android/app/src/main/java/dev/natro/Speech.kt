package dev.natro

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * When a spoken request is over, from the loudness of each 100 ms block (Loudness, of raw phone
 * levels, before Recorder.louder): after `silence` blocks of quiet once speech was heard, after
 * `wait` blocks with no speech at all, or after `longest` blocks in any case.
 *
 * Speech is louder than the room (its quietest block, not counting silent ones: the microphone
 * starting) and, once he has said a few words, than a fifth of the owner's usual loudness in this
 * recording. A recording that never ended needed both: its first block was
 * silent, which held the room at nothing, and after he stopped, the noise stayed well above the
 * room but far below his voice.
 */
class SpeechEnd(
    private val silence: Int = 20,  // 2 s
    private val wait: Int = 60,  // 6 s
    private val longest: Int = 600,  // a minute
) {
    private var floor = Double.MAX_VALUE
    private var blocks = 0
    private var quiet = 0
    /** The loudness of each block that was speech, in order. */
    private val voice = mutableListOf<Double>()
    var heard = false
        private set
    var done = false
        private set

    fun add(loudness: Double) {
        blocks++
        if (loudness >= 1.0) floor = minOf(floor, loudness)
        val room = if (floor == Double.MAX_VALUE) 1.0 else floor
        val usual = if (voice.size >= USUAL_AFTER) voice.sorted()[voice.size / 2] else 0.0
        if (loudness > maxOf(MIN_SPEECH, SPEECH_OVER_NOISE * room, SPEECH_SHARE * usual)) {
            voice += loudness
            heard = true
            quiet = 0
        } else if (heard) {
            quiet++
        }
        done = (heard && quiet >= silence) || (!heard && blocks >= wait) || blocks >= longest
    }

    companion object {
        // The phone's microphone: background ~1-5, speech ~30-2,500 (see Recorder.louder).
        const val MIN_SPEECH = 30.0
        const val SPEECH_OVER_NOISE = 4.0
        /** Once he has spoken this many blocks, speech is also louder than SPEECH_SHARE of his usual (the median). */
        const val USUAL_AFTER = 5
        const val SPEECH_SHARE = 0.2

        /** A block's loudness as 0 to 1 for the dock's dot, on a log scale: background (~3) about 0.1, speech (30-1,000) 0.5-1. */
        fun level(loudness: Double): Float =
            ((kotlin.math.log10(maxOf(loudness, 1.0)) - 0.3) / 2.4).coerceIn(0.0, 1.0).toFloat()

    }
}

/**
 * How loud 100 ms of the microphone is: the mean absolute sample after the rumble filter
 * (HighPass). One per recording, as the filter carries the last samples over to the next block.
 */
class Loudness {
    private val filter = HighPass()

    fun of(samples: ShortArray, count: Int = samples.size): Double {
        if (count == 0) return 0.0
        var sum = 0.0
        for (i in 0 until count) sum += abs(filter.next(samples[i].toDouble()))
        return sum / count
    }
}

/**
 * A high-pass filter (second-order Butterworth at 150 Hz) that takes out rumble, from the phone
 * being held or the wind, which the phone's microphone hears loudly: in a recording that never
 * ended, 90-98% of the sound after the owner stopped talking was under 100 Hz (0-2% of his
 * speech), and it counted as speech. One per stream of samples, as it remembers the last ones.
 */
class HighPass {
    private var x1 = 0.0
    private var x2 = 0.0
    private var y1 = 0.0
    private var y2 = 0.0

    fun next(x: Double): Double {
        val y = B0 * x + B1 * x1 + B2 * x2 - A1 * y1 - A2 * y2
        x2 = x1
        x1 = x
        y2 = y1
        y1 = y
        return y
    }

    private companion object {
        const val CUTOFF = 150.0
        // The high-pass of the Audio EQ Cookbook (Q = 1/√2), divided through by a0.
        val W = 2 * PI * CUTOFF / Recorder.RATE
        val ALPHA = sin(W) / sqrt(2.0)
        val A0 = 1 + ALPHA
        val B0 = (1 + cos(W)) / 2 / A0
        val B1 = -(1 + cos(W)) / A0
        val B2 = B0
        val A1 = -2 * cos(W) / A0
        val A2 = (1 - ALPHA) / A0
    }
}

/** Text helpers for contacts and the screen. */
object Text {
    private val GEORGIAN = mapOf(
        'ა' to "a", 'ბ' to "b", 'გ' to "g", 'დ' to "d", 'ე' to "e", 'ვ' to "v", 'ზ' to "z", 'თ' to "t", 'ი' to "i",
        'კ' to "k", 'ლ' to "l", 'მ' to "m", 'ნ' to "n", 'ო' to "o", 'პ' to "p", 'ჟ' to "zh", 'რ' to "r", 'ს' to "s",
        'ტ' to "t", 'უ' to "u", 'ფ' to "p", 'ქ' to "k", 'ღ' to "gh", 'ყ' to "q", 'შ' to "sh", 'ჩ' to "ch",
        'ც' to "ts", 'ძ' to "dz", 'წ' to "ts", 'ჭ' to "ch", 'ხ' to "kh", 'ჯ' to "j", 'ჰ' to "h",
    )

    /** Georgian letters in Latin ones ("ნინო" -> "nino"), so contacts saved either way match a spoken name. */
    fun latin(text: String): String = text.lowercase().map { GEORGIAN[it] ?: it.toString() }.joinToString("")

    /** Buttons that send, buy, pay or delete: tapping them needs the owner's yes (tap_risky). */
    val RISKY = Regex(
        "\\b(send|buy|purchase|pay|payment|order|checkout|check out|delete|remove|erase|clear all|uninstall|" +
            "transfer|submit|post|publish|share|call|subscribe|unsubscribe|book|block|report|sign out|log out)\\b|" +
            "გაგზავნ|წაშალ|წაშლა|ყიდვა|იყიდე|გადახდ|შეკვეთ|გაზიარ|დარეკ",
        RegexOption.IGNORE_CASE,
    )

    fun short(text: String, length: Int = 80): String {
        val flat = text.replace(Regex("\\s+"), " ").trim()
        return if (flat.length <= length) flat else flat.take(length - 1) + "…"
    }
}
