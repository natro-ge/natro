package dev.natro

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SeenTest {
    private val send = Seen("button", "com.discord:id/send", "Send", 950, 1800, 1050, 1900)
    private val field = Seen("field", "com.discord:id/text", "I don't want to", 40, 1800, 900, 1900)

    @Test
    fun sameElementRebuiltInPlace() {
        assertEquals(1, send.findIn(listOf(field, send.copy(left = 960, right = 1060))))
    }

    @Test
    fun movedOrDifferentIsNotIt() {
        // The keyboard closed and the button moved: not sure it's the one he said yes to.
        assertNull(send.findIn(listOf(field, send.copy(top = 2300, bottom = 2400))))
        assertNull(send.findIn(listOf(field, send.copy(label = "Delete"))))
        assertNull(send.findIn(listOf(field, send.copy(id = "com.discord:id/other"))))
        assertNull(send.findIn(listOf(field)))
    }

    @Test
    fun twoAlikeIsNotIt() {
        assertNull(send.findIn(listOf(send, send.copy(top = 1810, bottom = 1910))))
    }

    @Test
    fun noLabelIsNeverRecognized() {
        val icon = send.copy(label = "")
        assertNull(icon.findIn(listOf(icon)))
    }
}
