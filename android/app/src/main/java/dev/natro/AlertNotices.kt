package dev.natro

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat

/**
 * Something a watcher saw (agent/natro_agent/watchers.py): "Nino emailed: Dinner on Friday." A notification, in a
 * louder channel when it's urgent (a meeting's heads-up, or one he asked to hear right away). It also shows in the
 * shared chat. A tap opens the chat.
 */
object AlertNotices {
    private const val CHANNEL = "alerts"
    private const val URGENT = "alerts_urgent"

    fun show(context: Context, id: String, text: String, urgent: Boolean) {
        val notifications = context.getSystemService(NotificationManager::class.java)
        notifications.createNotificationChannel(
            NotificationChannel(CHANNEL, "Heads-ups", NotificationManager.IMPORTANCE_DEFAULT))
        notifications.createNotificationChannel(
            NotificationChannel(URGENT, "Urgent heads-ups", NotificationManager.IMPORTANCE_HIGH))
        val open = PendingIntent.getActivity(context, id.hashCode(),
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE)
        val notice = NotificationCompat.Builder(context, if (urgent) URGENT else CHANNEL)
            .setSmallIcon(R.drawable.ic_natro)
            .setContentTitle("Heads-up")
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setCategory(if (urgent) NotificationCompat.CATEGORY_EVENT else NotificationCompat.CATEGORY_STATUS)
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        try {
            notifications.notify(id, 0, notice)
        } catch (e: SecurityException) {  // notifications not allowed: the chat still shows it
        }
    }
}
