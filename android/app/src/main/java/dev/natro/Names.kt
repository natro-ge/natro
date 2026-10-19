package dev.natro

import java.net.URI
import java.net.URISyntaxException
import java.util.Locale

/** Plain functions for the phone's tools: matching spoken names, reading times and levels, places and Maps links. */
object Names {
    const val GOOD_MATCH = 0.6

    fun words(text: String): String =
        Regex("[\\p{L}\\p{N}]+").findAll(text.lowercase()).joinToString(" ") { it.value }

    /** How well a spoken app name matches a real one, 0 to 1 (as in the PC app's apps.py). */
    fun score(query: String, name: String): Double {
        val q = words(query)
        val n = words(name)
        if (q.isEmpty() || n.isEmpty()) return 0.0
        if (q == n || q.replace(" ", "") == n.replace(" ", "")) return 1.0
        val nameWords = n.split(" ")
        val queryWords = q.split(" ")
        if (queryWords.all { part -> nameWords.any { it.startsWith(part) } }) {
            return 0.9 - 0.02 * (nameWords.size - queryWords.size)  // "Spotify" over "Spotify Lite"
        }
        if (q in n) return 0.75
        return 0.75 * similarity(q, n)
    }

    fun best(query: String, names: List<String>): String? =
        names.map { it to score(query, it) }.filter { it.second >= GOOD_MATCH }.maxByOrNull { it.second }?.first

    fun closest(query: String, names: List<String>, count: Int = 5): List<String> =
        names.sortedByDescending { similarity(words(query), words(it)) }.take(count)

    /** 1 minus the edit distance over the longer length. */
    fun similarity(a: String, b: String): Double {
        if (a.isEmpty() && b.isEmpty()) return 1.0
        var previous = IntArray(b.length + 1) { it }
        for (i in 1..a.length) {
            val current = IntArray(b.length + 1)
            current[0] = i
            for (j in 1..b.length) {
                val cost = if (a[i - 1] == b[j - 1]) 0 else 1
                current[j] = minOf(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            }
            previous = current
        }
        return 1.0 - previous[b.length].toDouble() / maxOf(a.length, b.length)
    }

    /** "07:30", "7:30" or "19:05" as (hour, minute). */
    fun time(text: String): Pair<Int, Int> {
        val match = Regex("^\\s*(\\d{1,2})[:.](\\d{2})\\s*$").find(text)
            ?: throw ToolFailure("Give the time as HH:MM in 24 hours, like 07:30 or 19:05.")
        val hour = match.groupValues[1].toInt()
        val minute = match.groupValues[2].toInt()
        if (hour > 23 || minute > 59) throw ToolFailure("$text isn't a time of day.")
        return hour to minute
    }

    /** A timer's length as said aloud: "5 minutes", "1 hour 30 minutes", "45 seconds". */
    fun duration(seconds: Int): String {
        fun part(count: Int, unit: String) = if (count == 0) null else "$count $unit${if (count == 1) "" else "s"}"
        return listOfNotNull(part(seconds / 3600, "hour"), part(seconds % 3600 / 60, "minute"), part(seconds % 60, "second"))
            .joinToString(" ").ifEmpty { "0 seconds" }
    }

    /** A volume request ("60", "up", "down", "max", "mute") as a new percentage, given the current one. */
    fun level(request: String, current: Int, step: Int = 10): Int = when (val value = request.trim().lowercase()) {
        "up" -> current + step
        "down" -> current - step
        "max" -> 100
        "mute", "off" -> 0
        else -> value.removeSuffix("%").toIntOrNull()
            ?: throw ToolFailure("The level is 0 to 100, or up, down, max or mute.")
    }.coerceIn(0, 100)

    /** Where the phone is, for the brain: "41.69339,44.80153 (within 12 m, just now)". The first part is a place the
     *  travel tools take; the dot is a dot in every language. */
    fun location(latitude: Double, longitude: Double, meters: Float?, ageSeconds: Long): String {
        val minutes = (ageSeconds / 60).toInt()
        val age = when {
            minutes < 1 -> "just now"
            minutes < 60 -> "${duration(minutes * 60)} ago"
            else -> "${duration(minutes / 60 * 3600)} ago"
        }
        val within = if (meters != null) "within ${Math.round(meters)} m, " else ""
        return String.format(Locale.US, "%.5f,%.5f (%s%s)", latitude, longitude, within, age)
    }

    /**
     * A Google Maps link to open on the phone, as given; with navigate, a directions link made to start navigating
     * from where he is (Google's dir_action=navigate, and no origin). Anything but a Google Maps link is refused.
     */
    fun mapsLink(link: String, navigate: Boolean): String {
        val given = link.trim().replace(" ", "%20")
        val uri = try {
            URI(given)
        } catch (e: URISyntaxException) {
            null
        }
        val host = uri?.host.orEmpty().lowercase()
        val path = uri?.rawPath.orEmpty()
        val maps = uri?.scheme?.lowercase() in setOf("https", "http") && (host == "maps.app.goo.gl" ||
            GOOGLE.matches(host) && (host.startsWith("maps.") || path.startsWith("/maps")))
        if (!maps) throw ToolFailure("That isn't a Google Maps link (https://www.google.com/maps/...).")
        if (!navigate) return given
        if (!path.startsWith("/maps/dir")) {
            throw ToolFailure("To navigate, give a directions link (https://www.google.com/maps/dir/?api=1&...).")
        }
        val kept = uri!!.rawQuery.orEmpty().split("&")
            .filter { it.isNotEmpty() && it.substringBefore("=") !in setOf("origin", "origin_place_id", "dir_action") }
        return "${uri.scheme}://${uri.rawAuthority}$path?" + (kept + "dir_action=navigate").joinToString("&")
    }

    private val GOOGLE = Regex("(www\\.|maps\\.)?google\\.[a-z]{2,3}(\\.[a-z]{2})?")
}

class ToolFailure(message: String) : Exception(message)

/** A tool's result with a picture (a screenshot): text tells Natro, the picture goes into the chat for him. */
class ToolPicture(val text: String, val data: ByteArray, val mime: String, val name: String)
