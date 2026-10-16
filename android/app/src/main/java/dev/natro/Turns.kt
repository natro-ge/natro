package dev.natro

/**
 * One exchange in the chat: what the owner asked and Natro's answer. Everything else she did for
 * it (what she heard, her steps, the tools that ran, her questions, the model and the cost) is kept
 * with it, folded away under the reply.
 *
 * id is the request's id, as the brain knows it. A request made on the PC that used the phone's
 * tools shows as a turn from the PC, with only those tools.
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
) {
    enum class From { Typed, Spoken, Pc }
    enum class State { Working, Done, Failed, Cancelled }

    val working get() = state == State.Working

    /** Whether there is anything folded under the reply. */
    val hasDetails
        get() = heard.isNotBlank() || steps.isNotEmpty() || tools.isNotEmpty() || questions.isNotEmpty() ||
            model.isNotBlank() || seconds != null
}

/** A tool that ran for a request: its name and arguments as the brain sent them, and how it went. */
data class ToolRun(val name: String, val args: String, val ok: Boolean, val declined: Boolean, val result: String)

/** A question Natro asked before something risky, and the owner's answer ("" while unanswered). */
data class Asked(val question: String, val details: String, val answer: String = "")

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

    /** A phone tool ran: for the phone's request id, or (null) for a request made on the PC. */
    fun toolRan(turns: List<Turn>, id: String?, run: ToolRun, time: Long): List<Turn> {
        if (id != null) return turns.change(id) { it.copy(tools = it.tools + run) }
        val last = turns.lastOrNull()
        if (last != null && last.from == Turn.From.Pc && time - last.time < PC_TOGETHER_MILLIS) {
            return turns.change(last.id) { it.copy(tools = it.tools + run) }
        }
        return (turns + Turn("pc$time", time, Turn.From.Pc, state = Turn.State.Done, tools = listOf(run))).takeLast(MAX_TURNS)
    }

    /** Turns still working when the app last stopped: they will never be answered now. */
    fun interrupted(turns: List<Turn>) =
        turns.map { if (it.working) it.copy(state = Turn.State.Failed, step = "", problem = "Interrupted.") else it }

    private fun List<Turn>.change(id: String, how: (Turn) -> Turn): List<Turn> {
        val at = indexOfLast { it.id == id }
        if (at < 0 || this[at].state == Turn.State.Cancelled) return this
        return toMutableList().also { it[at] = how(it[at]) }
    }
}
