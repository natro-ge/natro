package dev.natro

/**
 * What "this" and "that" mean on the phone now (its tool this; server/natro_agent/this.py asks when a request's
 * words point at something): the app in front, a browser's page and what's playing, by name only. What's on the
 * screen stays here until she reads it with the tool screen. Android doesn't say which file an app has open.
 */
object InFront {
    /** Where each browser keeps its address. Scrolling a page hides the bar: then there's no address to say. */
    val ADDRESS_BARS = mapOf(
        "com.android.chrome" to "com.android.chrome:id/url_bar",
        "com.microsoft.emmx" to "com.microsoft.emmx:id/url_bar",
        "com.brave.browser" to "com.brave.browser:id/url_bar",
        "org.mozilla.firefox" to "org.mozilla.firefox:id/mozac_browser_toolbar_url_view",
        "com.sec.android.app.sbrowser" to "com.sec.android.app.sbrowser:id/location_bar_edit_text",
    )

    /** A web address from what a bar says, which often leaves out the https://; null for words being typed. */
    fun page(bar: String?): String? {
        val text = bar?.trim().orEmpty()
        if (text.startsWith("http://") || text.startsWith("https://")) return text
        val host = text.substringBefore('/')
        return if (host.contains('.') && !host.contains(':') && text.none(Char::isWhitespace)) "https://$text" else null
    }

    /** "Playing: Yellow by Coldplay (Spotify)". */
    fun media(state: String, title: String?, artist: String?, app: String): String? {
        if (title.isNullOrBlank()) return null
        val by = if (artist.isNullOrBlank()) "" else " by $artist"
        return "$state: $title$by ($app)"
    }

    /**
     * As text for Natro. app: the app in front, or (ownChat, when Natro's own chat is) the one before it; null when
     * it isn't known.
     */
    fun describe(app: String?, ownChat: Boolean, page: String?, playing: String?): String {
        val front = when {
            ownChat -> "He's in Natro's own chat" + if (app.isNullOrBlank()) "." else ", and was in $app before it."
            app.isNullOrBlank() -> "Which app is in front isn't known: screen control is off, or the screen is."
            else -> "App in front: $app"
        }
        return listOfNotNull(front, page?.let { "Page: $it" }, playing).joinToString("\n")
    }
}
