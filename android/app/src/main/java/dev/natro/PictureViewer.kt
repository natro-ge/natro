package dev.natro

import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animate
import androidx.compose.animation.core.spring
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlin.math.max
import kotlin.math.min

/** One of the chat's pictures, the whole screen, fitted to it: pinch to zoom (around the fingers), a double tap
 *  between fitted and DOUBLE_TAP times that (around the tap), and a finger moves it while it's larger than the
 *  screen. Back or the ✕ closes it. */
@Composable
fun PictureViewer(bytes: ByteArray, close: () -> Unit) {
    Dialog(onDismissRequest = close, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        PictureScreen(bytes, close)
    }
}

/** The picture itself, for a window of its own (the chat's dialog, or a card's activity). */
@Composable
fun PictureScreen(bytes: ByteArray, close: () -> Unit) {
    val image = remember(bytes) { Pictures.decode(bytes, FULL_PIXELS)?.asImageBitmap() } ?: return close()
    val colors = palette
    val scope = rememberCoroutineScope()
    var screen by remember { mutableStateOf(IntSize.Zero) }
    var spot by remember { mutableStateOf(Spot()) }
    var gliding by remember { mutableStateOf<Job?>(null) }
    val zoom = remember(screen, image) {
        PictureZoom(screen.width.toFloat(), screen.height.toFloat(), image.width.toFloat(), image.height.toFloat())
    }

    fun glide(to: Spot) {
        gliding?.cancel()
        val from = spot
        gliding = scope.launch {
            animate(0f, 1f, animationSpec = spring(stiffness = Spring.StiffnessMediumLow)) { done, _ -> spot = from.toward(to, done) }
        }
    }

    Box(
        Modifier.fillMaxSize().background(Color.Black).onSizeChanged { screen = it }
            .pointerInput(zoom) {
                detectTapGestures(onDoubleTap = { at -> glide(zoom.doubleTapped(spot, at.x - size.width / 2f, at.y - size.height / 2f)) })
            }
            .pointerInput(zoom) {
                detectTransformGestures { centroid, pan, by, _ ->
                    gliding?.cancel()
                    spot = zoom.moved(spot, by, centroid.x - size.width / 2f, centroid.y - size.height / 2f, pan.x, pan.y)
                }
            },
    ) {
        Image(
            image, contentDescription = "Picture", contentScale = ContentScale.Fit,
            modifier = Modifier.fillMaxSize().graphicsLayer {
                scaleX = spot.zoom
                scaleY = spot.zoom
                translationX = spot.x
                translationY = spot.y
            },
        )
        Box(
            Modifier.align(Alignment.TopEnd).statusBarsPadding().padding(12.dp).size(44.dp).clip(CircleShape)
                .background(colors.raised.copy(alpha = 0.85f)).clickable(onClick = close)
                .semantics { contentDescription = "Close" },
            contentAlignment = Alignment.Center,
        ) {
            Icon(Glyph.Close, colors.text, size = 22.dp)
        }
    }
}

/** Where the picture is: its zoom (1 is fitted to the screen) and how far its middle is from the screen's (pixels). */
data class Spot(val zoom: Float = 1f, val x: Float = 0f, val y: Float = 0f) {
    /** Part of the way (0 to 1) to another. */
    fun toward(to: Spot, done: Float) = Spot(zoom + (to.zoom - zoom) * done, x + (to.x - x) * done, y + (to.y - y) * done)
}

/** Zooming a picture of `pictureWidth` × `pictureHeight` on a screen of `width` × `height` (pixels). Points are
 *  from the screen's middle. */
class PictureZoom(private val width: Float, private val height: Float, pictureWidth: Float, pictureHeight: Float) {
    // The picture fitted to the screen.
    private val fitted = if (pictureWidth > 0 && pictureHeight > 0) min(width / pictureWidth, height / pictureHeight) else 0f
    private val fittedWidth = pictureWidth * fitted
    private val fittedHeight = pictureHeight * fitted

    /** Zoomed `by` around a point and moved by (dx, dy): the picture's point under the fingers stays under them. */
    fun moved(spot: Spot, by: Float, px: Float, py: Float, dx: Float, dy: Float): Spot {
        val zoom = min(MOST_ZOOM, max(1f, spot.zoom * by))
        val grow = zoom / spot.zoom
        return within(Spot(zoom, px - (px - spot.x) * grow + dx, py - (py - spot.y) * grow + dy))
    }

    /** Fitted again, or zoomed in around the tap. */
    fun doubleTapped(spot: Spot, px: Float, py: Float) =
        if (spot.zoom > 1.01f) Spot() else moved(Spot(), DOUBLE_TAP, px, py, 0f, 0f)

    /** Its edges never inside the screen's while it's larger; centered while it's smaller. */
    fun within(spot: Spot): Spot {
        val mx = max(0f, (fittedWidth * spot.zoom - width) / 2f)
        val my = max(0f, (fittedHeight * spot.zoom - height) / 2f)
        return spot.copy(x = spot.x.coerceIn(-mx, mx), y = spot.y.coerceIn(-my, my))
    }

    companion object {
        const val MOST_ZOOM = 6f
        const val DOUBLE_TAP = 2.5f
    }
}

private const val FULL_PIXELS = 2048
