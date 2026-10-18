package dev.natro

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat

/**
 * A request he made on the PC while the phone was offline, kept for it by the brain (agent/natro_agent/pending.py).
 * When the phone connects, each shows as a notification with Run and Skip, and as a card in the chat, until he
 * answers one way or the other.
 */
data class Kept(val id: String, val text: String, val from: String, val time: Long)

object KeptNotices {
    private const val CHANNEL = "kept"

    fun show(context: Context, kept: Kept) {
        val notifications = context.getSystemService(NotificationManager::class.java)
        notifications.createNotificationChannel(
            NotificationChannel(CHANNEL, "Requests from the PC", NotificationManager.IMPORTANCE_HIGH))
        val open = Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val notice = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_natro)
            .setContentTitle("While your phone was offline")
            .setContentText("You asked on your ${kept.from}: ${kept.text}")
            .setStyle(NotificationCompat.BigTextStyle().bigText("You asked on your ${kept.from}: ${kept.text}"))
            .setContentIntent(PendingIntent.getActivity(context, kept.id.hashCode(), open, PendingIntent.FLAG_IMMUTABLE))
            .addAction(0, "Skip", action(context, kept.id, KeptAction.SKIP))
            .addAction(0, "Run", action(context, kept.id, KeptAction.RUN))
            .setAutoCancel(true)
            .build()
        try {
            notifications.notify(kept.id, 0, notice)
        } catch (e: SecurityException) {  // notifications not allowed: the card in the chat still asks
        }
    }

    fun cancel(context: Context, id: String) {
        context.getSystemService(NotificationManager::class.java).cancel(id, 0)
    }

    private fun action(context: Context, id: String, what: String): PendingIntent {
        val intent = Intent(context, KeptAction::class.java).setAction(what).putExtra("id", id)
        return PendingIntent.getBroadcast(context, (what + id).hashCode(), intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }
}

/** The notification's Run or Skip. */
class KeptAction : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getStringExtra("id") ?: return
        Natro.init(context)
        Natro.runKept(id, run = intent.action == RUN)
    }

    companion object {
        const val RUN = "dev.natro.KEPT_RUN"
        const val SKIP = "dev.natro.KEPT_SKIP"
    }
}
