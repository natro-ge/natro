package dev.natro

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Test
import java.util.Locale

class NamesTest {
    private val apps = listOf("Spotify", "Spotify Lite", "YouTube", "YouTube Music", "Google Maps", "Clock", "Camera",
        "Calculator", "Nothing X")

    @Test
    fun appNamesAsPeopleSayThem() {
        assertEquals("Spotify", Names.best("spotify", apps))
        assertEquals("YouTube", Names.best("youtube", apps))
        assertEquals("YouTube Music", Names.best("youtube music", apps))
        assertEquals("Google Maps", Names.best("maps", apps))
        assertEquals("Calculator", Names.best("calculater", apps))  // misheard
        assertNull(Names.best("telegram", apps))
        assertEquals("Camera", Names.closest("camra", apps, 1).single())
    }

    @Test
    fun times() {
        assertEquals(7 to 30, Names.time("07:30"))
        assertEquals(7 to 5, Names.time("7.05"))
        assertEquals(19 to 0, Names.time(" 19:00 "))
        assertThrows(ToolFailure::class.java) { Names.time("7am") }
        assertThrows(ToolFailure::class.java) { Names.time("25:00") }
    }

    @Test
    fun durations() {
        assertEquals("5 minutes", Names.duration(300))
        assertEquals("1 hour 30 minutes", Names.duration(5400))
        assertEquals("1 minute 5 seconds", Names.duration(65))
    }

    @Test
    fun volumeLevels() {
        assertEquals(60, Names.level("up", 50))
        assertEquals(0, Names.level("down", 5))
        assertEquals(35, Names.level("35%", 80))
        assertEquals(100, Names.level("max", 10))
        assertEquals(0, Names.level("mute", 70))
        assertEquals(100, Names.level("150", 10))
        assertThrows(ToolFailure::class.java) { Names.level("loud", 50) }
    }

    @Test
    fun whereThePhoneIs() {
        assertEquals("41.69339,44.80153 (within 12 m, just now)", Names.location(41.693394, 44.801528, 12.4f, 20))
        assertEquals("-33.86882,151.20929 (5 minutes ago)", Names.location(-33.868820, 151.209290, null, 330))
        assertEquals("41.69339,44.80153 (within 1500 m, 2 hours ago)", Names.location(41.69339, 44.80153, 1500f, 7300))
        // A dot, whatever the phone's language writes.
        val before = Locale.getDefault()
        Locale.setDefault(Locale.GERMANY)
        try {
            assertEquals("41.69339,44.80153 (just now)", Names.location(41.69339, 44.80153, null, 0))
        } finally {
            Locale.setDefault(before)
        }
    }

    @Test
    fun mapsLinks() {
        val route = "https://www.google.com/maps/dir/?api=1&origin=12+Chavchavadze+Ave%2C+Tbilisi&destination=Tbilisi+airport&travelmode=transit"
        assertEquals(route, Names.mapsLink(" $route ", navigate = false))
        // Navigating goes from where he is: no origin, and Google's word to start.
        assertEquals("https://www.google.com/maps/dir/?api=1&destination=Tbilisi+airport&travelmode=transit&dir_action=navigate",
            Names.mapsLink(route, navigate = true))
        assertEquals("https://www.google.com/maps/dir/?api=1&destination=Vake%20Park&dir_action=navigate",
            Names.mapsLink("https://www.google.com/maps/dir/?api=1&destination=Vake Park&dir_action=preview", navigate = true))
        assertEquals("https://maps.app.goo.gl/abc123", Names.mapsLink("https://maps.app.goo.gl/abc123", navigate = false))
        assertEquals("https://maps.google.ge/?q=Mtskheta", Names.mapsLink("https://maps.google.ge/?q=Mtskheta", navigate = false))
        // Only Google Maps opens, and only a route is navigated.
        for (other in listOf("https://www.google.com/search?q=maps", "https://evil.example/maps/dir/?api=1",
                "https://www.google.com.evil.example/maps/dir/", "javascript:alert(1)", "intent://maps#Intent;end", "")) {
            assertThrows(other, ToolFailure::class.java) { Names.mapsLink(other, navigate = false) }
        }
        assertThrows(ToolFailure::class.java) {
            Names.mapsLink("https://www.google.com/maps/search/?api=1&query=Vake+Park", navigate = true)
        }
    }
}

class LouderTest {
    private fun pcm(vararg samples: Int) = ByteArray(samples.size * 2).also { out ->
        samples.forEachIndexed { i, s -> out[2 * i] = s.toByte(); out[2 * i + 1] = (s shr 8).toByte() }
    }

    private fun samples(pcm: ByteArray) = List(pcm.size / 2) { ((pcm[2 * it + 1].toInt() shl 8) or (pcm[2 * it].toInt() and 0xFF)).toShort().toInt() }

    @Test
    fun quietSpeechIsMadeLouderWithinLimits() {
        // Loudest frame 200 -> gain 20 (4,000 / 200); negatives survive the round trip.
        val quiet = pcm(*IntArray(Recorder.RATE / 50) { if (it % 2 == 0) 200 else -200 })
        assertEquals(listOf(4000, -4000), samples(Recorder.louder(quiet)).take(2))
        // Near-silence is capped at 30 times, so it stays quiet.
        val silence = pcm(*IntArray(Recorder.RATE / 50) { 3 })
        assertEquals(90, samples(Recorder.louder(silence)).first())
        // Loud enough already: unchanged.
        val loud = pcm(*IntArray(Recorder.RATE / 50) { 5000 })
        assertEquals(5000, samples(Recorder.louder(loud)).first())
    }
}
