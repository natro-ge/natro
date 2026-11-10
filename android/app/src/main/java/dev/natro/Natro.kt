package dev.natro

import android.app.KeyguardManager
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
 *
 * Here: its state, and what the owner does. The connection is NatroConnection.kt's, the messages from the brain
 * NatroMessages.kt's, and recording him NatroRecording.kt's.
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
    /** Whether the PC is online, as the brain last said (false while the phone isn't connected): WorksOn. */
    val pcOnline = MutableStateFlow(false)
    /** Whether a request is being recorded: the mic button, the assistant gesture or "OK Natro". */
    val recording = MutableStateFlow(false)
    /** Whether the recording ends by itself when the owner stops talking, rather than when he lets go of the button. */
    val handsFree = MutableStateFlow(false)
    /** The recording is listening for him to go on after a reply: if he says nothing it ends quietly, with nothing sent. */
    val listeningForMore = MutableStateFlow(false)
    /** The service keeps the microphone between recordings: Android wouldn't hand it back in the background. */
    val keepMic = MutableStateFlow(false)
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

    internal val client = OkHttpClient.Builder().pingInterval(30, TimeUnit.SECONDS).build()
    internal val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val sequence = AtomicLong()
    internal val recorder = Recorder()
    internal var app: Context? = null
    internal var tools: PhoneTools? = null
    internal var cacheDir: File? = null
    /** A chat_picture's message, until its bytes come next. */
    @Volatile internal var picture: JSONObject? = null
    @Volatile internal var cardComing: JSONObject? = null
    @Volatile internal var speaker: Speaker? = null
    @Volatile internal var socket: WebSocket? = null
    @Volatile internal var running = false
    @Volatile internal var refused = false
    internal var retryMillis = RETRY_FIRST_MILLIS

    /** The phone's requests the brain is still working on, oldest first. Tool calls and questions are for the first. */
    internal val pending = CopyOnWriteArrayList<String>()
    /** A spoken answer's request id -> the request whose question it answers. */
    internal val answers = ConcurrentHashMap<String, String>()
    /** A cancelled request, until the brain says it stopped: tool calls until then were still for it, and are refused. */
    @Volatile internal var cancelling: String? = null
    @Volatile internal var cancelledAt = 0L
    /** Decides when a hands-free recording is over; null while the button is held. */
    @Volatile internal var speechEnd: SpeechEnd? = null
    /** Counts recordings, so the "OK Natro" listener can tell that its own was cancelled. */
    internal val recordings = AtomicInteger()
    @Volatile internal var wakeRecording = false
    @Volatile internal var finishWake = false
    internal val bubbleTurn = AtomicInteger()
    /** Moves on with each reply it may listen after, and when he stops or starts something: what was waiting stops. */
    internal val moreDue = AtomicInteger()

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

    // What the owner does

    /** A typed request. files: on this phone, attached for Natro; the chat shows their names after his words, as the
     * brain's does (server/natro_agent/server.py). */
    fun sendText(text: String, files: List<File> = emptyList()) {
        val id = newId()
        val shown = if (files.isEmpty()) text else "$text (${files.joinToString { it.name }})"
        turns.update { Chat.asked(it, id, Turn.From.Typed, shown, now()) }
        val message = JSONObject().put("type", "text").put("id", id).put("text", text)
        if (files.isNotEmpty()) message.put("files", JSONArray(files.map { it.path }))
        request(id, message)
    }

    internal fun request(id: String, message: JSONObject, recording: ByteArray? = null) {
        // Anyone can say "OK Natro" to a phone on a table: what only he should see is kept for when it's unlocked.
        if (app?.getSystemService(KeyguardManager::class.java)?.isKeyguardLocked == true) message.put("locked", true)
        if (!send(message)) {
            turns.update { Chat.failed(it, id, "Not sent: Natro is offline.") }
            keepMic.value = false
            bubbleEnds(BubbleKind.Problem, "Natro is offline", PROBLEM_MILLIS)
            return
        }
        recording?.let { socket?.send(it.toByteString()) }
        pending += id
        bubbleShows(BubbleKind.Thinking, "Thinking…")
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

    /** Nothing's under way, so the app can restart for an update. */
    fun idle() = pending.isEmpty() && !recording.value

    /** His answer to an alert ("On my way"): the brain answers with alert_answered. */
    fun answerAlert(context: Context, id: String, answer: String) {
        if (!send(JSONObject().put("type", "alert_answer").put("id", id).put("answer", answer))) {
            AlertNotices.answered(context, id, "Natro is offline, so it can't be told.")
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
    internal fun afterAnswer(yes: Boolean) {
        if (pending.isEmpty()) bubbleEnds(BubbleKind.Done, if (yes) "Going ahead" else "Not doing it", DONE_MILLIS)
        else bubbleShows(BubbleKind.Thinking, "Thinking…")
    }

    /** Stop everything the phone asked for: the recording, and the requests she's working on (on the VPS too). */
    fun cancel() {
        moreDue.incrementAndGet()
        keepMic.value = false
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

    internal fun bubbleShows(kind: BubbleKind, label: String) {
        bubbleTurn.incrementAndGet()
        bubble.value = BubbleStatus(kind, label)
    }

    /** A last word ("Done", a problem), which the bubble lets go of after a moment. */
    internal fun bubbleEnds(kind: BubbleKind, label: String, millis: Long) {
        bubbleShows(kind, label)
        val turn = bubbleTurn.get()
        scope.launch {
            delay(millis)
            if (bubbleTurn.get() == turn) bubble.value = null
        }
    }

    // Helpers

    /** Request ids are unique across restarts, as the chat outlives the app's process. */
    internal fun newId() = "r${now().toString(36)}${sequence.incrementAndGet()}"

    internal fun now() = System.currentTimeMillis()

    internal const val DONE_MILLIS = 1_400L
    /** How long "Done" shows before it listens for him to go on. */
    internal const val DONE_BEFORE_LISTENING_MILLIS = 700L
    internal const val BLOCKS_A_SECOND = 10
    internal const val PROBLEM_MILLIS = 2_200L
    internal const val RETRY_FIRST_MILLIS = 2_000L
    internal const val RETRY_MAX_MILLIS = 60_000L
    /** The pictures kept (the brain keeps as many: older ones are gone from the chat). */
    internal const val MAX_PICTURES = 24
    /** The longest to wait for the brain to say a cancelled request stopped. */
    internal const val CANCEL_WAIT_MILLIS = 5_000L
    internal const val TOOL_RESULT_CHARS = 600
}
