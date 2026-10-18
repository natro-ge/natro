package dev.natro

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NotificationWatchTest {
    private val bank = NotificationWatch.Rule("w2", "Bank of Georgia", "")
    private val nino = NotificationWatch.Rule("w3", "WhatsApp", "Nino")
    private val transfer = NotificationWatch.Rule("w4", "", "received GEL")

    @Test
    fun anAppByNameOrPackage() {
        assertEquals(bank, NotificationWatch.match(listOf(bank), "Bank of Georgia", "ge.bog.mobile", "Transfer", "Hi"))
        assertEquals(bank, NotificationWatch.match(listOf(bank), "BoG", "ge.bank of georgia", "", "x"))
        assertNull(NotificationWatch.match(listOf(bank), "TBC Bank", "ge.tbc", "Transfer", "Hi"))
    }

    @Test
    fun everyWordInTheTitleOrText() {
        assertEquals(nino, NotificationWatch.match(listOf(nino), "WhatsApp", "com.whatsapp", "nino", "Are you coming?"))
        assertNull(NotificationWatch.match(listOf(nino), "WhatsApp", "com.whatsapp", "Giorgi", "Ask Nina"))
        assertEquals(transfer, NotificationWatch.match(listOf(nino, transfer), "Messages", "com.google.android.apps.messaging",
            "BOG", "You received 500 GEL"))
        assertNull(NotificationWatch.match(listOf(transfer), "Messages", "x", "BOG", "You sent 500 GEL"))
    }

    @Test
    fun rulesAsTheBrainSendsThem() {
        val rules = JSONArray(listOf(
            JSONObject().put("id", "w2").put("app", " Bank of Georgia ").put("words", ""),
            JSONObject().put("id", "w5").put("app", "").put("words", ""),  // nothing to match: dropped
        ))
        assertEquals(listOf(NotificationWatch.Rule("w2", "Bank of Georgia", "")), NotificationWatch.rulesFrom(rules))
        assertEquals(emptyList<NotificationWatch.Rule>(), NotificationWatch.rulesFrom(null))
    }

    @Test
    fun aNotificationUpdatedInPlaceIsSentOnce() {
        assertTrue(NotificationWatch.firstTime("0|com.whatsapp|1|Nino|Hi"))
        assertFalse(NotificationWatch.firstTime("0|com.whatsapp|1|Nino|Hi"))
        assertTrue(NotificationWatch.firstTime("0|com.whatsapp|1|Nino|Hi again"))
    }
}
