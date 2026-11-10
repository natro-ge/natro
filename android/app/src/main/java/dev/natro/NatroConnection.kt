package dev.natro

import dev.natro.Natro.Status
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.json.JSONObject

// Natro's connection to her brain on the VPS (over Tailscale): it says hello with the phone's tools and what it takes,
// and keeps connecting, more slowly each time, while Natro is on. What comes over it is NatroMessages.kt's.

internal fun Natro.connect() {
    status.value = Status.Connecting
    socket = client.newWebSocket(Request.Builder().url(BuildConfig.NATRO_SERVER).build(), Listener())
}

internal fun Natro.lost(webSocket: WebSocket, reason: String) {
    if (webSocket !== socket) return
    socket = null
    dropAll("Lost the connection to Natro.")
    Transfers.lost()
    pcOnline.value = false
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
fun Natro.reconnect() {
    if (!running || status.value == Status.Online) return
    refused = false
    retryMillis = RETRY_FIRST_MILLIS
    val old = socket
    socket = null
    old?.cancel()
    connect()
}

internal fun Natro.send(message: JSONObject): Boolean = socket?.send(message.toString()) ?: false

/** For Transfers: a message, and a piece of a file right after its file_part. False when offline. */
fun Natro.sendMessage(message: JSONObject): Boolean = send(message)

fun Natro.sendBytes(bytes: ByteString): Boolean = socket?.send(bytes) ?: false

/** The brain won't answer these any more: the connection went, or Natro was turned off. */
internal fun Natro.dropAll(problem: String) {
    val dropped = pending.toList()
    pending.clear()
    question.value = null
    moreDue.incrementAndGet()
    keepMic.value = false
    app?.let { QuestionNotices.cancelAll(it) }
    if (dropped.isEmpty()) return
    turns.update { all -> dropped.fold(all) { list, id -> Chat.failed(list, id, problem) } }
    if (running) bubbleEnds(BubbleKind.Problem, "Natro is offline", PROBLEM_MILLIS)
}

/** The connection's events, from OkHttp's thread. */
internal class Listener : WebSocketListener() {
    override fun onOpen(webSocket: WebSocket, response: Response) {
        webSocket.send(JSONObject().put("type", "hello").put("device", "phone").put("label", "phone")
            .put("token", BuildConfig.NATRO_TOKEN).put("tools", Natro.tools?.specs()).put("files", true)
            .put("chat", true).put("queue", true).put("reminders", true).put("jobs", true).put("alerts", true)
            .put("watch_notifications", true).put("questions", true).put("say", true).put("places", true).put("cards", true).put("version", BuildConfig.VERSION_CODE)
            .put("works_on", Prefs.worksOn.value).toString())
    }

    override fun onMessage(webSocket: WebSocket, text: String) {
        if (webSocket === Natro.socket) Natro.handle(JSONObject(text))
    }

    override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
        if (webSocket !== Natro.socket) return
        val card = Natro.cardComing
        if (card != null) {
            Natro.cardComing = null
            Natro.app?.let { Cards.came(it, card, bytes.toByteArray()) }
            return
        }
        val shown = Natro.picture
        if (shown != null) {
            Natro.picture = null
            Natro.gotPicture(shown.optString("id"), bytes.toByteArray())
        } else {
            Transfers.piece(bytes)
        }
    }

    override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
        if (code == 1008) Natro.refused = true  // a wrong device token
        webSocket.close(1000, null)
    }

    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = Natro.lost(webSocket, "closed")

    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) =
        Natro.lost(webSocket, t.message ?: t.javaClass.simpleName)
}
