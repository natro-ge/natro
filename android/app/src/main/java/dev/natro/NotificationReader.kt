package dev.natro

import android.app.Notification
import android.content.pm.PackageManager
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Reads the phone's notifications for "what did I miss?". The owner turns it on in Settings >
 * Notification access (the Natro app has a button). Everything it reads is personal: only Claude
 * gets it. New ones are matched here against what he asked Natro to watch for (NotificationWatch), and
 * only those that fit a rule go to the brain.
 */
class NotificationReader : NotificationListenerService() {
    override fun onNotificationPosted(item: StatusBarNotification) {
        val rules = NotificationWatch.rules
        if (rules.isEmpty() || item.isOngoing || item.packageName == packageName) return
        if (item.notification.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
        val (title, body) = parts(item)
        if (title.isBlank() && body.isBlank()) return
        val app = appName(item.packageName)
        val rule = NotificationWatch.match(rules, app, item.packageName, title, body) ?: return
        if (NotificationWatch.firstTime("${item.key}|$title|$body")) Natro.notificationMatched(rule.id, app, title, body)
    }

    override fun onListenerConnected() {
        current = this
    }

    override fun onListenerDisconnected() {
        if (current === this) current = null
    }

    /** The notifications on screen now, newest first, as text. */
    fun describe(limit: Int = 25): String {
        val shown = activeNotifications.orEmpty()
            .filter { !it.isOngoing && it.packageName != packageName && text(it).isNotBlank() }
            .sortedByDescending { it.postTime }
        if (shown.isEmpty()) return "No notifications."
        val clock = SimpleDateFormat("EEE HH:mm", Locale.US)
        val lines = shown.take(limit).map { "- ${clock.format(Date(it.postTime))} ${appName(it.packageName)}: ${text(it)}" }
        val more = if (shown.size > limit) "\n(and ${shown.size - limit} more)" else ""
        return lines.joinToString("\n") + more
    }

    private fun text(item: StatusBarNotification): String {
        val (title, body) = parts(item)
        return Text.short(listOf(title, body).filter { it.isNotBlank() }.joinToString(": "), 300)
    }

    /** A notification's title, and its text (with the lines of an inbox-style one). */
    private fun parts(item: StatusBarNotification): Pair<String, String> {
        val extras = item.notification.extras
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val body = (extras.getCharSequence(Notification.EXTRA_BIG_TEXT) ?: extras.getCharSequence(Notification.EXTRA_TEXT))
            ?.toString().orEmpty()
        val lines = extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)?.joinToString(" / ").orEmpty()
        return title to listOf(body, lines).filter { it.isNotBlank() }.joinToString(": ")
    }

    private fun appName(pkg: String): String = try {
        packageManager.getApplicationLabel(packageManager.getApplicationInfo(pkg, 0)).toString()
    } catch (e: PackageManager.NameNotFoundException) {
        pkg
    }

    companion object {
        @Volatile var current: NotificationReader? = null
    }
}
