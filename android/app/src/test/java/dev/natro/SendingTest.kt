package dev.natro

import dev.natro.Sending.Node
import dev.natro.Sending.Route
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SendingTest {
    @Test
    fun theChatAndThePersonFromANotification() {
        assertEquals("340282366841710300949128137443944319108",
            Sending.chatId(" 340282366841710300949128137443944319108 "))
        assertEquals("", Sending.chatId(null))
        assertEquals("340282366841710301244258928979199747040",
            Sending.threadId("thread_shortcut_3619688556_340282366841710301244258928979199747040"))
        assertEquals("", Sending.threadId("thread_shortcut_GROUP:42"))
        assertEquals("", Sending.threadId(""))
        assertEquals("100012345678901", Sending.facebookId("FACEBOOK:100012345678901"))
        assertEquals("", Sending.facebookId("INSTAGRAM:123"))
        assertEquals("", Sending.facebookId(null))
    }

    @Test
    fun tokensAreKeptPerChatTheNewestKept() {
        val tokens = ReplyTokens<String> { it }
        tokens.keep("ig", "nino", "a")
        tokens.keep("ig", "giorgi", "b")
        tokens.keep("ig", "nino", "a2")
        assertEquals("a2", tokens.get("ig", "nino"))
        assertEquals("b", tokens.get("ig", "giorgi"))
        assertNull(tokens.get("ig", "zura"))
        tokens.drop("ig", "nino")
        assertNull(tokens.get("ig", "nino"))
        assertEquals("b", tokens.get("ig", "giorgi"))
    }

    @Test
    fun anAppWhoseTokensAreSharedBetweenChatsNeverHasOneKept() {
        val tokens = ReplyTokens<String> { it }
        tokens.keep("fb", "andria", "same")
        tokens.keep("ig", "nino", "same")  // another app's: never compared
        tokens.keep("fb", "family", "same")  // two chats, one token: it could answer the wrong one
        assertTrue(tokens.isShared("fb"))
        assertNull(tokens.get("fb", "andria"))
        assertNull(tokens.get("fb", "family"))
        tokens.keep("fb", "andria", "new")
        assertNull(tokens.get("fb", "andria"))
        assertEquals("same", tokens.get("ig", "nino"))
    }

    @Test
    fun theWayChosenLockedOrNot() {
        assertEquals(listOf(Route.NOTIFICATION, Route.KEPT, Route.SHARE, Route.LINK),
            Sending.routes(live = true, kept = true, share = true, link = true, locked = false))
        assertEquals(listOf(Route.NOTIFICATION, Route.KEPT),
            Sending.routes(live = true, kept = true, share = true, link = true, locked = true))
        assertEquals(listOf(Route.LINK), Sending.routes(live = false, kept = false, share = false, link = true, locked = false))
        assertEquals(emptyList<Route>(), Sending.routes(live = false, kept = false, share = true, link = true, locked = true))
        // The quiet ways only: never the screen, locked or not.
        assertEquals(listOf(Route.KEPT),
            Sending.routes(live = false, kept = true, share = true, link = true, locked = false, screen = false))
    }

    @Test
    fun linksAndUsernames() {
        assertEquals("https://ig.me/m/nino.k", Sending.link(Sending.INSTAGRAM, "nino.k"))
        assertEquals("https://m.me/andria.beridze", Sending.link(Sending.MESSENGER, "andria.beridze"))
        assertTrue(Sending.usernameOk(Sending.INSTAGRAM, "andria.b"))
        assertTrue(Sending.usernameOk(Sending.INSTAGRAM, "zura_99"))
        assertFalse(Sending.usernameOk(Sending.INSTAGRAM, "nino k"))
        assertFalse(Sending.usernameOk(Sending.INSTAGRAM, ".nino"))
        assertFalse(Sending.usernameOk(Sending.INSTAGRAM, "a".repeat(31)))
        assertFalse(Sending.usernameOk(Sending.MESSENGER, "zura_99"))
        assertFalse(Sending.usernameOk("org.telegram.messenger", "nino"))
    }

    @Test
    fun theChatsTitleFitsWhomHeAskedForInEitherAlphabet() {
        assertTrue(Sending.titleFits("andria.b", "", "andria.b"))
        assertTrue(Sending.titleFits("Nino  Kapanadze", "Nino Kapanadze", ""))
        assertTrue(Sending.titleFits("ნინო კაპანაძე", "Nino Kapanadze", "nino.k"))
        assertTrue(Sending.titleFits("Nino Kapanadze", "ნინო კაპანაძე", ""))
        assertFalse(Sending.titleFits("Nino Beridze", "Nino Kapanadze", "nino.k"))
        assertFalse(Sending.titleFits("", "Nino", "nino"))
        assertFalse(Sending.titleFits("nino.k2", "", "nino.k"))
    }

    private fun node(id: String = "", text: String = "", label: String = "", editable: Boolean = false,
                     enabled: Boolean = true, top: Int = 0, bottom: Int = 100) =
        Node(id, text, label, editable, enabled, top, bottom)

    @Test
    fun sendIsPickedFromTheScreen() {
        val box = node(id = "row_thread_composer_edittext", text = "Hi", editable = true, top = 2000, bottom = 2100)
        val byId = listOf(node(text = "Nino"), box, node(id = "row_thread_composer_send_button_container", top = 2000, bottom = 2100))
        assertEquals(2, Sending.sendButton(byId, box, listOf("row_thread_composer_send_button_container")))
        // By its label, beside the box; one above it (a message saying "Send") doesn't count.
        val byLabel = listOf(node(text = "Send", top = 500, bottom = 560), box, node(label = "Send", top = 2010, bottom = 2090))
        assertEquals(2, Sending.sendButton(byLabel, box, emptyList()))
        // Two, a disabled one only, or none: nothing is tapped.
        assertNull(Sending.sendButton(listOf(box, node(label = "Send", top = 2000), node(label = "Send", top = 2050)), box, emptyList()))
        assertNull(Sending.sendButton(listOf(box, node(label = "Send", enabled = false, top = 2000, bottom = 2100)), box, emptyList()))
        assertNull(Sending.sendButton(listOf(box, node(label = "Like", top = 2000, bottom = 2100)), box, emptyList()))
        // The share sheet's button, by its id, with no box.
        assertEquals(1, Sending.sendButton(listOf(node(text = "Hi"), node(id = "send_button", text = "Share")), null,
            listOf("send_button")))
    }

    @Test
    fun theMessageSeenInTheChat() {
        // Instagram's as its text; Messenger's in a label that starts with the sender's name.
        val chat = listOf(node(text = "Running late"), node(label = "Giorgi Beridze, Running late, 22:51"),
            node(text = "Running late", editable = true))
        assertEquals(2, Sending.shows(chat, "Running late"))
        assertEquals(0, Sending.shows(chat, "On my way"))
    }

    @Test
    fun aMessageWentOnceItShowsOrTheBoxLetsGoOfIt() {
        val typed = node(text = "Running late", editable = true)
        val empty = node(text = "Message", editable = true)
        assertFalse(Sending.sent(listOf(node(text = "Hi"), typed), 1, "Running late", 0))  // still in the box
        assertTrue(Sending.sent(listOf(node(text = "Running late"), empty), 1, "Running late", 0))  // Instagram
        assertTrue(Sending.sent(listOf(node(label = "23:47"), empty), 1, "Running late", 0))  // Messenger
        assertFalse(Sending.sent(listOf(node(text = "Running late"), typed), 1, "Running late", 1))  // an old one
        assertFalse(Sending.sent(listOf(node(text = "Hi")), null, "Running late", 0))  // no box: unknown
    }

    @Test
    fun theChatsTitleOnEachAppsScreen() {
        val instagram = listOf(node(id = "header_left_button", label = "Back"), node(id = "header_title", text = "andria.b"))
        assertEquals("andria.b", Sending.title(Sending.INSTAGRAM, instagram))
        val messenger = listOf(node(label = "Back"), node(label = "Andria Beridze, Thread Details"),
            node(label = "Thread Details"))
        assertEquals("Andria Beridze", Sending.title(Sending.MESSENGER, messenger))
        assertNull(Sending.title(Sending.MESSENGER, listOf(node(label = "Chats"))))
        assertEquals("https://m.me/100012345678901", Sending.link(Sending.MESSENGER, "100012345678901"))
    }
}
