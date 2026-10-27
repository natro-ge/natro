package dev.natro

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat

/**
 * A background job is done (server/natro_agent/jobs.py): a notification with what Natro found, whichever device says
 * it out loud, under the routine's name when it's a routine's run (title). A tap opens the chat, where the job's
 * entry has her steps too.
 */
object JobNotices {
    private const val CHANNEL = "jobs"

    fun show(context: Context, id: String, text: String, ok: Boolean, title: String = "") {
        val notifications = context.getSystemService(NotificationManager::class.java)
        notifications.createNotificationChannel(
            NotificationChannel(CHANNEL, "Background jobs", NotificationManager.IMPORTANCE_HIGH))
        val open = PendingIntent.getActivity(context, id.hashCode(),
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE)
        val notice = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_natro)
            .setContentTitle(when {
                ok -> title.ifBlank { "Done in the background" }
                title.isNotBlank() -> "A routine failed"
                else -> "A background job failed"
            })
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        try {
            notifications.notify(id, 0, notice)
        } catch (e: SecurityException) {  // notifications not allowed: the chat still shows it
        }
    }
}
