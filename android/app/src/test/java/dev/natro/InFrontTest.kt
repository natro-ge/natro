package dev.natro

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** What "this" means on the phone, as she reads it. */
class InFrontTest {
    @Test
    fun aBrowsersAddress() {
        assertEquals("https://example.com/a?b=c", InFront.page("https://example.com/a?b=c"))
        assertEquals("https://github.com/natro-ge", InFront.page("github.com/natro-ge"))
        assertEquals("https://example.com", InFront.page(" example.com "))
        for (neither in listOf(null, "", "two words", "Search or type web address", "chrome://settings", "a.b c")) {
            assertNull(neither, InFront.page(neither))
        }
    }

    @Test
    fun whatsPlaying() {
        assertEquals("Playing: Yellow by Coldplay (Spotify)", InFront.media("Playing", "Yellow", "Coldplay", "Spotify"))
        assertEquals("Paused: A talk (YouTube)", InFront.media("Paused", "A talk", null, "YouTube"))
        assertNull(InFront.media("Playing", " ", "Her", "Spotify"))
        assertNull(InFront.media("Playing", null, null, "Spotify"))
    }

    @Test
    fun itSaysWhatThereIs() {
        assertEquals("App in front: Chrome\nPage: https://example.com\nPlaying: Yellow (Spotify)",
            InFront.describe("Chrome", false, "https://example.com", "Playing: Yellow (Spotify)"))
        assertEquals("App in front: Files", InFront.describe("Files", false, null, null))
        assertEquals("He's in Natro's own chat, and was in Chrome before it.", InFront.describe("Chrome", true, null, null))
        assertEquals("He's in Natro's own chat.", InFront.describe("", true, null, null))
        assertEquals("Which app is in front isn't known: screen control is off, or the screen is.\nPlaying: Yellow (Spotify)",
            InFront.describe(null, false, null, "Playing: Yellow (Spotify)"))
    }
}
