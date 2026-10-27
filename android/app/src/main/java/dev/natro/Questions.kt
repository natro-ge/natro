package dev.natro

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import java.util.concurrent.ConcurrentHashMap

/**
 * Natro's question on the PC, before something risky ("I'll force close Spotify."), asked here too
 * (server/natro_agent/server.py's question_elsewhere): a notification with Yes and No, so he can answer it away
 * from the PC. Yes asks him to unlock the phone first; No doesn't. It goes once the question is over: answered
 * here, on the PC or in the chat, or she stopped waiting. The chat shows it too, with its own Yes and No.
 */
object QuestionNotices {
    private const val CHANNEL = "questions"
    /** In case the word that it's over never comes (the connection went): the brain waits a minute. */
    private const val LONGEST_MILLIS = 5 * 60_000L
    /** "Natro is offline" stays this long. */
    private const val WORD_MILLIS = 6_000L
    private val shown = ConcurrentHashMap.newKeySet<String>()

    fun show(context: Context, device: String, id: String, label: String, question: String) {
        val notifications = channel(context)
        val tag = tag(device, id)
        val yes = NotificationCompat.Action.Builder(0, "Yes", action(context, device, id, QuestionAction.YES))
            .setAuthenticationRequired(true)
            .build()
        val notice = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_natro)
            .setContentTitle("Natro asks on your $label")
            .setContentText(question)
            .setStyle(NotificationCompat.BigTextStyle().bigText(question))
            .setContentIntent(open(context, tag))
            .addAction(0, "No", action(context, device, id, QuestionAction.NO))
            .addAction(yes)
            .setTimeoutAfter(LONGEST_MILLIS)
            .build()
        try {
            notifications.notify(tag, 0, notice)
            shown += tag
        } catch (e: SecurityException) {  // notifications not allowed: the chat still asks
        }
    }

    /** The answer couldn't go: Natro is offline. */
    fun offline(context: Context, device: String, id: String) {
        val tag = tag(device, id)
        val notice = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_natro)
            .setContentTitle("Natro is offline, so your answer didn't go.")
            .setContentIntent(open(context, tag))
            .setSilent(true)
            .setTimeoutAfter(WORD_MILLIS)
            .setAutoCancel(true)
            .build()
        try {
            channel(context).notify(tag, 0, notice)
        } catch (e: SecurityException) {
        }
    }

    fun cancel(context: Context, device: String, id: String) {
        val tag = tag(device, id)
        shown -= tag
        context.getSystemService(NotificationManager::class.java).cancel(tag, 0)
    }

    /** The connection went: these can't be answered now. The brain asks again those still waiting when it's back. */
    fun cancelAll(context: Context) {
        val notifications = context.getSystemService(NotificationManager::class.java)
        shown.toList().forEach {
            shown -= it
            notifications.cancel(it, 0)
        }
    }

    private fun tag(device: String, id: String) = "question:$device:$id"

    private fun channel(context: Context): NotificationManager {
        val notifications = context.getSystemService(NotificationManager::class.java)
        notifications.createNotificationChannel(
            NotificationChannel(CHANNEL, "Questions from the PC", NotificationManager.IMPORTANCE_HIGH))
        return notifications
    }

    private fun open(context: Context, tag: String): PendingIntent {
        val intent = Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return PendingIntent.getActivity(context, tag.hashCode(), intent, PendingIntent.FLAG_IMMUTABLE)
    }

    private fun action(context: Context, device: String, id: String, what: String): PendingIntent {
        val intent = Intent(context, QuestionAction::class.java).setAction(what).putExtra("device", device)
            .putExtra("id", id)
        return PendingIntent.getBroadcast(context, (what + tag(device, id)).hashCode(), intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }
}

/** The notification's Yes or No. */
class QuestionAction : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val device = intent.getStringExtra("device") ?: return
        val id = intent.getStringExtra("id") ?: return
        Natro.init(context)
        Natro.answerElsewhere(context, device, id, yes = intent.action == YES)
    }

    companion object {
        const val YES = "dev.natro.QUESTION_YES"
        const val NO = "dev.natro.QUESTION_NO"
    }
}
