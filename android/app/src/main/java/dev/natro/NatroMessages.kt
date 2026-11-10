package dev.natro

import dev.natro.Natro.Spend
import dev.natro.Natro.Status
import dev.natro.Natro.Question
import android.os.SystemClock
import android.util.Base64
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

// The messages from Natro's brain (server/natro_agent/server.py's), and the phone's tools it asks for.

internal fun Natro.handle(data: JSONObject) {
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
                listenAfter(data.number("listen"))
            }
        }
        // What he said after a reply had nothing in it she can use: no reply comes.
        "nothing_heard" -> {
            spent(data)
            turns.update { Chat.dropped(it, id) }
            if (pending.remove(id) && pending.isEmpty()) {
                keepMic.value = false
                bubbleTurn.incrementAndGet()
                bubble.value = null
            }
        }
        "confirm" -> {
            endListeningForMore()  // what it would hear now isn't an answer to this
            moreDue.incrementAndGet()
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
            if (pending.remove(id) && pending.isEmpty()) {
                keepMic.value = false
                bubbleEnds(BubbleKind.Problem, "Something went wrong", PROBLEM_MILLIS)
            }
        }
        "cancelled" -> if (cancelling == id) cancelling = null
        // The shared chat (server/natro_agent/chats.py).
        "chat_entry" -> data.optJSONObject("entry")?.let { entry -> turns.update { Chat.entry(it, entry) } }
        "chat_all" -> turns.update { Chat.all(it, data.optJSONArray("entries")) }
        "chat_picture" -> picture = data
        "card" -> cardComing = data
        "app_update" -> app?.let { AppUpdates.came(it, data) }
        "app_current" -> AppUpdates.current()
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
            app?.let { AlertNotices.show(it, id, text, data.optBoolean("urgent"), data.optJSONArray("answers")) }
            if (data.optBoolean("speak") && Prefs.speakReplies.value) speaker?.say(text)
        }
        "alert_answered" -> app?.let { AlertNotices.answered(it, id, data.optString("text")) }
        // What to watch for in the phone's notifications (NotificationReader matches them).
        "works_on" -> app?.let { context -> WorksOn.named(data.optString("device"))?.let { WorksOn.taken(context, it) } }
        "online" -> pcOnline.value = data.optJSONArray("devices")?.let { devices ->
            (0 until devices.length()).any { devices.optString(it) == WorksOn.PC }
        } ?: false
        "watch_rules" -> NotificationWatch.rules = NotificationWatch.rulesFrom(data.optJSONArray("rules"))
        "place_rules" -> Places.setRules(data.optJSONArray("places"))
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

internal fun Natro.gotPicture(id: String, bytes: ByteArray) {
    pictures.update { all -> (all - id + (id to bytes)).entries.toList().takeLast(MAX_PICTURES).associate { it.toPair() } }
}

internal fun Natro.keptCame(items: JSONArray?) {
    val came = List(items?.length() ?: 0) { i ->
        val item = items!!.getJSONObject(i)
        val from = item.optString("from")
        Kept(item.optString("id"), item.optString("text"), if (from == "pc") "PC" else from, item.optLong("time"))
    }.filter { it.id.isNotBlank() }
    kept.value = came
    app?.let { context -> came.forEach { KeptNotices.show(context, it) } }
}

/** request: the shared chat's turn it's for, for one made on the PC. */
internal suspend fun Natro.runTool(id: String, name: String, args: JSONObject, request: String) {
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

internal fun Natro.spent(data: JSONObject) {
    val spent = data.number("spent") ?: return
    spend.value = Spend(spent, data.number("budget") ?: 0.0)
}

private fun JSONObject.number(key: String): Double? = if (has(key) && !isNull(key)) optDouble(key) else null

/** The tools a reply says ran: {"tool", "args", "ok", "declined", "result"}. */
internal fun Natro.toolRuns(array: JSONArray?): List<ToolRun>? = array?.let { all ->
    List(all.length()) { i ->
        val run = all.getJSONObject(i)
        ToolRun(run.optString("tool"), run.opt("args")?.toString().orEmpty(), run.optBoolean("ok"),
            run.optBoolean("declined"), run.optString("result"))
    }
}
