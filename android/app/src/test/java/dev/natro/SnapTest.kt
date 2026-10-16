package dev.natro

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SnapTest {
    // The Nothing Phone (2): 1080 x 2412 pixels, 2.625 pixels a dp, gesture navigation.
    private val dp = 2.625f
    private val field = Field(1080f, 2412f, top = 100f, bottom = 63f, dp = dp)

    private fun landing(x: Float, y: Float, vx: Float = 0f, vy: Float = 0f) = Snap.landing(x, y, vx, vy, field)

    @Test
    fun homeIsCenteredNearTheBottomAboveTheCross() {
        assertEquals(540f, field.homeX)
        assertTrue(field.homeY < field.targetY)
        // Pulling down from home reaches the ✕ soon, but home itself isn't on it.
        assertFalse(Snap.onTarget(field.homeX, field.homeY, field))
        assertTrue(Snap.onTarget(field.homeX, field.homeY + 30 * dp, field))
    }

    @Test
    fun droppedNearHomeItGoesHome() {
        assertEquals(Landing.Home, landing(field.homeX + 40 * dp, field.homeY - 50 * dp))
    }

    @Test
    fun droppedElsewhereItParksOnTheNearerEdge() {
        assertEquals(Landing.Edge(right = false, y = 900f), landing(300f, 900f))
        assertEquals(Landing.Edge(right = true, y = 900f), landing(700f, 900f))
    }

    @Test
    fun aFlingCarriesItToTheOtherSide() {
        val landed = landing(450f, 900f, vx = 3000f)
        assertTrue(landed is Landing.Edge && landed.right)
    }

    @Test
    fun droppedOnTheCrossOrFlungDownItsDismissed() {
        assertEquals(Landing.Dismiss, landing(field.targetX, field.targetY))
        assertEquals(Landing.Dismiss, landing(800f, 600f, vx = 500f, vy = 3000 * dp))
        // Fast, but more sideways than down: it parks.
        assertTrue(landing(500f, 600f, vx = 9000 * dp, vy = 3000 * dp) is Landing.Edge)
    }

    @Test
    fun parkedClearOfTheBars() {
        val high = landing(100f, 0f) as Landing.Edge
        val low = landing(100f, 2412f) as Landing.Edge
        assertEquals(100f + field.radius + Snap.MARGIN_DP * dp, high.y)
        assertEquals(2412f - 63f - field.radius - Snap.MARGIN_DP * dp, low.y)
    }
}
