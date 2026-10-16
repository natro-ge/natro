package dev.natro

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SpeechTest {
    private fun feed(end: SpeechEnd, vararg levels: Double) = levels.forEach(end::add)

    @Test
    fun endsAfterTwoSecondsOfQuietFollowingSpeech() {
        val end = SpeechEnd()
        feed(end, 3.0, 2.0, 400.0, 600.0, 250.0)
        feed(end, *DoubleArray(19) { 3.0 })
        assertFalse(end.done)  // a thinking pause
        feed(end, 3.0)
        assertTrue(end.heard && end.done)
    }

    @Test
    fun pausesInsideARequestDontEndIt() {
        val end = SpeechEnd()
        feed(end, 500.0, *DoubleArray(15) { 4.0 }, 300.0, *DoubleArray(10) { 4.0 })
        assertFalse(end.done)
    }

    @Test
    fun givesUpWhenNothingIsSaid() {
        val end = SpeechEnd()
        feed(end, *DoubleArray(59) { 5.0 })
        assertFalse(end.done)
        feed(end, 5.0)
        assertTrue(end.done && !end.heard)
    }

    @Test
    fun noisyRoomNeedsSpeechWellAboveTheNoise() {
        val noisy = SpeechEnd()
        feed(noisy, 40.0, 45.0, 120.0)  // a fan at ~40: speech must pass 160
        assertFalse(noisy.heard)
        feed(noisy, 400.0)
        assertTrue(noisy.heard)
    }

    @Test
    fun theMicrophonesSilentStartIsntTheRoom() {
        val fan = SpeechEnd()
        feed(fan, 0.0, *DoubleArray(5) { 40.0 }, *DoubleArray(10) { 400.0 }, *DoubleArray(19) { 40.0 })
        assertTrue(fan.heard)
        assertFalse(fan.done)
        feed(fan, 40.0)  // the fan at ~40 is the room, not speech: 2 s of it end the request
        assertTrue(fan.done)
    }

    @Test
    fun noiseFarBelowHisVoiceEndsIt() {
        // As recorded on the phone: a silent first block, then speech, then rumble well above the
        // room (~6) that went on for 12 s and used to count as speech.
        val end = SpeechEnd()
        feed(end, 0.0, 6.0, 900.0, 2000.0, 1000.0, 1900.0, 300.0, 1700.0, 2600.0, 1600.0, 2000.0, 1300.0)
        feed(end, *DoubleArray(19) { 200.0 })
        assertFalse(end.done)
        feed(end, 200.0)
        assertTrue(end.heard && end.done)
    }

    @Test
    fun speakingMoreSoftlyLaterStillCounts() {
        val end = SpeechEnd()
        feed(end, 0.0, 4.0, *DoubleArray(10) { 2000.0 }, *DoubleArray(30) { 500.0 })
        assertFalse(end.done)
    }

    @Test
    fun loudnessLeavesOutRumble() {
        // A sine of amplitude 1000 (mean absolute value ~637), 100 ms at a time; the third block, once the filter has settled.
        fun measured(hertz: Double): Double {
            val loudness = Loudness()
            return (0 until 3).map { block ->
                loudness.of(ShortArray(Recorder.RATE / 10) {
                    (1000 * kotlin.math.sin(2 * Math.PI * hertz * (block * Recorder.RATE / 10 + it) / Recorder.RATE)).toInt().toShort()
                })
            }.last()
        }
        assertTrue(measured(50.0) < 80)  // the phone handled, wind
        assertTrue(measured(1000.0) > 600)  // a voice
    }

    @Test
    fun georgianNamesInLatin() {
        assertEquals("nino beridze", Text.latin("ნინო ბერიძე"))
        assertEquals("giorgi", Text.latin("Giorgi"))
        assertEquals(1.0, Names.score("nino", Text.latin("ნინო")), 0.0)
    }

    @Test
    fun riskyButtons() {
        assertTrue(Text.RISKY.containsMatchIn("Send"))
        assertTrue(Text.RISKY.containsMatchIn("Place order"))
        assertTrue(Text.RISKY.containsMatchIn("გაგზავნა"))
        assertFalse(Text.RISKY.containsMatchIn("Search"))
        assertFalse(Text.RISKY.containsMatchIn("Sender settings"))  // whole words only
        assertEquals("a long…", Text.short("a   long text", 7))
    }

    @Test
    fun dockLevelRisesWithTheVoiceAndStaysInRange() {
        assertEquals(0f, SpeechEnd.level(0.0), 0f)
        assertTrue(SpeechEnd.level(3.0) < 0.2f)  // the room
        assertTrue(SpeechEnd.level(100.0) in 0.5f..0.8f)  // speech
        assertEquals(1f, SpeechEnd.level(100_000.0), 0f)
        assertTrue(SpeechEnd.level(300.0) > SpeechEnd.level(30.0))
    }
}
