package dev.natro

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs
import kotlin.math.sin

class WakeWordTest {
    private val block = ShortArray(Recorder.RATE / 10)

    /** Feeds blocks of these loudnesses, the spotter hearing `phrase` in the one at `heardAt`; the tries that ended. */
    private fun Tries.feed(levels: List<Double>, phrase: String? = null, heardAt: Int = -1) =
        levels.mapIndexedNotNull { i, level -> add(block, level, if (i == heardAt) phrase else null) }

    @Test
    fun aTryEndsAfterASecondsPauseAndKeepsTheHalfSecondBefore() {
        val tries = Tries()
        val levels = List(8) { 3.0 } + List(6) { 400.0 } + List(10) { 3.0 }
        val done = tries.feed(levels, "Hey Natro", heardAt = 13)
        assertEquals(1, done.size)
        assertEquals("Hey Natro", done[0].phrase)
        assertEquals(400.0, done[0].loudness, 0.0)
        assertEquals((5 + 6 + 10) * block.size * 2, done[0].pcm.size)  // 0.5 s before, the speech, the pause
    }

    @Test
    fun theSpotterHearsItAMomentLaterAndItStillCounts() {
        val tries = Tries()
        val done = tries.feed(List(3) { 3.0 } + List(6) { 400.0 } + List(10) { 3.0 }, "OK Natro", heardAt = 11)
        assertEquals("OK Natro", done.single().phrase)
    }

    @Test
    fun aTryWithoutTheWakeWordIsMissed() {
        val tries = Tries()
        val done = tries.feed(List(3) { 3.0 } + List(6) { 400.0 } + List(9) { 3.0 } + listOf(400.0) + List(10) { 3.0 })
        assertEquals(1, done.size)  // a short pause inside a try doesn't split it
        assertNull(done[0].phrase)
    }

    @Test
    fun triesAreKeptWhenTheTestIsLeftAndAfterARestart() {
        val cache = kotlin.io.path.createTempDirectory("natro").toFile()
        val quiet = ShortArray(block.size)
        val voice = ShortArray(block.size) { (2000 * sin(2 * Math.PI * 300 * it / Recorder.RATE)).toInt().toShort() }
        fun sayAndPause(phrase: String?) {
            repeat(6) { WakeTest.hear(voice, if (it == 5) phrase else null, WakeTest.Mic.Recognition) }
            repeat(10) { WakeTest.hear(quiet, null, WakeTest.Mic.Recognition) }
        }
        WakeTest.tries.value = emptyList()
        WakeTest.start(cache)
        repeat(3) { WakeTest.hear(quiet, null, WakeTest.Mic.Recognition) }
        sayAndPause("Hey Natro")
        sayAndPause(null)
        WakeTest.stop()
        WakeTest.start(cache)  // the screen opened again
        assertEquals(2, WakeTest.tries.value.size)
        WakeTest.stop()
        WakeTest.tries.value = emptyList()  // the app restarted
        WakeTest.start(cache)
        val restored = WakeTest.tries.value
        assertEquals(listOf(1, 2), restored.map { it.number })
        assertEquals(listOf("Hey Natro", null), restored.map { it.phrase })
        assertTrue(restored.all { it.mic == WakeTest.Mic.Recognition && it.loudness > 1000 })
        WakeTest.clear()
        WakeTest.stop()
        assertTrue(cache.resolve("wake-test").listFiles().orEmpty().isEmpty())
        cache.deleteRecursively()
    }

    private fun sine(amplitude: Double, blocks: Int) = (0 until blocks).map { b ->
        ShortArray(block.size) { (amplitude * sin(2 * Math.PI * 1000 * (b * block.size + it) / Recorder.RATE)).toInt().toShort() }
    }

    @Test
    fun theVolumeTurnsQuietSpeechUpAtMostTenTimes() {
        val input = WakeInput()
        val peak = sine(100.0, 3).map { input.of(it) }.last().maxOf { abs(it) }
        assertEquals(100.0 * WakeInput.MAX_GAIN / 32768, peak.toDouble(), 0.003)
    }

    @Test
    fun loudSpeechIsTurnedDownNotClipped() {
        val input = WakeInput()
        val out = sine(30_000.0, 3).map { input.of(it) }
        assertTrue(out.all { part -> part.all { abs(it) <= WakeInput.TARGET + 0.01 } })
        assertTrue(out.last().maxOf { abs(it) } > WakeInput.TARGET * 0.9)
    }
}
