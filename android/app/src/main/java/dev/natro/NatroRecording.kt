package dev.natro

import dev.natro.Natro.Status
import android.media.AudioManager
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.io.File

// Recording him: a request, held or hands-free (it ends when he stops talking), "OK Natro"'s, and listening after a
// reply for him to go on. What's recorded goes to the brain, as a request or as the answer to her question.

/** Record a request: until stopRecording (the mic button is let go) or, handsFree, until the owner stops talking. */
fun Natro.startRecording(handsFree: Boolean = false) {
    if (recording.value) return
    moreDue.incrementAndGet()
    speaker?.stop()  // don't record Natro herself
    recordings.incrementAndGet()
    speechEnd = if (handsFree) SpeechEnd() else null
    try {
        recorder.start { loudness ->
            hear(loudness)
            val end = speechEnd ?: return@start
            end.add(loudness)
            if (end.done) {
                speechEnd = null
                scope.launch { if (end.heard) stopRecording() else nothingHeard() }
            }
        }
    } catch (e: Exception) {  // the microphone is someone else's right now (a call, another app)
        speechEnd = null
        notice.value = "Couldn't use the microphone: ${e.message ?: e.javaClass.simpleName}"
        bubbleEnds(BubbleKind.Problem, "Microphone busy", PROBLEM_MILLIS)
        return
    }
    this.handsFree.value = handsFree
    recording.value = true
    bubbleShows(BubbleKind.Listening, "Listening…")
}

/** The mic button was only tapped, not held: keep listening until the owner stops talking. */
fun Natro.keepListening() {
    if (!recording.value || speechEnd != null || wakeRecording) return
    speechEnd = SpeechEnd()
    handsFree.value = true
}

/** The assistant gesture: a beep, then record until the owner stops talking. */
fun Natro.listen() {
    if (recording.value) return
    beep()
    startRecording(handsFree = true)
}

/** Stop recording and send it: as the answer to her question if she's asking, else as a request. */
fun Natro.stopRecording() {
    if (listeningForMore.value) {  // a tap on the bubble: what he's said so far goes, and if nothing, nothing does
        if (speechEnd?.spoke == true) sendRecording(recorder.stop()) else endListeningForMore()
        return
    }
    if (wakeRecording) {
        finishWake = true  // the "OK Natro" listener sends its recording
        return
    }
    val raw = recorder.stop()
    if (!recording.value) return
    if (raw.size < Recorder.BYTES_PER_SECOND / 2) nothingHeard() else sendRecording(raw)
}

/** Stop recording and throw it away. */
fun Natro.dropRecording() {
    recorder.stop()
    endRecording()
    keepMic.value = false
    bubble.value = null
}

/** The recording had nothing in it. */
fun Natro.nothingHeard() {
    recorder.stop()
    endRecording()
    keepMic.value = false
    bubbleEnds(BubbleKind.Problem, "Didn't hear anything", PROBLEM_MILLIS)
}

internal fun Natro.endRecording() {
    recording.value = false
    handsFree.value = false
    listeningForMore.value = false
    speechEnd = null
    wakeRecording = false
    level.value = 0f
}

// Listening after a reply

/** She replied, and if it says he can go on by just talking, for this many seconds: listen once she's quiet. */
internal fun Natro.listenAfter(seconds: Double?) {
    if (seconds == null || !Prefs.keepListening.value) {
        if (pending.isEmpty()) keepMic.value = false
        return
    }
    val due = moreDue.incrementAndGet()
    val listen = {
        scope.launch {
            delay(DONE_BEFORE_LISTENING_MILLIS)
            if (moreDue.get() == due) listenForMore(seconds)
            else if (!recording.value && pending.isEmpty()) keepMic.value = false
        }
        Unit
    }
    speaker?.whenQuiet(listen) ?: listen()
}

/**
 * Listen for him to go on, unless he's moved on or can't be heard. In the app he types (the bubble's away), and the
 * chat's input would make way for this. What's playing here it would hear, and pay to recognize.
 */
internal fun Natro.listenForMore(seconds: Double) {
    val audio = app?.getSystemService(AudioManager::class.java)
    if (recording.value || pending.isNotEmpty() || question.value != null || onScreen.value || !Prefs.keepListening.value ||
        status.value != Status.Online || !NatroService.mic.value || audio == null || soundOn(audio)) {
        keepMic.value = false
        return
    }
    recordings.incrementAndGet()
    val end = SpeechEnd(wait = (seconds * BLOCKS_A_SECOND).toInt())
    speechEnd = end
    listeningForMore.value = true
    try {
        recorder.start { loudness ->
            hear(loudness)
            if (speechEnd !== end) return@start
            end.add(loudness)
            val started = !end.heard && soundOn(audio)
            if (started || end.done) {
                speechEnd = null
                scope.launch { if (end.spoke && !started) sendRecording(recorder.stop()) else endListeningForMore() }
            }
        }
    } catch (e: Exception) {  // the microphone is someone else's right now
        speechEnd = null
        listeningForMore.value = false
        keepMic.value = false
        return
    }
    handsFree.value = true
    recording.value = true
    bubbleShows(BubbleKind.Listening, "Listening…")
}

/** Something's playing, or a call is on: what it would hear isn't him. */
internal fun Natro.soundOn(audio: AudioManager) = audio.isMusicActive || audio.mode != AudioManager.MODE_NORMAL

/** Stop listening for him to go on (silence, its ✕, the app opening): nothing goes anywhere. */
fun Natro.endListeningForMore() {
    if (!listeningForMore.value) return
    recorder.stop()
    endRecording()
    keepMic.value = false
    bubbleTurn.incrementAndGet()
    bubble.value = null
}

/** Send a recording (raw phone levels; made louder here): a spoken request, or the answer to her question. */
fun Natro.sendRecording(raw: ByteArray) {
    val following = listeningForMore.value
    // Before the recording ends, or the service could give the microphone up in between.
    if (Prefs.keepListening.value) keepMic.value = true
    endRecording()
    val pcm = Recorder.louder(raw)
    // Debug builds keep the last recording, to check the microphone's levels: adb exec-out run-as dev.natro cat cache/last.pcm
    if (BuildConfig.DEBUG) cacheDir?.let { File(it, "last.pcm").writeBytes(raw) }
    val id = newId()
    val message = JSONObject().put("type", "audio").put("id", id)
    if (following) message.put("follow_up", true)
    val asked = question.value
    if (asked == null) {
        turns.update { Chat.asked(it, id, Turn.From.Spoken, "", now()) }
        request(id, message, pcm)
        return
    }
    message.put("answer_to", asked.id)
    question.value = null
    asked.turn?.let { answers[id] = it }
    if (send(message)) {
        socket?.send(pcm.toByteString())
        bubbleShows(BubbleKind.Thinking, "Thinking…")
    } else {
        keepMic.value = false
        bubbleEnds(BubbleKind.Problem, "Natro is offline", PROBLEM_MILLIS)
    }
}

/** "OK Natro" was heard. The listener records the request itself; this returns its recording's number. */
fun Natro.wakeHeard(): Int {
    moreDue.incrementAndGet()
    speaker?.stop()
    beep()
    wakeRecording = true
    finishWake = false
    handsFree.value = true
    recording.value = true
    bubbleShows(BubbleKind.Listening, "Listening…")
    return recordings.incrementAndGet()
}

/** Whether recording number `which` is still on (not cancelled). */
fun Natro.stillRecording(which: Int) = recording.value && recordings.get() == which

/** Whether the owner asked to end the "OK Natro" recording now (the mic button). Asking clears it. */
fun Natro.finishWakeAsked(): Boolean {
    if (!finishWake) return false
    finishWake = false
    return true
}

/** The loudness of the latest 100 ms of the owner's voice (raw phone levels). */
fun Natro.hear(loudness: Double) {
    level.value = SpeechEnd.level(loudness)
}
