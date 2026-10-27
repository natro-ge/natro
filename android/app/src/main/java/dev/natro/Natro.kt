package dev.natro

import android.app.role.RoleManager
import android.content.Context
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.SystemClock
import android.util.Base64
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
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
 * server/natro_agent/server.py; files between the phone and the PC are Transfers'.
 *
 * The chat is shared with the PC and lives on the brain, in memory (server/natro_agent/chats.py):
 * nothing of it is kept on the phone. Its pictures (screenshots, ones he sends) come with it. A new
 * chat here clears the PC's too. Requests he made on the PC while the phone was offline come when it
 * connects (Kept), to run or skip. Reminders that go off are notifications (ReminderNotices), spoken here when
 * the brain picks the phone to say them; so is what a background job found (JobNotices), and what a watcher
 * saw (AlertNotices). New notifications that fit what he asked her to watch for go to the brain
 * (NotificationWatch); no others do. Her questions on the PC are asked here too (QuestionNotices).
 *
 * One for the whole app. NatroService starts and stops the connection, so it stays open with the
 * screen off and the brain can use the phone's tools even for a request made on the PC.
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
    /** The chat's pictures, by id (JPEG or PNG bytes), oldest first. */
    val pictures = MutableStateFlow<Map<String, ByteArray>>(emptyMap())
    /** Requests he made on the PC while the phone was offline, still to run or skip. */
    val kept = MutableStateFlow<List<Kept>>(emptyList())

    /** Whether Natro is speaking a reply. */
    val speaking get() = speaker?.speaking == true

    /** Whether Android lets Natro open apps and other screens now: on screen, as the assistant, with screen control, or with the bubble up. */
    fun canOpenScreens(context: Context) = onScreen.value || ScreenControl.current != null || Bubble.showing ||
        context.getSystemService(RoleManager::class.java).isRoleHeld(RoleManager.ROLE_ASSISTANT)

    private val client = OkHttpClient.Builder().pingInterval(30, TimeUnit.SECONDS).build()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val sequence = AtomicLong()
    private val recorder = Recorder()
    private var app: Context? = null
    private var tools: PhoneTools? = null
    private var cacheDir: File? = null
    /** A chat_picture's message, until its bytes come next. */
    @Volatile private var picture: JSONObject? = null
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
        if (app != null) return
        val app = context.applicationContext
        this.app = app
        cacheDir = app.cacheDir
        tools = PhoneTools(app)
        File(app.filesDir, "chat.json").delete()  // the chat the phone used to keep: it's the brain's now
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
                .put("token", BuildConfig.NATRO_TOKEN).put("tools", tools?.specs()).put("files", true)
                .put("chat", true).put("queue", true).put("reminders", true).put("jobs", true).put("alerts", true)
                .put("watch_notifications", true).put("questions", true).put("say", true).toString())
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (webSocket === socket) handle(JSONObject(text))
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            if (webSocket !== socket) return
            val shown = picture
            if (shown != null) {
                picture = null
                gotPicture(shown.optString("id"), bytes.toByteArray())
            } else {
                Transfers.piece(bytes)
            }
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
        app?.let { QuestionNotices.cancelAll(it) }
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
                    // One he asked to have said out loud was ("say", below).
                    if (Prefs.speakReplies.value && !data.optBoolean("said")) {
                        // How long its first sound took, for the dashboard.
                        val came = SystemClock.elapsedRealtime()
                        speaker?.say(text) {
                            send(JSONObject().put("type", "spoken").put("id", id)
                                .put("seconds", (SystemClock.elapsedRealtime() - came) / 1000.0))
                        }
                    }
                    if (pending.isEmpty()) bubbleEnds(BubbleKind.Done, "Done", DONE_MILLIS)
                }
            }
            "confirm" -> {
                val text = data.optString("question")
                val details = data.optString("details")
                // A background job's question isn't about one of the phone's requests: its own entry shows it.
                val turn = if (data.optString("request").startsWith("natro:")) null else pending.firstOrNull()
                question.value = Question(id, turn, text, details)
                turn?.let { asking -> turns.update { Chat.asking(it, asking, text, details) } }
                bubbleShows(BubbleKind.Asking, Text.short(text, 90))
                if (Prefs.speakReplies.value) speaker?.say(text)
            }
            // Something he asked to have said out loud (server/natro_agent/aloud.py): said even with Speak replies
            // off, here or asked for on the PC.
            "say" -> speaker?.say(data.optString("text"))
            "tool_call" -> scope.launch {
                runTool(id, data.optString("name"), data.optJSONObject("args") ?: JSONObject(),
                    if (data.isNull("request")) "" else data.optString("request"))
            }
            "error" -> {
                turns.update { Chat.failed(it, id, data.optString("message")) }
                if (pending.remove(id) && pending.isEmpty()) bubbleEnds(BubbleKind.Problem, "Something went wrong", PROBLEM_MILLIS)
            }
            "cancelled" -> if (cancelling == id) cancelling = null
            // The shared chat (server/natro_agent/chats.py).
            "chat_entry" -> data.optJSONObject("entry")?.let { entry -> turns.update { Chat.entry(it, entry) } }
            "chat_all" -> turns.update { Chat.all(it, data.optJSONArray("entries")) }
            "chat_picture" -> picture = data
            "chat_cleared" -> {
                turns.update { Chat.cleared(it) }
                pictures.value = emptyMap()
            }
            // The phone's question, answered on the PC.
            "answered" -> {
                val asked = question.value
                if (asked?.id == id) {
                    question.value = null
                    val answer = if (data.optBoolean("yes")) "Yes" else "No"
                    asked.turn?.let { turn -> turns.update { Chat.answered(it, turn, answer) } }
                    afterAnswer(data.optBoolean("yes"))
                }
            }
            // Natro asks on the PC: a notification asks here too, until the question is over (answered anywhere).
            "question_elsewhere" -> app?.let {
                QuestionNotices.show(it, data.optString("device"), id, data.optString("label").ifBlank { "PC" },
                    data.optString("question"))
            }
            "question_done" -> app?.let { QuestionNotices.cancel(it, data.optString("device"), id) }
            "queued" -> keptCame(data.optJSONArray("items"))
            // A reminder going off: always a notification; spoken only if the brain picked the phone to say it.
            "reminder" -> {
                val text = data.optString("text")
                app?.let { ReminderNotices.show(it, id, text) }
                if (data.optBoolean("speak") && Prefs.speakReplies.value) speaker?.say(text)
            }
            "reminder_snoozed" -> app?.let { ReminderNotices.snoozed(it, id, data.optString("text")) }
            // Something a watcher saw: a notification; spoken only if it's urgent and the brain picked the phone.
            "alert" -> {
                val text = data.optString("text")
                app?.let { AlertNotices.show(it, id, text, data.optBoolean("urgent")) }
                if (data.optBoolean("speak") && Prefs.speakReplies.value) speaker?.say(text)
            }
            // What to watch for in the phone's notifications (NotificationReader matches them).
            "watch_rules" -> NotificationWatch.rules = NotificationWatch.rulesFrom(data.optJSONArray("rules"))
            // A background job is done: a notification, as a reminder is; spoken only if the brain picked the phone.
            "job_done" -> {
                val text = data.optString("text")
                app?.let { JobNotices.show(it, id, text, data.optBoolean("ok", true), data.optString("title")) }
                // Its first paragraph is said: the answer. The rest is for reading.
                if (data.optBoolean("speak") && Prefs.speakReplies.value) speaker?.say(data.optString("say").ifBlank { text })
            }
            else -> if (data.optString("type").startsWith("file_")) Transfers.handle(data)
        }
    }

    private fun gotPicture(id: String, bytes: ByteArray) {
        pictures.update { all -> (all - id + (id to bytes)).entries.toList().takeLast(MAX_PICTURES).associate { it.toPair() } }
    }

    private fun keptCame(items: JSONArray?) {
        val came = List(items?.length() ?: 0) { i ->
            val item = items!!.getJSONObject(i)
            val from = item.optString("from")
            Kept(item.optString("id"), item.optString("text"), if (from == "pc") "PC" else from, item.optLong("time"))
        }.filter { it.id.isNotBlank() }
        kept.value = came
        app?.let { context -> came.forEach { KeptNotices.show(context, it) } }
    }

    /** request: the shared chat's turn it's for, for one made on the PC. */
    private suspend fun runTool(id: String, name: String, args: JSONObject, request: String) {
        val phone = tools ?: return
        val cancelled = cancelling != null && System.currentTimeMillis() - cancelledAt < CANCEL_WAIT_MILLIS
        val (ok, output) = if (cancelled) false to "The owner cancelled this request." else try {
            true to withContext(Dispatchers.IO) { phone.run(name, args) }
        } catch (e: ToolFailure) {
            false to (e.message ?: "$name failed.")
        } catch (e: Exception) {
            false to "$name failed on the phone: ${e.javaClass.simpleName}: ${e.message}"
        }
        val message = JSONObject().put("type", "tool_result").put("id", id).put("ok", ok)
        val result = if (output is ToolPicture) {
            message.put("picture", JSONObject().put("mime", output.mime).put("name", output.name)
                .put("data", Base64.encodeToString(output.data, Base64.NO_WRAP)))
            output.text
        } else output.toString()
        send(message.put("result", result))
        if (cancelled) return
        val shown = if (result.length > TOOL_RESULT_CHARS) result.take(TOOL_RESULT_CHARS - 1) + "…" else result
        val run = ToolRun("phone_$name", args.toString(), ok, false, shown)
        turns.update { Chat.toolRan(it, pending.firstOrNull(), run, now(), request.ifBlank { null }) }
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

    /** A question about the PC's request, answered here. */
    fun answerFor(asked: Asked, yes: Boolean) {
        val answer = if (yes) "Yes" else "No"
        if (!send(JSONObject().put("type", "answer").put("id", asked.id).put("yes", yes).put("device", asked.device))) {
            notice.value = "Not sent: Natro is offline."
            return
        }
        turns.update { Chat.answeredFor(it, asked.device, asked.id, answer) }
    }

    /** The notification's Yes or No to her question on the PC. */
    fun answerElsewhere(context: Context, device: String, id: String, yes: Boolean) {
        QuestionNotices.cancel(context, device, id)
        if (!send(JSONObject().put("type", "answer").put("id", id).put("yes", yes).put("device", device))) {
            QuestionNotices.offline(context, device, id)
            return
        }
        turns.update { Chat.answeredFor(it, device, id, if (yes) "Yes" else "No") }
    }

    /** A picture he picked, into the shared chat (it shows once the brain has it). */
    fun sendPicture(bytes: ByteArray, mime: String, name: String) {
        val id = "p${newId()}"
        val sent = send(JSONObject().put("type", "picture").put("id", id).put("mime", mime).put("name", name)) &&
            (socket?.send(bytes.toByteString()) ?: false)
        if (!sent) notice.value = "Not sent: Natro is offline."
    }

    /** A notification fitted one of the rules he asked Natro to watch for: to the brain (lost if it's offline). */
    fun notificationMatched(rule: String, app: String, title: String, text: String) {
        send(JSONObject().put("type", "notification_matched").put("rule", rule).put("app", app)
            .put("title", Text.short(title, 200)).put("text", Text.short(text, 600)))
    }

    /** A message came on the phone: for the PC to show (server/natro_agent/server.py; lost if Natro is offline). */
    fun phoneMessage(key: String, app: String, from: String, text: String, reply: Boolean) {
        send(JSONObject().put("type", "phone_message").put("key", key).put("app", app).put("title", from)
            .put("text", text).put("reply", reply))
    }

    /** A chat on Instagram or Messenger wrote: for the brain's address book (server/natro_agent/people.py; never what
     * it says). False if Natro is offline. */
    fun person(app: String, name: String, chat: String, facebook: String, group: Boolean): Boolean =
        send(JSONObject().put("type", "person").put("app", app).put("name", name).put("chat", chat)
            .put("facebook", facebook).put("group", group))

    /** That message's notification went (read, answered or dismissed): the PC lets go of it. */
    fun phoneMessageGone(key: String) {
        send(JSONObject().put("type", "phone_message_gone").put("key", key))
    }

    /** A reminder's Snooze: it comes back in 10 minutes (the brain answers with reminder_snoozed). */
    fun snooze(context: Context, id: String) {
        if (!send(JSONObject().put("type", "reminder_snooze").put("id", id).put("minutes", 10))) {
            ReminderNotices.snoozed(context, id, "Natro is offline, so it can't be snoozed.")
        }
    }

    /** Run (or skip) a request he made on the PC while the phone was offline: run, it becomes the phone's request. */
    fun runKept(id: String, run: Boolean) {
        val item = kept.value.firstOrNull { it.id == id } ?: return
        app?.let { KeptNotices.cancel(it, id) }
        if (!send(JSONObject().put("type", if (run) "queue_run" else "queue_skip").put("id", id))) {
            notice.value = "Not sent: Natro is offline."
            return
        }
        kept.update { all -> all.filter { it.id != id } }
        if (!run) return
        turns.update { Chat.asked(it, id, Turn.From.Typed, item.text, now()).let { all -> Chat.queued(all, id, item.from) } }
        pending += id
        bubbleShows(BubbleKind.Thinking, "Thinking…")
    }

    fun answer(yes: Boolean) {
        val asked = question.value ?: return
        question.value = null
        asked.turn?.let { turn -> turns.update { Chat.answered(it, turn, if (yes) "Yes" else "No") } }
        afterAnswer(yes)
        send(JSONObject().put("type", "answer").put("id", asked.id).put("yes", yes))
    }

    /** After his answer: she goes on with the phone's request; a background job's goes on out of sight. */
    private fun afterAnswer(yes: Boolean) {
        if (pending.isEmpty()) bubbleEnds(BubbleKind.Done, if (yes) "Going ahead" else "Not doing it", DONE_MILLIS)
        else bubbleShows(BubbleKind.Thinking, "Thinking…")
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

    /** A new chat: here and on the PC, and Natro starts a new conversation. */
    fun newChat() {
        send(JSONObject().put("type", "chat_clear"))
        turns.update { Chat.cleared(it) }
        pictures.value = emptyMap()
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
    /** The pictures kept (the brain keeps as many: older ones are gone from the chat). */
    private const val MAX_PICTURES = 24
    /** The longest to wait for the brain to say a cancelled request stopped. */
    private const val CANCEL_WAIT_MILLIS = 5_000L
    private const val TOOL_RESULT_CHARS = 600
}
