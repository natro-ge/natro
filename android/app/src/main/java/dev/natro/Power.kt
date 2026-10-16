package dev.natro

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService

/**
 * Natro on or off, for the whole app. Off, she doesn't connect, listen or show the bubble, and her
 * notification goes; the assistant gesture only shows "Natro is off". The choice is remembered,
 * across restarts too.
 *
 * Android keeps the services the owner switched on in its settings bound (the assistant, screen
 * control, notification access) even then, but they do nothing unless Natro's brain asks.
 */
object Power {
    /** False if Natro was turned on but Android didn't let her service start from here (the app starts it when opened). */
    fun set(context: Context, on: Boolean): Boolean {
        Prefs.setPower(on)
        val started = if (on) NatroService.start(context) else {
            Natro.cancel()
            NatroService.stop(context)
            true
        }
        TileService.requestListeningState(context, ComponentName(context, PowerTile::class.java))
        return started
    }
}

/** The on/off tile in the notification shade's quick settings. */
class PowerTile : TileService() {
    override fun onStartListening() = show()

    override fun onClick() {
        // Android may not let the service start from here: then the app opens, which starts it.
        if (!Power.set(this, !Prefs.power.value)) {
            startActivityAndCollapse(PendingIntent.getActivity(this, 0,
                Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE))
        }
        show()
    }

    private fun show() {
        val tile = qsTile ?: return
        val on = Prefs.power.value
        tile.state = if (on) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        tile.label = "Natro"
        tile.subtitle = if (on) "On" else "Off"
        tile.updateTile()
    }
}

/** Starts Natro after the phone restarts, or after the app is updated, if she's on. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            if (Prefs.power.value) NatroService.start(context)
        }
    }
}
