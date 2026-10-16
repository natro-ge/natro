package dev.natro

import android.app.role.RoleManager
import android.content.Context
import android.media.AudioManager
import android.media.ToneGenerator
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

/** Which picture the bubble shows. */
enum class BubbleKind { Listening, Thinking, Step, Asking, Done, Problem, Off }

/** What the bubble says: a picture and a few words ("Opening Spotify"). */
data class BubbleStatus(val kind: BubbleKind, val label: String)

/** Natro's reply to a request the phone made, as written so far (done: all of it), for the bubble's reply card. */
data class ReplyText(val id: String, val text: String, val done: Boolean)

/**
 * Natro on the phone: the connection to her brain on the VPS (over Tailscale), the chat, what the
 * bubble says, recording, speaking, and the phone's tools. The messages are the ones in
 * agent/natro_agent/server.py; files between the phone and the PC are Transfers'.
 *
 * One for the whole app. init() loads the chat; NatroService starts and stops the connection, so
 * it stays open with the screen off and the brain can use the phone's tools even for a request
 * made on the PC.
 */
object Natro {
    sealed interface Status {
        /** Turned off by the owner. */
        data object Off : Status
        data object Connecting : Status
        data object Online : Status
        data class Offline(val reason: String) : Status
    }

    /** Natro's question before something risky; turn is the request it's for. */
    data class Question(val id: String, val turn: String?, val text: String, val details: String)

    /** This month's spending on the brain, in dollars. */
    data class Spend(val spent: Double, val budget: Double)

    val status = MutableStateFlow<Status>(Status.Off)
    val turns = MutableStateFlow<List<Turn>>(emptyList())
    val question = MutableStateFlow<Question?>(null)
    /** Whether a request is being recorded: the mic button, the assistant gesture or "OK Natro". */
    val recording = MutableStateFlow(false)
    /** Whether the recording ends by itself when the owner stops talking, rather than when he lets go of the button. */
    val handsFree = MutableStateFlow(false)
    /** How loud the owner is while recorded, 0 to 1. */
    val level = MutableStateFlow(0f)
    /** What the bubble says, or null when it has nothing to say. */
    val bubble = MutableStateFlow<BubbleStatus?>(null)
    /** Her latest reply to the phone, as it's written (the bubble's reply card). */
    val reply = MutableStateFlow<ReplyText?>(null)
    val spend = MutableStateFlow<Spend?>(null)
    /** Whether the app is on screen: the bubble keeps out of its way. */
    val onScreen = MutableStateFlow(false)
    /** A short message for the owner, shown for a moment in the app. */
    val notice = MutableStateFlow<String?>(null)

    /** Whether Natro is speaking a reply. */
    val speaking get() = speaker?.speaking == true

    /** Whether Android lets Natro open apps and other screens now: on screen, as the assistant, with screen control, or with the bubble up. */
    fun canOpenScreens(context: Context) = onScreen.value || ScreenControl.current != null || Bubble.showing ||
        context.getSystemService(RoleManager::class.java).isRoleHeld(RoleManager.ROLE_ASSISTANT)

    private val client = OkHttpClient.Builder().pingInterval(30, TimeUnit.SECONDS).build()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val sequence = AtomicLong()
    private val recorder = Recorder()
    private lateinit var history: History
    private var tools: PhoneTools? = null
    private var cacheDir: File? = null
    @Volatile private var speaker: Speaker? = null
    @Volatile private var socket: WebSocket? = null
    @Volatile private var running = false
    @Volatile private var refused = false
    private var retryMillis = RETRY_FIRST_MILLIS

    /** The phone's requests the brain is still working on, oldest first. Tool calls and questions are for the first. */
    private val pending = CopyOnWriteArrayList<String>()
    /** A spoken answer's request id -> the request whose question it answers. */
    private val answers = ConcurrentHashMap<String, String>()
    /** A cancelled request, until the brain says it stopped: tool calls until then were still for it, and are refused. */
    @Volatile private var cancelling: String? = null
    @Volatile private var cancelledAt = 0L
    /** Decides when a hands-free recording is over; null while the button is held. */
    @Volatile private var speechEnd: SpeechEnd? = null
    /** Counts recordings, so the "OK Natro" listener can tell that its own was cancelled. */
    private val recordings = AtomicInteger()
    @Volatile private var wakeRecording = false
    @Volatile private var finishWake = false
    private val bubbleTurn = AtomicInteger()

    fun init(context: Context) {
        if (::history.isInitialized) return
        val app = context.applicationContext
        cacheDir = app.cacheDir
        tools = PhoneTools(app)
        history = History(File(app.filesDir, "chat.json"))
        turns.value = Chat.interrupted(history.load())
        scope.launch(Dispatchers.IO) {
            // Saved a moment after the last change: a streamed reply is written once, not per piece.
            turns.drop(1).collectLatest {
                delay(SAVE_DELAY_MILLIS)
                history.save(it)
            }
        }
    }

    /** Connect, and keep connecting (NatroService, while Natro is on). */
    fun start(context: Context) {
        if (running) return
        running = true
        speaker = Speaker(context.applicationContext)
        refused = false
        retryMillis = RETRY_FIRST_MILLIS
        connect()
    }

    /** Disconnect and stay so (Natro turned off). */
    fun stop() {
        running = false
        val open = socket
        socket = null
        open?.close(1000, "turned off")
        dropAll("Natro was turned off.")
        Transfers.lost()
        speaker?.shutdown()
        speaker = null
        status.value = Status.Off
    }

    // The connection

    private fun connect() {
        status.value = Status.Connecting
        socket = client.newWebSocket(Request.Builder().url(BuildConfig.NATRO_SERVER).build(), Listener())
    }

    private class Listener : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            webSocket.send(JSONObject().put("type", "hello").put("device", "phone").put("label", "phone")
                .put("token", BuildConfig.NATRO_TOKEN).put("tools", tools?.specs()).put("files", true).toString())
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (webSocket === socket) handle(JSONObject(text))
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            if (webSocket === socket) Transfers.piece(bytes)
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            if (code == 1008) refused = true  // a wrong device token
            webSocket.close(1000, null)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = lost(webSocket, "closed")

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) =
            lost(webSocket, t.message ?: t.javaClass.simpleName)
    }

    private fun lost(webSocket: WebSocket, reason: String) {
        if (webSocket !== socket) return
        socket = null
        dropAll("Lost the connection to Natro.")
        Transfers.lost()
        if (!running) return
        if (refused) {
            status.value = Status.Offline("Natro refused this phone: check natro.token in local.properties")
            return
        }
        status.value = Status.Offline(reason)
        val wait = retryMillis
        retryMillis = minOf(retryMillis * 2, RETRY_MAX_MILLIS)
        scope.launch {
            delay(wait)
            if (socket == null && running) connect()
        }
    }

    /** Try again now (the status line, tapped). */
    fun reconnect() {
        if (!running || status.value == Status.Online) return
        refused = false
        retryMillis = RETRY_FIRST_MILLIS
        val old = socket
        socket = null
        old?.cancel()
        connect()
    }

    private fun send(message: JSONObject): Boolean = socket?.send(message.toString()) ?: false

    /** For Transfers: a message, and a piece of a file right after its file_part. False when offline. */
    fun sendMessage(message: JSONObject): Boolean = send(message)

    fun sendBytes(bytes: ByteString): Boolean = socket?.send(bytes) ?: false

    /** The brain won't answer these any more: the connection went, or Natro was turned off. */
    private fun dropAll(problem: String) {
        val dropped = pending.toList()
        pending.clear()
        question.value = null
        if (dropped.isEmpty()) return
        turns.update { all -> dropped.fold(all) { list, id -> Chat.failed(list, id, problem) } }
        if (running) bubbleEnds(BubbleKind.Problem, "Natro is offline", PROBLEM_MILLIS)
    }

    // Messages from Natro

    private fun handle(data: JSONObject) {
        val id = data.optString("id")
        when (data.optString("type")) {
            "welcome" -> {
                status.value = Status.Online
                retryMillis = RETRY_FIRST_MILLIS
                spent(data)
                Transfers.online()
            }
            "english" -> if (!answers.containsKey(id)) turns.update { Chat.english(it, id, data.optString("text")) }
            "heard" -> {
                val answering = answers.remove(id)
                if (answering != null) {
                    turns.update { Chat.answered(it, answering, data.optString("english").ifBlank { "(not heard)" }) }
                } else {
                    turns.update { Chat.heard(it, id, data.optString("georgian"), data.optString("english")) }
                }
            }
            "step" -> {
                val text = data.optString("text").ifBlank { "Working…" }
                turns.update { Chat.step(it, id, text) }
                if (id in pending && question.value == null) bubbleShows(BubbleKind.Step, text)
            }
            "reply_text" -> {
                val piece = data.optString("text")
                turns.update { Chat.replyText(it, id, piece) }
                if (id in pending) reply.update { if (it?.id == id) it.copy(text = it.text + piece) else ReplyText(id, piece, false) }
            }
            "reply" -> {
                spent(data)
                val text = data.optString("text")
                turns.update {
                    Chat.replied(it, id, text, data.optString("model"), data.number("cost"), data.number("seconds"),
                        toolRuns(data.optJSONArray("tools")))
                }
                if (pending.remove(id)) {
                    reply.update { ReplyText(id, text.ifBlank { it?.takeIf { old -> old.id == id }?.text.orEmpty() }, true) }
                    if (Prefs.speakReplies.value) speaker?.say(text)
                    if (pending.isEmpty()) bubbleEnds(BubbleKind.Done, "Done", DONE_MILLIS)
                }
            }
            "confirm" -> {
                val text = data.optString("question")
                val details = data.optString("details")
                val turn = pending.firstOrNull()
                question.value = Question(id, turn, text, details)
                turn?.let { asking -> turns.update { Chat.asking(it, asking, text, details) } }
                bubbleShows(BubbleKind.Asking, Text.short(text, 90))
                if (Prefs.speakReplies.value) speaker?.say(text)
            }
            "tool_call" -> scope.launch { runTool(id, data.optString("name"), data.optJSONObject("args") ?: JSONObject()) }
            "error" -> {
                turns.update { Chat.failed(it, id, data.optString("message")) }
                if (pending.remove(id) && pending.isEmpty()) bubbleEnds(BubbleKind.Problem, "Something went wrong", PROBLEM_MILLIS)
            }
            "cancelled" -> if (cancelling == id) cancelling = null
            else -> if (data.optString("type").startsWith("file_")) Transfers.handle(data)
        }
    }

    private suspend fun runTool(id: String, name: String, args: JSONObject) {
        val phone = tools ?: return
        val cancelled = cancelling != null && System.currentTimeMillis() - cancelledAt < CANCEL_WAIT_MILLIS
        val (ok, result) = if (cancelled) false to "The owner cancelled this request." else try {
            true to withContext(Dispatchers.IO) { phone.run(name, args) }
        } catch (e: ToolFailure) {
            false to (e.message ?: "$name failed.")
        } catch (e: Exception) {
            false to "$name failed on the phone: ${e.javaClass.simpleName}: ${e.message}"
        }
        send(JSONObject().put("type", "tool_result").put("id", id).put("ok", ok).put("result", result))
        if (cancelled) return
        val shown = if (result.length > TOOL_RESULT_CHARS) result.take(TOOL_RESULT_CHARS - 1) + "…" else result
        turns.update { Chat.toolRan(it, pending.firstOrNull(), ToolRun("phone_$name", args.toString(), ok, false, shown), now()) }
    }

    private fun spent(data: JSONObject) {
        val spent = data.number("spent") ?: return
        spend.value = Spend(spent, data.number("budget") ?: 0.0)
    }

    // What the owner does

    fun sendText(text: String) {
        val id = newId()
        turns.update { Chat.asked(it, id, Turn.From.Typed, text, now()) }
        request(id, JSONObject().put("type", "text").put("id", id).put("text", text))
    }

    private fun request(id: String, message: JSONObject, recording: ByteArray? = null) {
        if (!send(message)) {
            turns.update { Chat.failed(it, id, "Not sent: Natro is offline.") }
            bubbleEnds(BubbleKind.Problem, "Natro is offline", PROBLEM_MILLIS)
            return
        }
        recording?.let { socket?.send(it.toByteString()) }
        pending += id
        bubbleShows(BubbleKind.Thinking, "Thinking…")
    }

    /** Record a request: until stopRecording (the mic button is let go) or, handsFree, until the owner stops talking. */
    fun startRecording(handsFree: Boolean = false) {
        if (recording.value) return
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
    fun keepListening() {
        if (!recording.value || speechEnd != null || wakeRecording) return
        speechEnd = SpeechEnd()
        handsFree.value = true
    }

    /** The assistant gesture: a beep, then record until the owner stops talking. */
    fun listen() {
        if (recording.value) return
        beep()
        startRecording(handsFree = true)
    }

    /** Stop recording and send it: as the answer to her question if she's asking, else as a request. */
    fun stopRecording() {
        if (wakeRecording) {
            finishWake = true  // the "OK Natro" listener sends its recording
            return
        }
        val raw = recorder.stop()
        if (!recording.value) return
        if (raw.size < Recorder.BYTES_PER_SECOND / 2) nothingHeard() else sendRecording(raw)
    }

    /** Stop recording and throw it away. */
    fun dropRecording() {
        recorder.stop()
        endRecording()
        bubble.value = null
    }

    /** The recording had nothing in it. */
    fun nothingHeard() {
        recorder.stop()
        endRecording()
        bubbleEnds(BubbleKind.Problem, "Didn't hear anything", PROBLEM_MILLIS)
    }

    private fun endRecording() {
        recording.value = false
        handsFree.value = false
        speechEnd = null
        wakeRecording = false
        level.value = 0f
    }

    /** Send a recording (raw phone levels; made louder here): a spoken request, or the answer to her question. */
    fun sendRecording(raw: ByteArray) {
        endRecording()
        val pcm = Recorder.louder(raw)
        // Debug builds keep the last recording, to check the microphone's levels: adb exec-out run-as dev.natro cat cache/last.pcm
        if (BuildConfig.DEBUG) cacheDir?.let { File(it, "last.pcm").writeBytes(raw) }
        val id = newId()
        val message = JSONObject().put("type", "audio").put("id", id)
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
            bubbleEnds(BubbleKind.Problem, "Natro is offline", PROBLEM_MILLIS)
        }
    }

    /** "OK Natro" was heard. The listener records the request itself; this returns its recording's number. */
    fun wakeHeard(): Int {
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
    fun stillRecording(which: Int) = recording.value && recordings.get() == which

    /** Whether the owner asked to end the "OK Natro" recording now (the mic button). Asking clears it. */
    fun finishWakeAsked(): Boolean {
        if (!finishWake) return false
        finishWake = false
        return true
    }

    /** The loudness of the latest 100 ms of the owner's voice (raw phone levels). */
    fun hear(loudness: Double) {
        level.value = SpeechEnd.level(loudness)
    }

    fun answer(yes: Boolean) {
        val asked = question.value ?: return
        question.value = null
        asked.turn?.let { turn -> turns.update { Chat.answered(it, turn, if (yes) "Yes" else "No") } }
        bubbleShows(BubbleKind.Thinking, "Thinking…")
        send(JSONObject().put("type", "answer").put("id", asked.id).put("yes", yes))
    }

    /** Stop everything the phone asked for: the recording, and the requests she's working on (on the VPS too). */
    fun cancel() {
        speaker?.stop()
        if (recording.value) {
            recorder.stop()
            endRecording()  // the "OK Natro" listener drops its recording when it sees this
        }
        question.value = null
        val stopped = pending.toList()
        pending.clear()
        stopped.forEach { send(JSONObject().put("type", "cancel").put("id", it)) }
        stopped.lastOrNull()?.let {
            cancelling = it
            cancelledAt = System.currentTimeMillis()
        }
        turns.update { all -> stopped.fold(all) { list, id -> Chat.cancelled(list, id) } }
        bubbleTurn.incrementAndGet()
        bubble.value = null
    }

    /** The assistant gesture while Natro is off: the bubble says so for a moment. */
    fun flashOff() = bubbleEnds(BubbleKind.Off, "Natro is off", PROBLEM_MILLIS)

    fun clearHistory() {
        turns.update { all -> all.filter { it.working } }
    }

    fun beep() {
        val tone = ToneGenerator(AudioManager.STREAM_MUSIC, 70)
        tone.startTone(ToneGenerator.TONE_PROP_BEEP, 150)
        scope.launch {
            delay(400)
            tone.release()
        }
    }

    // The bubble

    private fun bubbleShows(kind: BubbleKind, label: String) {
        bubbleTurn.incrementAndGet()
        bubble.value = BubbleStatus(kind, label)
    }

    /** A last word ("Done", a problem), which the bubble lets go of after a moment. */
    private fun bubbleEnds(kind: BubbleKind, label: String, millis: Long) {
        bubbleShows(kind, label)
        val turn = bubbleTurn.get()
        scope.launch {
            delay(millis)
            if (bubbleTurn.get() == turn) bubble.value = null
        }
    }

    // Helpers

    /** Request ids are unique across restarts, as the chat outlives the app's process. */
    private fun newId() = "r${now().toString(36)}${sequence.incrementAndGet()}"

    private fun now() = System.currentTimeMillis()

    private fun JSONObject.number(key: String): Double? = if (has(key) && !isNull(key)) optDouble(key) else null

    /** The tools a reply says ran: {"tool", "args", "ok", "declined", "result"}. */
    private fun toolRuns(array: JSONArray?): List<ToolRun>? = array?.let { all ->
        List(all.length()) { i ->
            val run = all.getJSONObject(i)
            ToolRun(run.optString("tool"), run.opt("args")?.toString().orEmpty(), run.optBoolean("ok"),
                run.optBoolean("declined"), run.optString("result"))
        }
    }

    private const val DONE_MILLIS = 1_400L
    private const val PROBLEM_MILLIS = 2_200L
    private const val RETRY_FIRST_MILLIS = 2_000L
    private const val RETRY_MAX_MILLIS = 60_000L
    private const val SAVE_DELAY_MILLIS = 1_000L
    /** The longest to wait for the brain to say a cancelled request stopped. */
    private const val CANCEL_WAIT_MILLIS = 5_000L
    private const val TOOL_RESULT_CHARS = 600
}
