package dev.natro

import android.app.KeyguardManager
import android.app.Notification
import android.app.Person
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.Rect
import android.net.Uri
import android.os.Bundle
import android.os.PowerManager
import android.os.SystemClock
import android.service.notification.StatusBarNotification
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONObject
import java.time.LocalDate

/**
 * Messaging anyone on Instagram or Messenger (agent/natro_agent/sending.py): the brain finds the person in Natro's
 * address book and asks him; on his yes it calls send_message here (a tool the model isn't offered), and the phone
 * sends it the best way it has:
 *  1. Her chat's notification is still there: its Reply, as reply_message does.
 *  2. A Reply kept from her chat's last notification: the same, with no notification (and the phone locked too).
 *     Tokens are kept per chat, in memory only (Android can't save them), so they're lost when Natro's app
 *     restarts. An app whose tokens turn out shared between chats never gets this way. Messenger's answer once.
 *  3. Instagram: sharing into her chat by its id, as the share sheet's row of people does, then Share tapped on the
 *     sheet once it shows exactly the text. (Messenger's share by the person's id opens its general Send-to list,
 *     which neither shows the text nor names anyone to the screen reader, so it isn't used: tried 2026-10-22.)
 *  4. Her chat's link (ig.me/m/<username>; m.me/<username or Facebook id>): the chat's title checked against whom
 *     he asked for, the text typed, Send tapped, and the text seen in the chat.
 * 3 and 4 need the screen (screen control on, the phone unlocked); anything unexpected stops them, with nothing
 * sent. Each chat that writes is told to the brain once a day (person: the app, the name, the ids; never the
 * words), for the address book, whatever "Show messages on the PC" says.
 */
object Sending {
    const val INSTAGRAM = "com.instagram.android"
    const val MESSENGER = "com.facebook.orca"
    private val APPS = mapOf("instagram" to INSTAGRAM, "messenger" to MESSENGER)
    private val LABELS = mapOf(INSTAGRAM to "Instagram", MESSENGER to "Messenger")
    /** Where text shared into one Instagram chat goes. */
    private const val SHARE_SCREEN = "com.instagram.direct.share.handler.DirectShareHandlerActivity"
    /**
     * Instagram's view ids (after ":id/"): its share sheet's button, a chat's title, box and Send (seen 2026-10-22).
     * Messenger's views have no ids: its box is the only text field, its Send the button labelled Send beside it,
     * and its title the header's "<name>, Thread Details".
     */
    private val SHARE_BUTTON = listOf("send_button")
    private val TITLE = listOf("header_title")
    private val BOX = mapOf(INSTAGRAM to listOf("row_thread_composer_edittext"))
    private val SEND = mapOf(INSTAGRAM to listOf("row_thread_composer_send_button_container"))
    private val MESSENGER_TITLE = Regex("^(.+), Thread Details$")
    private val SEND_WORDS = Regex("^(send|share|გაგზავნა|გაზიარება)$", RegexOption.IGNORE_CASE)
    private const val SCREEN_MILLIS = 8000L
    private const val SETTLE_MILLIS = 1000L
    private const val TYPED_MILLIS = 3000L
    private const val SEEN_MILLIS = 3000L

    enum class Route { NOTIFICATION, KEPT, SHARE, LINK }

    /** A screen element as sending reads it: its view id (after ":id/"), words and place. */
    data class Node(
        val id: String, val text: String, val label: String, val editable: Boolean, val enabled: Boolean,
        val top: Int, val bottom: Int,
    ) {
        fun beside(other: Node) = top < other.bottom && bottom > other.top
    }

    private class Chat(val app: String, val chat: String, val who: String)

    private val tokens = ReplyTokens<Notification.Action> { it.actionIntent }
    /** Notification key -> its chat, for the PC's Reply to a message whose notification has gone. */
    private val chats = LinkedHashMap<String, Chat>()
    private const val MOST_KEYS = 100
    /** "app|chat" -> the day it was last told to the brain. */
    private val told = HashMap<String, String>()

    /** The chat a notification is for: its conversation shortcut's id (Instagram's thread id). */
    fun chatId(shortcut: String?): String = shortcut?.trim().orEmpty()

    /** What Instagram's share takes to go into one chat: the thread id, the end of its chat's shortcut id
     * (thread_shortcut_<his id>_<thread id>). Given the whole shortcut id, it opens its general share sheet. */
    fun threadId(chat: String): String = chat.substringAfterLast('_').takeIf { it.isNotEmpty() && it.all(Char::isDigit) }.orEmpty()

    /** Messenger's sender (a MessagingStyle Person) has the person's Facebook id as its key, "FACEBOOK:<id>". */
    fun facebookId(key: String?): String = Regex("^FACEBOOK:(\\d{5,20})$").find(key.orEmpty())?.groupValues?.get(1).orEmpty()

    fun usernameOk(app: String, username: String): Boolean = when (app) {
        INSTAGRAM -> Regex("[a-z0-9._]{1,30}").matches(username) && username.trim('.') == username
        MESSENGER -> Regex("[a-z0-9.]{5,50}").matches(username) && username.trim('.') == username
        else -> false
    }

    /** The link that opens one chat: ig.me/m/<username>, or m.me/<username or Facebook id>. */
    fun link(app: String, who: String): String = if (app == INSTAGRAM) "https://ig.me/m/$who" else "https://m.me/$who"

    /** The chat's title on its screen (Instagram's header; Messenger's "<name>, Thread Details"), or null. */
    fun title(app: String, nodes: List<Node>): String? =
        if (app == INSTAGRAM) nodes.firstOrNull { it.id in TITLE }?.text
        else nodes.firstNotNullOfOrNull { MESSENGER_TITLE.find(it.label.trim())?.groupValues?.get(1) }

    /** Whether a chat's title is whom he asked for: the username, or the name in Georgian or Latin letters. */
    fun titleFits(title: String, name: String, username: String): Boolean {
        val shown = plain(title).removePrefix("@")
        if (shown.isEmpty()) return false
        return (username.isNotBlank() && shown == username.lowercase()) || (name.isNotBlank() && shown == plain(name))
    }

    private fun plain(text: String) = Text.latin(text).split(Regex("\\s+")).filter { it.isNotBlank() }.joinToString(" ")

    /** The ways to send, best first. Locked, or asked for quiet ones only (screen false), only those with no screen. */
    fun routes(live: Boolean, kept: Boolean, share: Boolean, link: Boolean, locked: Boolean, screen: Boolean = true):
        List<Route> = buildList {
        if (live) add(Route.NOTIFICATION)
        if (kept) add(Route.KEPT)
        if (share && screen && !locked) add(Route.SHARE)
        if (link && screen && !locked) add(Route.LINK)
    }

    /**
     * Its Send: the one enabled element with one of its view ids, else the one enabled element labelled Send or Share
     * beside the box (or anywhere, with no box). Two or none: null.
     */
    fun sendButton(nodes: List<Node>, box: Node?, ids: List<String>): Int? {
        val byId = nodes.indices.filter { nodes[it].id in ids && nodes[it].enabled }
        if (byId.isNotEmpty()) return byId.singleOrNull()
        return nodes.indices.filter { i ->
            val node = nodes[i]
            node.enabled && !node.editable && SEND_WORDS.matches(node.label.ifBlank { node.text }.trim()) &&
                (box == null || node.beside(box))
        }.singleOrNull()
    }

    /** A notification came from Instagram or Messenger: its Reply kept for its chat, and the chat told once a day. */
    fun seen(item: StatusBarNotification, title: String) {
        val app = item.packageName
        if (app !in LABELS) return
        val chat = chatId(item.notification.shortcutId)
        if (chat.isEmpty()) return
        val extras = item.notification.extras
        val group = extras.getBoolean(Notification.EXTRA_IS_GROUP_CONVERSATION)
        val conversation = extras.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)?.toString().orEmpty()
        val last = extras.getParcelableArray(Notification.EXTRA_MESSAGES, Bundle::class.java)?.lastOrNull()
        val sender = last?.getParcelable("sender_person", Person::class.java)
        val senderName = sender?.name?.toString() ?: last?.getCharSequence("sender")?.toString().orEmpty()
        val me = extras.getParcelable(Notification.EXTRA_MESSAGING_PERSON, Person::class.java)
        val mine = (last != null && senderName.isBlank()) || (me != null && sender != null &&
            ((me.key != null && me.key == sender.key) || me.name?.toString() == sender.name?.toString()))
        val name = (if (group) conversation.ifBlank { title } else senderName.ifBlank { conversation }.ifBlank { title })
            .trim().take(80)
        Messages.replyAction(item)?.let { tokens.keep(app, chat, it) }
        synchronized(this) {
            chats.remove(item.key)
            chats[item.key] = Chat(app, chat, if (mine) chats[item.key]?.who ?: name else name)
            while (chats.size > MOST_KEYS) chats.remove(chats.keys.first())
        }
        if (mine || name.isEmpty()) return  // his own answer: the chat isn't named after him
        val facebook = if (app == MESSENGER && !group) facebookId(sender?.key) else ""
        val today = LocalDate.now().toString()
        val where = "$app|$chat"
        if (synchronized(this) { told[where] } == today) return
        if (Natro.person(if (app == INSTAGRAM) "instagram" else "messenger", name, chat, facebook, group)) {
            synchronized(this) { told[where] = today }
        }
    }

    /** The PC's Reply to a message whose notification has gone: its chat's kept Reply. What was done, or null. */
    fun replyKept(context: Context, key: String, text: String): String? {
        val chat = synchronized(this) { chats[key] } ?: return null
        val token = tokens.get(chat.app, chat.chat) ?: return null
        val sent = Messages.answer(context, token, text)
        if (!sent || chat.app == MESSENGER) tokens.drop(chat.app, chat.chat)
        return if (sent) "Sent to ${Text.short(chat.who, 60)} on ${LABELS[chat.app]}." else null
    }

    /**
     * The phone's send_message: after his yes, the brain's call with whom (from the address book) and what. screen
     * false: only the quiet ways (1 and 2), which never show anything; with none, it fails saying so, and the brain
     * sends it from the PC's hidden Edge instead, coming back here with screen true only if that can't.
     */
    fun send(context: Context, args: JSONObject): String {
        val app = APPS[args.optString("app")] ?: throw ToolFailure("The phone sends messages on Instagram and Messenger only.")
        val label = LABELS.getValue(app)
        val text = args.optString("text").trim()
        if (text.isEmpty()) throw ToolFailure("The message is empty.")
        val chat = args.optString("chat").trim()
        val facebook = args.optString("facebook").trim()
        val username = args.optString("username").trim().lowercase()
        val name = args.optString("name").trim()
        val shown = name.ifBlank { username }
        val group = args.optBoolean("group")
        val live = if (chat.isEmpty()) null else NotificationReader.current?.activeNotifications.orEmpty()
            .filter { it.packageName == app && chatId(it.notification.shortcutId) == chat }
            .sortedByDescending { it.postTime }.firstNotNullOfOrNull { Messages.replyAction(it) }
        val kept = if (chat.isEmpty()) null else tokens.get(app, chat)
        val shareId = if (app == INSTAGRAM) threadId(chat) else ""
        val linkTo = if (usernameOk(app, username)) username
            else if (app == MESSENGER && Regex("\\d{5,20}").matches(facebook)) facebook else ""
        val byLink = linkTo.isNotEmpty()
        val locked = context.getSystemService(KeyguardManager::class.java).isKeyguardLocked
        val screen = args.optBoolean("screen", true)
        val done = "Sent to $shown on $label."
        for (route in routes(live != null, kept != null, shareId.isNotEmpty(), byLink, locked, screen)) {
            when (route) {
                Route.NOTIFICATION -> if (Messages.answer(context, live!!, text)) {
                    if (app == MESSENGER) tokens.drop(app, chat)  // the same one-shot Reply
                    return done
                }
                Route.KEPT -> {
                    val sent = Messages.answer(context, kept!!, text)
                    if (!sent || app == MESSENGER) tokens.drop(app, chat)
                    if (sent) return done
                }
                Route.SHARE -> return onScreen(context) { share(it, app, shareId, text, done) }
                Route.LINK -> return onScreen(context) { byLink(it, app, linkTo, username, name, text, done) }
            }
        }
        // The brain matches these two starts (sending.py).
        if (!screen) throw ToolFailure("No quiet way on the phone: $shown's chat has no Reply here now.")
        if (locked && (shareId.isNotEmpty() || byLink)) throw ToolFailure("Your phone is locked: unlock it and tell me to send it.")
        throw ToolFailure("The phone has no way to reach $shown on $label now: ask him for their $label username.")
    }

    /**
     * Work on the screen, kept on meanwhile (the phone locking itself mid-way lost a share once); then back to what he
     * had open, so the app shows only for the few seconds it takes.
     */
    private fun <T> onScreen(context: Context, work: (ScreenControl) -> T): T {
        val screen = ScreenControl.current
            ?: throw ToolFailure("Screen control is off, so the phone can't open the chat: he can turn it on in " +
                "Settings > Accessibility > Natro.")
        val was = screen.rootInActiveWindow?.packageName?.toString().orEmpty()
        @Suppress("DEPRECATION")
        val awake = context.getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.SCREEN_DIM_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP, "natro:sending")
        awake.acquire(30_000)
        try {
            return work(screen)
        } finally {
            goBack(screen, was)
            if (awake.isHeld) awake.release()
        }
    }

    /** Back to the app he had open (its own task, as the launcher opens it), or the home screen. */
    private fun goBack(screen: ScreenControl, was: String) {
        if (was in LABELS) return  // he was in that app already
        val home = screen.packageManager.resolveActivity(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME), 0)
            ?.activityInfo?.packageName
        val open = if (was.isEmpty() || was == home || was == "com.android.systemui") null
            else screen.packageManager.getLaunchIntentForPackage(was)
        try {
            if (open != null) screen.startActivity(open.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            else screen.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME)
        } catch (e: RuntimeException) {  // going back is a nicety: never in the way of what was sent
            screen.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME)
        }
    }

    /** Route 3: the text shared into the chat by its id; Share tapped once the sheet shows exactly the text. */
    private fun share(screen: ScreenControl, app: String, id: String, text: String, done: String): String {
        screen.startActivity(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
            .putExtra(Intent.EXTRA_SHORTCUT_ID, id).setComponent(ComponentName(app, SHARE_SCREEN))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        val ids = SHARE_BUTTON
        val sheet = waitFor(screen, app) { nodes -> sendButton(nodes, null, ids) != null }
            ?: stop(screen, "The share sheet didn't show its Share button")
        if (sheet.none { (node, _) -> !node.editable && node.text.trim() == text }) {
            stop(screen, "The share sheet doesn't show the message as it should be")
        }
        val button = sendButton(sheet.map { it.first }, null, ids)!!
        screen.click(sheet[button].second)
        waitFor(screen, null, SCREEN_MILLIS) { nodes -> sendButton(nodes, null, ids) == null }
            ?: throw ToolFailure("I tapped Share, but the sheet is still open: he should check the chat on the phone. " +
                "Don't send it again.")
        return done
    }

    /** Route 4: the chat opened by its link, its title checked, the text typed, Send tapped and seen. */
    private fun byLink(screen: ScreenControl, app: String, who: String, username: String, name: String, text: String,
                       done: String): String {
        screen.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(link(app, who))).setPackage(app)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        val boxes = BOX[app].orEmpty()
        val sends = SEND[app].orEmpty()
        val whom = name.ifBlank { username }
        fun opened(nodes: List<Node>) = box(nodes, boxes) != null && title(app, nodes) != null
        waitFor(screen, app) { opened(it) } ?: stop(screen, "The chat didn't open")
        Thread.sleep(SETTLE_MILLIS)  // the link's first screen of the chat can be replaced by another right after
        val typed = Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text) }
        var ready: List<Pair<Node, AccessibilityNodeInfo>>? = null
        for (attempt in 1..2) {
            val chat = waitFor(screen, app) { opened(it) } ?: stop(screen, "The chat closed")
            val title = title(app, chat.map { it.first })!!
            if (!titleFits(title, name, username)) stop(screen, "The chat that opened is \"$title\", not $whom")
            val box = chat[box(chat.map { it.first }, boxes)!!].second
            box.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
            if (!box.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, typed)) continue
            ready = waitFor(screen, app, TYPED_MILLIS) { nodes ->
                val at = box(nodes, boxes)
                at != null && nodes[at].text == text && sendButton(nodes, nodes[at], sends) != null
            }
            if (ready != null) break
        }
        if (ready == null) {
            val nodes = waitFor(screen, app, 0) { true }?.map { it.first }.orEmpty()
            val at = box(nodes, boxes)
            stop(screen, when {
                at == null -> "The chat's box is gone"
                nodes[at].text != text -> "The box holds \"${Text.short(nodes[at].text, 60)}\" instead of the message"
                else -> "There isn't one Send button beside the box"
            })
        }
        val nodes = ready.map { it.first }
        val atBox = box(nodes, boxes)!!
        if (!titleFits(title(app, nodes).orEmpty(), name, username)) stop(screen, "The chat changed before Send")
        val send = sendButton(nodes, nodes[atBox], sends)!!
        val before = shows(nodes, text)  // the same words sent before
        screen.click(ready[send].second)
        waitFor(screen, app, SEEN_MILLIS) { now ->
            sent(now, box(now, boxes), text, before) && titleFits(title(app, now).orEmpty(), name, username)
        } ?: throw ToolFailure("I tapped Send, but don't see the message in the chat: he should check it on the " +
            "phone. Don't send it again.")
        return done
    }

    /** How many times the chat shows these words, not counting the box. */
    fun shows(nodes: List<Node>, text: String): Int =
        nodes.count { !it.editable && (it.text.trim() == text || it.label.contains(text)) }

    /**
     * Whether a message went, after Send: its words show once more in the chat (Instagram), or the box let go of
     * them (Messenger's own newest message is labelled with its time alone, so its words never show; seen 2026-10-23).
     */
    fun sent(nodes: List<Node>, box: Int?, text: String, before: Int): Boolean =
        shows(nodes, text) > before || (box != null && nodes[box].text.trim() != text)

    /** The chat's box: the editable element with one of its view ids, else the only editable one. */
    private fun box(nodes: List<Node>, ids: List<String>): Int? =
        nodes.indices.firstOrNull { nodes[it].editable && nodes[it].id in ids }
            ?: nodes.indices.filter { nodes[it].editable }.singleOrNull()

    private fun stop(screen: ScreenControl, why: String): Nothing {
        val now = try {
            screen.describe()
        } catch (e: ToolFailure) {
            "(it can't be read)"
        }
        throw ToolFailure("$why, so nothing was sent. Tell him so; never send it another way (not with the screen " +
            "tools either). The screen now:\n$now")
    }

    /**
     * The screen's elements once they pass check, read again and again until then (null if they never do). Read fresh
     * each time: screen control hears only of windows changing, so Android's copy of a screen it read before (a box's
     * text, a Send that has since appeared) would be stale.
     */
    private fun waitFor(
        screen: ScreenControl, app: String?, millis: Long = SCREEN_MILLIS,
        check: (List<Node>) -> Boolean,
    ): List<Pair<Node, AccessibilityNodeInfo>>? {
        val until = SystemClock.uptimeMillis() + millis
        while (true) {
            screen.clearCache()
            val root = screen.rootInActiveWindow
            if (root != null && (app == null || root.packageName == app)) {
                val read = read(root)
                if (check(read.map { it.first })) return read
            }
            if (SystemClock.uptimeMillis() > until) return null
            Thread.sleep(200)
        }
    }

    private fun read(root: AccessibilityNodeInfo): List<Pair<Node, AccessibilityNodeInfo>> {
        val found = mutableListOf<Pair<Node, AccessibilityNodeInfo>>()
        fun visit(node: AccessibilityNodeInfo) {
            if (found.size >= 400 || !node.isVisibleToUser) return
            val bounds = Rect().also(node::getBoundsInScreen)
            found += Node(node.viewIdResourceName.orEmpty().substringAfter(":id/"), node.text?.toString().orEmpty(),
                node.contentDescription?.toString().orEmpty(), node.isEditable, node.isEnabled, bounds.top,
                bounds.bottom) to node
            for (i in 0 until node.childCount) node.getChild(i)?.let(::visit)
        }
        visit(root)
        return found
    }
}

/**
 * Reply tokens kept per chat ("app|chat"), the newest of each. token(item) is what tells two apart (a PendingIntent
 * is equal to another for the same chat's Reply). An app whose tokens turn out shared between chats (two chats'
 * equal) has none kept from then on: a kept one could answer the wrong chat.
 */
class ReplyTokens<T>(private val most: Int = 100, private val token: (T) -> Any?) {
    private val kept = LinkedHashMap<String, T>()
    private val shared = HashSet<String>()

    @Synchronized
    fun keep(app: String, chat: String, item: T) {
        if (app in shared || chat.isEmpty()) return
        val mine = "$app|$chat"
        if (kept.any { (where, other) -> where.startsWith("$app|") && where != mine && token(other) == token(item) }) {
            shared += app
            kept.keys.removeAll { it.startsWith("$app|") }
            return
        }
        kept.remove(mine)
        kept[mine] = item
        while (kept.size > most) kept.remove(kept.keys.first())
    }

    @Synchronized
    fun get(app: String, chat: String): T? = if (app in shared) null else kept["$app|$chat"]

    @Synchronized
    fun drop(app: String, chat: String) {
        kept.remove("$app|$chat")
    }

    @Synchronized
    fun isShared(app: String) = app in shared
}
