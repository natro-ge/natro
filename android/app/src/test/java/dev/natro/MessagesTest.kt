package dev.natro

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MessagesTest {
    @Test
    fun whoAMessageIsFrom() {
        assertEquals("Nino", Messages.who("Nino", "", "Nino"))
        assertEquals("Nino in Family", Messages.who("Family (3 messages)", "Family", "Nino"))
        assertEquals("Family", Messages.who("Family", "Family", ""))
        assertEquals("Giorgi", Messages.who("Giorgi", "", ""))
        assertEquals("Nino", Messages.who("x", "Nino", "nino"))  // a conversation named after its sender
    }

    @Test
    fun aMessageFitsWhomHeNamedInEitherAlphabet() {
        assertTrue(Messages.fits("Nino Beridze", "WhatsApp", "com.whatsapp", "nino", ""))
        assertTrue(Messages.fits("ნინო", "WhatsApp", "com.whatsapp", "Nino", ""))
        assertTrue(Messages.fits("Family", "Messenger", "com.facebook.orca", "family", "messenger"))
        assertTrue(Messages.fits("Nino", "WhatsApp", "com.whatsapp", "", "whatsapp"))
        assertFalse(Messages.fits("Giorgi", "WhatsApp", "com.whatsapp", "Nino", ""))
        assertFalse(Messages.fits("Nino", "Telegram", "org.telegram.messenger", "Nino", "WhatsApp"))
    }

    @Test
    fun aMessageGoesOnceAndItsNotificationGoingIsTold() {
        assertTrue(Messages.firstTime("k1", "Nino", "Are you coming?"))
        assertFalse(Messages.firstTime("k1", "Nino", "Are you coming?"))  // the same notification, updated
        assertTrue(Messages.firstTime("k1", "Nino", "Hello?"))
        assertTrue(Messages.forget("k1"))
        assertFalse(Messages.forget("k1"))
    }
}
