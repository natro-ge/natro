package dev.natro

import android.app.KeyguardManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.PowerManager
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.core.app.NotificationCompat
import org.json.JSONObject
import java.io.File

/**
 * His cards (server/natro_agent/cards.py): a voucher, loyalty card or ticket, shown full screen and bright for scanning
 * at the till. A copy of each card a place names stays here, so it shows with no signal in the shop. At a place it opens
 * by itself only with the phone awake and unlocked; otherwise a notification opens it, so nothing lights up in a pocket.
 * The messages are in shared/PROTOCOL.md (card, card_get).
 */
object Cards {
    private const val CHANNEL = "cards"
    private lateinit var folder: File

    fun init(context: Context) {
        folder = File(context.filesDir, "cards").apply { mkdirs() }
    }

    private fun picture(id: String) = File(folder, id.replace(Regex("[^A-Za-z0-9_-]"), "_"))

    /** The brain sent a card: kept, and (asked for now) shown. */
    fun came(context: Context, message: JSONObject, bytes: ByteArray) {
        val id = message.optString("id")
        if (id.isBlank()) return
        picture(id).writeBytes(bytes)
        if (message.optBoolean("show")) {
            open(context, id, message.optString("name"), message.optString("ended"), asked = true)
        }
    }

    /** The places' cards now: copies of what's missing are asked for, and those no place names go. */
    fun keepOnly(ids: Set<String>) {
        for (file in folder.listFiles().orEmpty()) {
            if (file.name !in ids.map { picture(it).name }) file.delete()
        }
        for (id in ids) {
            if (!picture(id).exists()) Natro.sendMessage(JSONObject().put("type", "card_get").put("id", id))
        }
    }

    /** He got to a place (or left it) that shows this card. */
    fun arrived(context: Context, card: PlaceCard) {
        if (picture(card.card).exists()) {
            open(context, card.card, card.name, "", asked = false)
        } else {  // not here yet, so it needs a connection after all
            Natro.sendMessage(JSONObject().put("type", "card_get").put("id", card.card).put("show", true))
        }
    }

    private fun open(context: Context, id: String, name: String, ended: String, asked: Boolean) {
        val intent = Intent(context, CardActivity::class.java).putExtra("id", id).putExtra("name", name)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val title = "Your $name card"
        val notifications = context.getSystemService(NotificationManager::class.java)
        notifications.createNotificationChannel(NotificationChannel(CHANNEL, "Cards", NotificationManager.IMPORTANCE_HIGH))
        val notice = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_natro)
            .setContentTitle(title)
            .setContentText(if (ended.isBlank()) "Tap to show it." else "It ended on $ended. Tap to show it.")
            .setCategory(NotificationCompat.CATEGORY_RECOMMENDATION)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setContentIntent(PendingIntent.getActivity(context, id.hashCode(), intent,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            .setAutoCancel(true)
            .build()
        try {
            notifications.notify(id, 0, notice)
        } catch (e: SecurityException) {  // notifications not allowed
        }
        val awake = context.getSystemService(PowerManager::class.java).isInteractive
        val locked = context.getSystemService(KeyguardManager::class.java).isKeyguardLocked
        if (asked || (awake && !locked)) {
            try {
                context.startActivity(intent)
            } catch (e: Exception) {  // Android won't open it from here: the notification does
            }
        }
    }

    fun bytes(id: String): ByteArray? = runCatching { picture(id).readBytes() }.getOrNull()

    fun cancelNotice(context: Context, id: String) {
        context.getSystemService(NotificationManager::class.java).cancel(id, 0)
    }
}

/** A card, the whole screen at full brightness, over the lock screen, staying on while it's shown. */
class CardActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val id = intent.getStringExtra("id") ?: return finish()
        val bytes = Cards.bytes(id) ?: return finish()
        Cards.cancelNotice(this, id)
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.attributes = window.attributes.apply { screenBrightness = WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_FULL }
        setContent {
            NatroTheme { PictureScreen(bytes) { finish() } }
        }
    }
}
