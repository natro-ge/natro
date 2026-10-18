package dev.natro

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat

/**
 * A reminder going off (agent/natro_agent/reminders.py): a notification, whichever device says it out loud, with
 * Snooze (it comes back in 10 minutes, from the brain) and Done. It also shows in the shared chat.
 */
object ReminderNotices {
    private const val CHANNEL = "reminders"
    /** A word about a snooze ("Snoozed until 15:10.") stays this long. */
    private const val WORD_MILLIS = 6_000L

    fun show(context: Context, id: String, text: String) {
        val notifications = channel(context)
        val notice = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_natro)
            .setContentTitle("Reminder")
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setContentIntent(open(context, id))
            .addAction(0, "Done", action(context, id, ReminderAction.DONE))
            .addAction(0, "Snooze 10 min", action(context, id, ReminderAction.SNOOZE))
            .setAutoCancel(true)
            .build()
        notify(notifications, id, notice)
    }

    /** What the brain said to a snooze, in place of the reminder for a moment. */
    fun snoozed(context: Context, id: String, text: String) {
        val notifications = channel(context)
        val notice = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_natro)
            .setContentTitle(text)
            .setContentIntent(open(context, id))
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setTimeoutAfter(WORD_MILLIS)
            .setAutoCancel(true)
            .build()
        notify(notifications, id, notice)
    }

    fun cancel(context: Context, id: String) {
        context.getSystemService(NotificationManager::class.java).cancel(id, 0)
    }

    private fun channel(context: Context): NotificationManager {
        val notifications = context.getSystemService(NotificationManager::class.java)
        notifications.createNotificationChannel(
            NotificationChannel(CHANNEL, "Reminders", NotificationManager.IMPORTANCE_HIGH))
        return notifications
    }

    private fun notify(notifications: NotificationManager, id: String, notice: android.app.Notification) {
        try {
            notifications.notify(id, 0, notice)
        } catch (e: SecurityException) {  // notifications not allowed: the chat still shows it
        }
    }

    private fun open(context: Context, id: String): PendingIntent {
        val intent = Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return PendingIntent.getActivity(context, id.hashCode(), intent, PendingIntent.FLAG_IMMUTABLE)
    }

    private fun action(context: Context, id: String, what: String): PendingIntent {
        val intent = Intent(context, ReminderAction::class.java).setAction(what).putExtra("id", id)
        return PendingIntent.getBroadcast(context, (what + id).hashCode(), intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }
}

/** The notification's Snooze or Done. */
class ReminderAction : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getStringExtra("id") ?: return
        Natro.init(context)
        when (intent.action) {
            SNOOZE -> Natro.snooze(context, id)
            else -> ReminderNotices.cancel(context, id)
        }
    }

    companion object {
        const val SNOOZE = "dev.natro.REMINDER_SNOOZE"
        const val DONE = "dev.natro.REMINDER_DONE"
    }
}
