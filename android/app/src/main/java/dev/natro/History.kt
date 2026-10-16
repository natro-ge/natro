package dev.natro

import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** The chat kept on the phone between restarts: the latest turns, as JSON in the app's own files. */
class History(private val file: File) {
    fun load(): List<Turn> = try {
        if (!file.exists()) emptyList() else JSONArray(file.readText()).let { all -> List(all.length()) { turn(all.getJSONObject(it)) } }
    } catch (e: Exception) {
        emptyList()  // unreadable: start a new chat rather than not start
    }

    fun save(turns: List<Turn>) {
        val partial = File(file.path + ".part")
        partial.writeText(JSONArray(turns.map(::json)).toString())
        partial.renameTo(file)
    }

    fun clear() {
        file.delete()
    }

    private fun json(turn: Turn): JSONObject = JSONObject()
        .put("id", turn.id).put("time", turn.time).put("from", turn.from.name)
        .put("asked", turn.asked).put("heard", turn.heard).put("reply", turn.reply).put("state", turn.state.name)
        .put("steps", JSONArray(turn.steps))
        .put("tools", JSONArray(turn.tools.map {
            JSONObject().put("name", it.name).put("args", it.args).put("ok", it.ok).put("declined", it.declined)
                .put("result", it.result)
        }))
        .put("questions", JSONArray(turn.questions.map {
            JSONObject().put("question", it.question).put("details", it.details).put("answer", it.answer)
        }))
        .put("problem", turn.problem).put("model", turn.model)
        .apply {
            turn.cost?.let { put("cost", it) }
            turn.seconds?.let { put("seconds", it) }
        }

    private fun turn(data: JSONObject) = Turn(
        id = data.getString("id"),
        time = data.optLong("time"),
        from = enumValueOr(data.optString("from"), Turn.From.Typed),
        asked = data.optString("asked"),
        heard = data.optString("heard"),
        reply = data.optString("reply"),
        state = enumValueOr(data.optString("state"), Turn.State.Done),
        steps = strings(data.optJSONArray("steps")),
        tools = objects(data.optJSONArray("tools")).map {
            ToolRun(it.optString("name"), it.optString("args"), it.optBoolean("ok"), it.optBoolean("declined"),
                it.optString("result"))
        },
        questions = objects(data.optJSONArray("questions")).map {
            Asked(it.optString("question"), it.optString("details"), it.optString("answer"))
        },
        problem = data.optString("problem"),
        model = data.optString("model"),
        cost = if (data.has("cost")) data.optDouble("cost") else null,
        seconds = if (data.has("seconds")) data.optDouble("seconds") else null,
    )

    private companion object {
        inline fun <reified T : Enum<T>> enumValueOr(name: String, default: T): T =
            enumValues<T>().firstOrNull { it.name == name } ?: default

        fun strings(array: JSONArray?) = List(array?.length() ?: 0) { array!!.optString(it) }

        fun objects(array: JSONArray?) = List(array?.length() ?: 0) { array!!.getJSONObject(it) }
    }
}
