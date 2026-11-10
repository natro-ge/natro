package dev.natro

import org.junit.Assert.assertEquals
import org.junit.Test

/** Zooming a picture in the viewer: a 1000 × 2000 screen and a picture as wide as it, fitted with room above and below. */
class PictureViewerTest {
    private val zoom = PictureZoom(1000f, 2000f, 2000f, 1000f) // fitted: 1000 × 500

    private fun assertSpot(expected: Spot, actual: Spot) {
        assertEquals(expected.zoom, actual.zoom, 0.001f)
        assertEquals(expected.x, actual.x, 0.01f)
        assertEquals(expected.y, actual.y, 0.01f)
    }

    @Test
    fun fittedItStaysPut() {
        assertSpot(Spot(), zoom.moved(Spot(), 1f, 0f, 0f, 300f, -200f))
        assertSpot(Spot(), zoom.moved(Spot(), 0.5f, 100f, 100f, 0f, 0f)) // no smaller than fitted
    }

    @Test
    fun aPinchKeepsThePointUnderTheFingers() {
        // Twice as big around a point 200 right of the middle: the picture's point that was there is still there.
        val spot = zoom.moved(Spot(), 2f, 200f, 0f, 0f, 0f)
        assertSpot(Spot(2f, -200f, 0f), spot)
        assertEquals(200f, spot.x + 200f * spot.zoom, 0.01f)
    }

    @Test
    fun itNeverLeavesAGapAtTheEdges() {
        // 2000 × 1000 at twice: 500 either way across, but no taller than the screen, so centered down.
        assertSpot(Spot(2f, 500f, 0f), zoom.moved(Spot(2f), 1f, 0f, 0f, 900f, 300f))
        assertSpot(Spot(2f, -500f, 0f), zoom.moved(Spot(2f), 1f, 0f, 0f, -900f, 0f))
        // At six times (the most), 3000 tall: 500 up or down.
        assertSpot(Spot(6f, 0f, -500f), zoom.moved(Spot(), 10f, 0f, 0f, 0f, -2000f))
    }

    @Test
    fun aDoubleTapZoomsInThereThenFitsAgain() {
        val tapped = zoom.doubleTapped(Spot(), 100f, 0f)
        assertSpot(Spot(PictureZoom.DOUBLE_TAP, -150f, 0f), tapped)
        assertSpot(Spot(), zoom.doubleTapped(tapped, 0f, 0f))
    }

    @Test
    fun aGlideGoesPartOfTheWay() {
        assertSpot(Spot(1.5f, 50f, -10f), Spot().toward(Spot(2f, 100f, -20f), 0.5f))
    }
}
