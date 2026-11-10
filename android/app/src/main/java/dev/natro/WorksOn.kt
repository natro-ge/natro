package dev.natro

import android.content.ComponentName
import android.content.Context
import android.graphics.drawable.Icon
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import org.json.JSONObject

/**
 * Where Natro works for what's asked on the phone (server/natro_agent/works_on.py): here, or on the PC.
 * The chat's chip and the quick settings tile change it, and so does asking, here or on the PC ("work on
 * the PC", "make the phone work on the PC"); while it's the PC, the bubble's label shows a badge. It's
 * remembered (Prefs). The brain hears it in the hello and whenever it's changed here, and says when it
 * changed it, and which of the owner's devices are online: set to the PC while the PC is offline, Natro works
 * here, and the chip, the badge and the status line say so.
 */
object WorksOn {
    const val PHONE = "phone"
    const val PC = "pc"

    /** One of the two, or null for anything else. */
    fun named(name: String?): String? = name?.takeIf { it == PHONE || it == PC }

    /** Changed here (the chip, the tile): remembered and told to the brain (offline, the next hello tells it). */
    fun set(context: Context, device: String) {
        if (Prefs.worksOn.value == device) return
        Prefs.setWorksOn(device)
        Natro.sendMessage(JSONObject().put("type", "works_on").put("device", device))
        refreshTile(context)
    }

    /** The brain's works_on: set by asking, or the phone's own change said back. */
    fun taken(context: Context, device: String) {
        if (Prefs.worksOn.value == device) return
        Prefs.setWorksOn(device)
        refreshTile(context)
    }

    /** The status line's words after "Online", or null while Natro works here. */
    fun status(worksOn: String, pcOnline: Boolean): String? = when {
        worksOn != PC -> null
        pcOnline -> "works on your PC"
        else -> "PC offline, so works here"
    }

    private fun refreshTile(context: Context) =
        TileService.requestListeningState(context, ComponentName(context, WorksOnTile::class.java))
}

/** Where Natro works, in the notification shade's quick settings: a tap moves her to the other device. */
class WorksOnTile : TileService() {
    override fun onStartListening() = show()

    override fun onClick() {
        WorksOn.set(this, if (Prefs.worksOn.value == WorksOn.PC) WorksOn.PHONE else WorksOn.PC)
        show()
    }

    private fun show() {
        val tile = qsTile ?: return
        val onPc = Prefs.worksOn.value == WorksOn.PC
        tile.state = if (onPc) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        tile.label = "Natro works on"
        tile.subtitle = if (onPc) "Your PC" else "This phone"
        tile.icon = Icon.createWithResource(this, if (onPc) R.drawable.ic_tile_pc else R.drawable.ic_tile_phone)
        tile.updateTile()
    }
}
