package dev.natro

import android.app.Notification
import android.app.PendingIntent
import android.app.Person
import android.app.RemoteInput
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.service.notification.StatusBarNotification

/**
 * Messages that come to the phone (WhatsApp, Messenger, Telegram, texts...), shown on the PC and answered from there
 * (agent/natro_agent/server.py: phone_message, message_reply), while "Show messages on the PC" is on. A message is a
 * notification of the kind "message", or one with a box to answer in. Only the newest message of a conversation
 * goes, once; his own answer, which the app adds to its notification, never goes back. An answer is typed into the
 * notification's own reply box, so it goes from that app as if he'd answered there.
 */
object Messages {
    /** Notification key -> the last message sent for it ("who|what"): one updated in place comes again. */
    private val sent = LinkedHashMap<String, String>()
    private const val MOST = 50
    /** What he answered lately: the app's notification shows it next, as the newest message. */
    private val answered = ArrayDeque<String>()
    private const val ANSWERS = 10

    fun isMessage(item: StatusBarNotification): Boolean {
        val notification = item.notification
        return notification.category == Notification.CATEGORY_MESSAGE ||
            notification.extras.containsKey(Notification.EXTRA_MESSAGES) || replyAction(item) != null
    }

    /** The notification's way to answer in a box (its "Reply"), or null. */
    fun replyAction(item: StatusBarNotification): Notification.Action? {
        val replies = item.notification.actions.orEmpty().filter { action ->
            action.remoteInputs.orEmpty().any { it.allowFreeFormInput }
        }
        return replies.firstOrNull { it.semanticAction == Notification.Action.SEMANTIC_ACTION_REPLY } ?: replies.firstOrNull()
    }

    /**
     * (who, what) of the newest message: who it's from as the notification shows it ("Nino", or "Nino in Family" in a
     * group), and its words; or null when the newest is his own. title and text: the notification's.
     */
    fun latest(item: StatusBarNotification, title: String, text: String): Pair<String, String>? {
        val extras = item.notification.extras
        val last = extras.getParcelableArray(Notification.EXTRA_MESSAGES, Bundle::class.java)?.lastOrNull()
        val words = last?.getCharSequence("text")?.toString()?.takeIf { it.isNotBlank() } ?: text
        val sender = last?.getCharSequence("sender")?.toString().orEmpty()
            .ifBlank { last?.getParcelable("sender_person", Person::class.java)?.name?.toString().orEmpty() }
        if (last != null && sender.isBlank()) return null  // a message with no sender is his own
        val conversation = extras.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)?.toString().orEmpty()
        return Text.short(who(title, conversation, sender), 80) to Text.short(words, 400)
    }

    /** Who a message is from, as he'd say it. */
    fun who(title: String, conversation: String, sender: String): String = when {
        conversation.isNotBlank() && sender.isNotBlank() && !conversation.equals(sender, ignoreCase = true) ->
            "$sender in $conversation"
        conversation.isNotBlank() -> conversation
        sender.isNotBlank() -> sender
        else -> title
    }

    /** True the first time this message is seen, unless it's what he just answered. */
    @Synchronized
    fun firstTime(key: String, who: String, words: String): Boolean {
        val said = "$who|$words"
        if (sent[key] == said || words.trim() in answered) return false
        sent.remove(key)
        sent[key] = said
        while (sent.size > MOST) sent.remove(sent.keys.first())
        return true
    }

    /** The notification went (read, answered or dismissed): true if a message from it was sent. */
    @Synchronized
    fun forget(key: String): Boolean = sent.remove(key) != null

    @Synchronized
    private fun remember(answer: String) {
        answered.addLast(answer.trim())
        if (answered.size > ANSWERS) answered.removeFirst()
    }

    /** Whether a notification is from whom he named (part of the name, Georgian or Latin letters) and the app (if he
     * named one: part of its name or package). */
    fun fits(shown: String, appName: String, pkg: String, to: String, app: String): Boolean {
        val name = Text.latin(to.trim())
        return (name.isEmpty() || Text.latin(shown).contains(name)) &&
            (app.isBlank() || appName.contains(app.trim(), ignoreCase = true) || pkg.contains(app.trim(), ignoreCase = true))
    }

    /**
     * Answer a message in its own app, through its notification's reply box. key: the notification's (the PC's
     * Reply), or blank: then the newest message from `to` (and `app`, if given). Returns what was done.
     */
    fun reply(context: Context, key: String, to: String, app: String, text: String): String {
        val reader = NotificationReader.current
            ?: throw ToolFailure("Natro can't see the phone's notifications: he can allow notification access in the Natro app.")
        if (text.isBlank()) throw ToolFailure("Say what to answer.")
        val open = reader.activeNotifications.orEmpty()
            .filter { it.packageName != context.packageName && replyAction(it) != null }
            .sortedByDescending { it.postTime }
        val item = if (key.isNotBlank()) {
            open.firstOrNull { it.key == key } ?: throw ToolFailure("That message's notification is gone from the " +
                "phone (it was read or dismissed), so it can't be answered from there.")
        } else {
            open.firstOrNull { fits(shownAs(it), reader.appName(it.packageName), it.packageName, to, app) }
                ?: throw ToolFailure("There's no message from \"$to\" on the phone that can be answered from its " +
                    "notification (it may have been read). Its notifications now:\n${reader.describe(10)}")
        }
        val action = replyAction(item)!!
        val inputs = action.remoteInputs.orEmpty()
        val results = Bundle().apply { inputs.filter { it.allowFreeFormInput }.forEach { putCharSequence(it.resultKey, text) } }
        val intent = Intent().addFlags(Intent.FLAG_RECEIVER_FOREGROUND)
        RemoteInput.addResultsToIntent(inputs, intent, results)
        RemoteInput.setResultsSource(intent, RemoteInput.SOURCE_FREE_FORM_INPUT)
        remember(text)
        try {
            action.actionIntent.send(context, 0, intent)
        } catch (e: PendingIntent.CanceledException) {
            throw ToolFailure("${reader.appName(item.packageName)} doesn't take answers from that notification any more.")
        }
        return "Sent to ${Text.short(shownAs(item), 60)} on ${reader.appName(item.packageName)}."
    }

    /** Who a notification is from, as it shows. */
    private fun shownAs(item: StatusBarNotification): String {
        val extras = item.notification.extras
        val conversation = extras.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)?.toString().orEmpty()
        return conversation.ifBlank { extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty() }
    }
}
