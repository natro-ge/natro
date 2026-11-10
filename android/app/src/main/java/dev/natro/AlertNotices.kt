package dev.natro

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import org.json.JSONArray

/**
 * Something a watcher saw (server/natro_agent/watchers.py): "Nino emailed: Dinner on Friday." A notification, in a
 * louder channel when it's urgent (a meeting's heads-up, or one he asked to hear right away). It also shows in the
 * shared chat. A tap opens the chat; one he can answer ("On my way") has a button for each answer.
 */
object AlertNotices {
    private const val CHANNEL = "alerts"
    private const val URGENT = "alerts_urgent"
    /** A word about his answer stays this long. */
    private const val WORD_MILLIS = 6_000L

    /** What the brain said to his answer, in place of the alert for a moment. */
    fun answered(context: Context, id: String, text: String) {
        val notifications = context.getSystemService(NotificationManager::class.java)
        notifications.createNotificationChannel(
            NotificationChannel(CHANNEL, "Heads-ups", NotificationManager.IMPORTANCE_DEFAULT))
        val notice = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_natro)
            .setContentTitle(text)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setTimeoutAfter(WORD_MILLIS)
            .setAutoCancel(true)
            .build()
        try {
            notifications.notify(id, 0, notice)
        } catch (e: SecurityException) {  // notifications not allowed
        }
    }

    fun show(context: Context, id: String, text: String, urgent: Boolean, answers: JSONArray? = null) {
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
        for (i in 0 until (answers?.length() ?: 0)) {
            val answer = answers!!.optJSONObject(i) ?: continue
            val intent = Intent(context, AlertAction::class.java).putExtra("id", id).putExtra("answer", answer.optString("id"))
            notice.addAction(0, answer.optString("text"), PendingIntent.getBroadcast(context, (id + i).hashCode(), intent,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
        }
        try {
            notifications.notify(id, 0, notice.build())
        } catch (e: SecurityException) {  // notifications not allowed: the chat still shows it
        }
    }
}

/** A button on the notification ("On my way"). */
class AlertAction : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getStringExtra("id") ?: return
        Natro.init(context)
        Natro.answerAlert(context, id, intent.getStringExtra("answer") ?: return)
    }
}
