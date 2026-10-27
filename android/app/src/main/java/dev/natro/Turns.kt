package dev.natro

import org.json.JSONArray
import org.json.JSONObject

/**
 * One exchange in the chat: what the owner asked and Natro's answer. Everything else she did for
 * it (what she heard, her steps, the tools that ran, her questions, the model and the cost) is kept
 * with it, folded away under the reply.
 *
 * id is the request's id, as the brain knows it. The chat is shared with the PC (server/natro_agent/chats.py):
 * a request made there is a turn From.Other, as the brain sends it, its id the brain's ("pc:r3"). (From.Pc:
 * a request made on the PC that used the phone's tools, from a brain that didn't share the chat.)
 */
data class Turn(
    val id: String,
    val time: Long,
    val from: From,
    /** The request in English: typed, or as she understood the spoken Georgian. */
    val asked: String = "",
    /** The Georgian she heard, for a spoken request. */
    val heard: String = "",
    val reply: String = "",
    val state: State = State.Working,
    /** What she's doing right now ("Opening Spotify"), while working. */
    val step: String = "",
    val steps: List<String> = emptyList(),
    val tools: List<ToolRun> = emptyList(),
    val questions: List<Asked> = emptyList(),
    /** Why it failed. */
    val problem: String = "",
    val model: String = "",
    val cost: Double? = null,
    val seconds: Double? = null,
    /** For a request made on the other device: its name ("PC"); empty for the phone's own. */
    val device: String = "",
    /** Its pictures, by id (Natro.pictures has them): a screenshot, or a picture he sent. */
    val pictures: List<String> = emptyList(),
    /** Asked on that device ("PC") while the phone was offline, and run on the phone after. */
    val queued: String = "",
    /** A picture he sent, not a request. */
    val sent: Boolean = false,
    /** A reminder that went off: her words alone, with no request. */
    val reminder: Boolean = false,
    /** A background job's task (server/natro_agent/jobs.py): her work on it as it goes, then what she found. */
    val job: String = "",
    /** A routine's name (server/natro_agent/routines.py), on the job that is its run. */
    val routine: String = "",
    /** Something a watcher saw (server/natro_agent/watchers.py): her words alone, as a reminder's. */
    val alert: Boolean = false,
) {
    enum class From { Typed, Spoken, Pc, Other }
    enum class State { Working, Done, Failed, Cancelled }

    val working get() = state == State.Working

    /** Whether there is anything folded under the reply. */
    val hasDetails
        get() = heard.isNotBlank() || steps.isNotEmpty() || tools.isNotEmpty() || questions.isNotEmpty() ||
            model.isNotBlank() || seconds != null
}

/** A tool that ran for a request: its name and arguments as the brain sent them, and how it went. */
data class ToolRun(val name: String, val args: String, val ok: Boolean, val declined: Boolean, val result: String)

/** A question Natro asked before something risky, and the owner's answer ("" while unanswered). For one about the
 * other device's request: the brain's id for it, and that device ("pc"), to answer it from here. */
data class Asked(val question: String, val details: String, val answer: String = "", val id: String = "",
                 val device: String = "")

/** How the chat changes as things happen. Each returns the new list of turns; a cancelled turn never changes again. */
object Chat {
    const val MAX_TURNS = 200
    /** The PC's uses of the phone this close together show as one turn. */
    private const val PC_TOGETHER_MILLIS = 120_000L

    fun asked(turns: List<Turn>, id: String, from: Turn.From, text: String, time: Long) =
        (turns + Turn(id, time, from, asked = text)).takeLast(MAX_TURNS)

    /** A piece of the translation of a spoken request, as it's written. */
    fun english(turns: List<Turn>, id: String, piece: String) = turns.change(id) { it.copy(asked = it.asked + piece) }

    fun heard(turns: List<Turn>, id: String, georgian: String, english: String) =
        turns.change(id) { it.copy(heard = georgian, asked = english.ifBlank { it.asked }) }

    fun step(turns: List<Turn>, id: String, text: String) = turns.change(id) {
        if (it.steps.lastOrNull() == text) it.copy(step = text) else it.copy(step = text, steps = it.steps + text)
    }

    fun replyText(turns: List<Turn>, id: String, piece: String) = turns.change(id) { it.copy(reply = it.reply + piece) }

    /** The whole reply. tools, when the brain sent them, is every tool that ran (its own too), so it replaces the phone's list. */
    fun replied(turns: List<Turn>, id: String, text: String, model: String, cost: Double?, seconds: Double?,
                tools: List<ToolRun>?) = turns.change(id) {
        it.copy(reply = text.ifBlank { it.reply }, state = Turn.State.Done, step = "", model = model, cost = cost,
            seconds = seconds, tools = tools ?: it.tools)
    }

    fun failed(turns: List<Turn>, id: String, problem: String) = turns.change(id) {
        if (it.working) it.copy(state = Turn.State.Failed, step = "", problem = problem) else it
    }

    fun cancelled(turns: List<Turn>, id: String) = turns.change(id) {
        if (it.working) it.copy(state = Turn.State.Cancelled, step = "") else it
    }

    fun asking(turns: List<Turn>, id: String, question: String, details: String) =
        turns.change(id) { it.copy(questions = it.questions + Asked(question, details)) }

    /** The answer to the turn's open question: "Yes", "No", or the words he said. */
    fun answered(turns: List<Turn>, id: String, answer: String) = turns.change(id) { turn ->
        val open = turn.questions.indexOfLast { it.answer.isEmpty() }
        if (open < 0) turn
        else turn.copy(questions = turn.questions.toMutableList().also { it[open] = it[open].copy(answer = answer) })
    }

    /** A phone tool ran: for the phone's request id, or (null) for a request made on the PC (request: its turn). */
    fun toolRan(turns: List<Turn>, id: String?, run: ToolRun, time: Long, request: String? = null): List<Turn> {
        if (id != null) return turns.change(id) { it.copy(tools = it.tools + run) }
        if (request != null && turns.any { it.id == request }) return turns.change(request) { it.copy(tools = it.tools + run) }
        val last = turns.lastOrNull()
        if (last != null && last.from == Turn.From.Pc && time - last.time < PC_TOGETHER_MILLIS) {
            return turns.change(last.id) { it.copy(tools = it.tools + run) }
        }
        return (turns + Turn("pc$time", time, Turn.From.Pc, state = Turn.State.Done, tools = listOf(run))).takeLast(MAX_TURNS)
    }

    /**
     * An entry of the shared chat, as the brain sends it. The phone's own requests show here as they happen, so from
     * those only the pictures are taken; the PC's (and the phone's from before the app started) show as the brain has
     * them, with the steps and phone tools seen here kept.
     */
    fun entry(turns: List<Turn>, entry: JSONObject): List<Turn> {
        val pictures = strings(entry.optJSONArray("pictures"))
        val ours = entry.text("device") == "phone"
        val id = entry.text(if (ours) "request" else "id")
        if (ours && turns.any { it.id == id }) return turns.change(id) { it.copy(pictures = pictures) }
        val problem = entry.text("problem")
        val done = entry.optBoolean("done")
        val question = entry.optJSONObject("question")
        val step = entry.text("step")
        val shared = Turn(
            id = id,
            time = entry.optLong("time"),
            from = when {
                !ours -> Turn.From.Other
                entry.optBoolean("spoken") -> Turn.From.Spoken
                else -> Turn.From.Typed
            },
            asked = entry.text("asked"),
            heard = entry.text("heard"),
            reply = entry.text("reply"),
            state = when {
                !done -> Turn.State.Working
                problem == "Cancelled" -> Turn.State.Cancelled
                problem.isNotBlank() -> Turn.State.Failed
                else -> Turn.State.Done
            },
            step = step,
            questions = listOfNotNull(question?.let {
                Asked(it.text("text"), "", it.text("answer"), it.text("id"), if (ours) "" else entry.text("device"))
            }),
            problem = problem,
            device = if (ours) "" else entry.text("label"),
            pictures = pictures,
            queued = entry.text("queued"),
            sent = entry.optBoolean("sent"),
            reminder = entry.optBoolean("reminder"),
            job = entry.text("job"),
            routine = entry.text("routine"),
            alert = entry.optBoolean("alert"),
        )
        val at = turns.indexOfLast { it.id == id }
        if (at < 0) return (turns + shared.copy(steps = listOfNotNull(step.ifBlank { null }))).takeLast(MAX_TURNS)
        val old = turns[at]
        val steps = if (step.isBlank() || old.steps.lastOrNull() == step) old.steps else old.steps + step
        return turns.toMutableList().also { it[at] = shared.copy(steps = steps, tools = old.tools) }
    }

    /** The whole shared chat, after connecting: the PC's turns anew, the phone's merged. */
    fun all(turns: List<Turn>, entries: JSONArray?): List<Turn> {
        var all = turns.filter { it.from != Turn.From.Other }
        for (i in 0 until (entries?.length() ?: 0)) all = entry(all, entries!!.getJSONObject(i))
        return all
    }

    /** A request kept for the phone while it was offline, asked on that device ("PC"). */
    fun queued(turns: List<Turn>, id: String, from: String) = turns.change(id) { it.copy(queued = from) }

    /** A new chat (on either device): only the phone's requests still on their way stay. */
    fun cleared(turns: List<Turn>) = turns.filter { it.working && it.from != Turn.From.Other }

    /** The answer, given here, to a question about the other device's request. */
    fun answeredFor(turns: List<Turn>, device: String, question: String, answer: String) = turns.map { turn ->
        if (turn.questions.none { it.id == question && it.device == device }) turn
        else turn.copy(questions = turn.questions.map {
            if (it.id == question && it.device == device && it.answer.isEmpty()) it.copy(answer = answer) else it
        })
    }

    /** Turns still working when the app last stopped: they will never be answered now. */
    fun interrupted(turns: List<Turn>) =
        turns.map { if (it.working) it.copy(state = Turn.State.Failed, step = "", problem = "Interrupted.") else it }

    /** A string, with JSON's null as empty (optString gives "null"). */
    private fun JSONObject.text(key: String) = if (isNull(key)) "" else optString(key)

    private fun strings(array: JSONArray?) = List(array?.length() ?: 0) { array!!.optString(it) }

    private fun List<Turn>.change(id: String, how: (Turn) -> Turn): List<Turn> {
        val at = indexOfLast { it.id == id }
        if (at < 0 || this[at].state == Turn.State.Cancelled) return this
        return toMutableList().also { it[at] = how(it[at]) }
    }
}
