package dev.natro

import org.json.JSONArray

/**
 * What the brain asked the phone to watch for in its notifications (server/natro_agent/watchers.py: watch_rules), and
 * the matching. Only a notification that fits a rule leaves the phone (notification_matched); the rest are never
 * sent. A rule names an app (part of its name or package), words that must all be in the notification's title or
 * text, or both; letter case doesn't matter.
 */
object NotificationWatch {
    data class Rule(val id: String, val app: String, val words: String)

    /** The rules now, as the brain last sent them (on connecting, and whenever they change). */
    @Volatile var rules: List<Rule> = emptyList()

    /** Notifications matched lately: one updated in place comes again, and is sent only once. */
    private val recent = ArrayDeque<String>()
    private const val RECENT = 50

    fun rulesFrom(array: JSONArray?): List<Rule> = List(array?.length() ?: 0) { i ->
        val rule = array!!.getJSONObject(i)
        Rule(rule.optString("id"), rule.optString("app").trim(), rule.optString("words").trim())
    }.filter { it.id.isNotBlank() && (it.app.isNotBlank() || it.words.isNotBlank()) }

    /** The first rule a notification fits, or null. app: its app's name; pkg: its package. */
    fun match(rules: List<Rule>, app: String, pkg: String, title: String, text: String): Rule? {
        val said = "$title $text"
        return rules.firstOrNull { rule ->
            (rule.app.isBlank() || app.contains(rule.app, ignoreCase = true) || pkg.contains(rule.app, ignoreCase = true)) &&
                rule.words.split(Regex("\\s+")).filter { it.isNotBlank() }.all { said.contains(it, ignoreCase = true) }
        }
    }

    /** True the first time a notification (its key and words) is seen; it's remembered for next time. */
    @Synchronized
    fun firstTime(key: String): Boolean {
        if (key in recent) return false
        recent.addLast(key)
        if (recent.size > RECENT) recent.removeFirst()
        return true
    }
}
