package dev.natro

import kotlin.math.abs
import kotlin.math.hypot

/** Where the bubble goes when it's let go. */
sealed interface Landing {
    /** Into the ✕ at the bottom: the request is cancelled and the bubble goes. */
    data object Dismiss : Landing
    /** Back to the bottom center, where it first shows. It goes away once Natro is done. */
    data object Home : Landing
    /** Parked on a side edge (center at y): it stays there, also when Natro is done. */
    data class Edge(val right: Boolean, val y: Float) : Landing
}

/**
 * The screen as the bubble sees it, in pixels: its size, the system bars at the top and bottom, and
 * one dp. Home is centered about an eighth of the screen up from the bottom, as in the plan's
 * drawing; the ✕ shows below it, just above the navigation bar, so pulling the bubble down a little
 * drops it in.
 */
class Field(val width: Float, val height: Float, val top: Float, val bottom: Float, val dp: Float) {
    val radius get() = Snap.BUBBLE_DP / 2 * dp
    val homeX get() = width / 2
    val homeY get() = height - maxOf(Snap.HOME_DP * dp, height * Snap.HOME_SHARE)
    val targetX get() = width / 2
    val targetY get() = height - bottom - Snap.TARGET_DP * dp

    fun edgeX(right: Boolean) = if (right) width - Snap.EDGE_DP * dp - radius else Snap.EDGE_DP * dp + radius

    /** A center height on an edge, clear of the status bar and the navigation bar. */
    fun edgeY(y: Float): Float {
        val highest = top + radius + Snap.MARGIN_DP * dp
        return y.coerceIn(highest, maxOf(highest, height - bottom - radius - Snap.MARGIN_DP * dp))
    }
}

object Snap {
    const val BUBBLE_DP = 64f
    const val HOME_DP = 132f
    const val HOME_SHARE = 0.13f
    const val TARGET_DP = 40f
    const val EDGE_DP = 10f
    const val MARGIN_DP = 12f
    /** Let go this close to home, it goes home. */
    const val HOME_PULL_DP = 96f
    /** This close to the ✕, the bubble jumps onto it. */
    const val MAGNET_DP = 52f
    /** Flung down at least this fast (dp a second), and more down than sideways, it's thrown away. */
    const val FLING_AWAY_DP = 2400f
    /** How far a fling carries the bubble: its speed for this long. */
    const val GLIDE_SECONDS = 0.12f

    /** Whether a bubble centered at (x, y) is over the ✕. */
    fun onTarget(x: Float, y: Float, field: Field) = hypot(x - field.targetX, y - field.targetY) < MAGNET_DP * field.dp

    /** Where the bubble lands, let go centered at (x, y) moving at (vx, vy) pixels a second. */
    fun landing(x: Float, y: Float, vx: Float, vy: Float, field: Field): Landing {
        if (onTarget(x, y, field)) return Landing.Dismiss
        if (vy > FLING_AWAY_DP * field.dp && vy > 2 * abs(vx)) return Landing.Dismiss
        val toX = x + vx * GLIDE_SECONDS
        val toY = y + vy * GLIDE_SECONDS
        if (hypot(toX - field.homeX, toY - field.homeY) < HOME_PULL_DP * field.dp) return Landing.Home
        return Landing.Edge(toX > field.width / 2, field.edgeY(toY))
    }
}
