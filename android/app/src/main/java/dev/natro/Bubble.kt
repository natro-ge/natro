package dev.natro

import android.annotation.SuppressLint
import android.content.ComponentCallbacks
import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.input.InputManager
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.Display
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.VelocityTracker
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowInsets
import android.view.WindowManager
import android.view.WindowManager.LayoutParams
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.AnimationSpec
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.VectorConverter
import androidx.compose.animation.core.spring
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.platform.AndroidUiDispatcher
import androidx.compose.ui.platform.ComposeView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.setViewTreeLifecycleOwner
import androidx.savedstate.SavedStateRegistry
import androidx.savedstate.SavedStateRegistryController
import androidx.savedstate.SavedStateRegistryOwner
import androidx.savedstate.setViewTreeSavedStateRegistryOwner
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.merge
import kotlinx.coroutines.launch
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.hypot
import kotlin.math.roundToInt

/** Which side of the bubble its label shows on. */
enum class LabelSide { Above, Right, Left }

/**
 * The bubble: Natro over whatever app is open, as drawn in the plan, a round grey button with her
 * green dot at the bottom center. It is a real overlay ("Display over other apps"), not a
 * see-through app: the app underneath keeps working, and only the bubble itself takes touches.
 *
 * - It shows while Natro is busy with something the phone asked (listening, thinking, a step, her
 *   question, done) and goes once she's done, unless the owner parked it on an edge: then it stays
 *   there as a shortcut.
 * - A tap while she listens ends the recording and sends it (as when she hears the owner stop);
 *   held, it opens the app. Dragged, it follows the finger; let go, it lands home, on the nearer
 *   edge, or (dropped on the ✕ that shows at the bottom, or flung down) it cancels what she's doing
 *   and goes. See Snap.
 * - Beside it a label says what she's doing; her questions unfold from it as a card with No and Yes.
 * - Her reply opens from it as text: the bubble grows into a card where it sits (up from it in the
 *   lower half of the screen, down in the upper; from its side when parked), filling in as she writes.
 *   A tap folds it back into the bubble; a short reply folds back by itself. A tap on the bubble
 *   opens her last reply again. Settings > "Show replies as text" turns this off.
 *
 * Each part is its own small window, so the app underneath gets every touch outside them. The
 * label and the ✕ never take touches; they're drawn at the opacity Android requires of an overlay
 * that lets touches through (80%).
 */
object Bubble {
    // What the windows draw (BubbleViews.kt). Compose state, so the windows follow it.
    internal val shown = mutableStateOf(false)
    internal val pressed = mutableStateOf(false)
    internal val dragging = mutableStateOf(false)
    /** Dragged or still gliding to where it was let go: the label and the card wait. */
    internal val moving = mutableStateOf(false)
    internal val overTarget = mutableStateOf(false)
    internal val status = mutableStateOf<BubbleStatus?>(null)
    internal val level = mutableFloatStateOf(0f)
    internal val labelSide = mutableStateOf(LabelSide.Above)
    internal val question = mutableStateOf<Natro.Question?>(null)
    internal val cardAbove = mutableStateOf(true)
    /** The ✕'s center from the top of its window, in pixels. */
    internal val targetY = mutableFloatStateOf(0f)
    /** Her reply is out as a card (the bubble hides meanwhile), and what it says. */
    internal val replyOpen = mutableStateOf(false)
    internal val reply = mutableStateOf<ReplyText?>(null)
    /** Where the bubble is in the reply card: it grows from there and folds back into it. */
    internal val replyFrom = mutableStateOf(TransformOrigin(0.5f, 1f))
    /** The tallest the reply card may be, in dp; it scrolls inside. */
    internal val replyMaxHeight = mutableFloatStateOf(300f)

    /** Whether the bubble is on screen (Android then lets Natro open apps from the background). */
    @Volatile var showing = false
        private set

    private class Overlay(val view: ComposeView, val params: LayoutParams)

    private lateinit var app: Context
    private lateinit var context: Context
    private lateinit var windows: WindowManager
    private val owner = WindowOwner()
    private val scope = CoroutineScope(SupervisorJob() + AndroidUiDispatcher.Main)
    private var density = 1f
    /** How see-through Android draws the windows that let touches through (the label draws thicker to match). */
    internal var passThroughAlpha = 0.8f
        private set

    private var face: Overlay? = null
    private var label: Overlay? = null
    private var target: Overlay? = null
    private var card: Overlay? = null
    private var replyCard: Overlay? = null
    private var leaving: Job? = null
    private var folding: Job? = null
    private var replyFolding: Job? = null
    private var motion: Job? = null
    /** Folds a short reply back once it's been read. */
    private var replyTimer: Job? = null
    /** After the reply card folds back, the bubble stays a moment before it goes (unless parked or busy). */
    private var lingering: Job? = null
    /** The reply that last opened by itself: each opens once. */
    private var openedFor: String? = null

    /** The bubble's center on screen, in pixels, and where it rests: Home or an Edge. */
    private var center = Offset.Zero
    private var place: Landing = Landing.Home

    // The finger
    private var tracker: VelocityTracker? = null
    private var down = Offset.Zero
    private var grabbedAt = Offset.Zero
    private var dragged = false
    /** Held long enough to open the app: the rest of the touch does nothing. */
    private var held = false
    private var holding: Job? = null
    /** The touch stopped the bubble while it was still gliding to where it was let go. */
    private var caught = false
    private var dragField: Field? = null

    fun init(context: Context) {
        if (::app.isInitialized) return
        app = context.applicationContext
        val display = app.getSystemService(DisplayManager::class.java).getDisplay(Display.DEFAULT_DISPLAY)
        this.context = app.createWindowContext(display, LayoutParams.TYPE_APPLICATION_OVERLAY, null)
        windows = this.context.getSystemService(WindowManager::class.java)
        density = this.context.resources.displayMetrics.density
        passThroughAlpha = minOf(PASS_THROUGH_ALPHA, app.getSystemService(InputManager::class.java).maximumObscuringOpacityForTouch)
        this.context.registerComponentCallbacks(object : ComponentCallbacks {
            override fun onConfigurationChanged(newConfig: Configuration) {
                if (face != null && !dragged) settleAgain()
            }

            @Deprecated("Deprecated in Java")
            override fun onLowMemory() {}
        })
        scope.launch {
            merge(Natro.bubble, Natro.question, Natro.recording, Natro.onScreen, Prefs.parked, Prefs.power, Prefs.showReplies)
                .collect { sync() }
        }
        scope.launch { Natro.level.collect { level.floatValue = it } }
        scope.launch { Natro.reply.collect(::replied) }
    }

    /** Show or hide the bubble and its parts as things stand (also after the overlay permission changes). */
    fun sync() {
        if (!::app.isInitialized) return
        status.value = Natro.bubble.value
        question.value = Natro.question.value.takeUnless { Natro.recording.value }
        // Listening again, her question, or the switch turned off: the reply card makes way.
        if (replyOpen.value && (Natro.recording.value || question.value != null || !Prefs.showReplies.value)) {
            replyTimer?.cancel()
            replyOpen.value = false
        }
        if (wanted()) {
            if (leaving != null) {  // back before it went: to its place again (it may have shrunk into the ✕)
                leaving?.cancel()
                leaving = null
                settleAgain()
            }
            if (face == null) open()
            shown.value = true
            syncCard()
            syncReply()
        } else if (face != null && leaving == null) {
            close()
        }
    }

    private fun wanted() = Settings.canDrawOverlays(app) && !Natro.onScreen.value &&
        (Natro.bubble.value != null || (Prefs.parked.value != null && Prefs.power.value) || dragging.value || moving.value ||
            replyOpen.value || lingering != null)

    private fun open() {
        val field = field()
        place = parkedPlace(field)
        center = spot(place, field)
        // Added bottom to top: the ✕ under the label under the bubble.
        target = overlay(LayoutParams.MATCH_PARENT, px(TARGET_HEIGHT_DP), touchable = false) { BubbleTarget() }
        label = overlay(px(LABEL_WIDTH_DP + 2 * LABEL_SHADOW_DP), px(LABEL_HEIGHT_DP + 2 * LABEL_SHADOW_DP), touchable = false) {
            BubbleLabel()
        }
        face = overlay(px(FACE_DP), px(FACE_DP), touchable = true) { BubbleFace() }.also {
            it.view.setOnTouchListener(::touch)
            it.view.contentDescription =
                "Natro. Tap to stop listening or to see her last reply, hold to open the app; drag to move, or down to the ✕ to cancel."
        }
        layout(field)
        showing = true
    }

    /** The bubble shrinks away and the label folds; then the windows go. */
    private fun close() {
        shown.value = false
        replyOpen.value = false
        replyTimer?.cancel()
        leaving = scope.launch {
            delay(EXIT_MILLIS)
            listOfNotNull(card, replyCard, face, label, target).forEach { windows.removeView(it.view) }
            replyFolding?.cancel()
            replyFolding = null
            replyCard = null
            card = null
            face = null
            label = null
            target = null
            leaving = null
            showing = false
            motion?.cancel()
            overTarget.value = false
            pressed.value = false
        }
    }

    private fun overlay(width: Int, height: Int, touchable: Boolean, content: @Composable () -> Unit): Overlay {
        val view = ComposeView(context).apply {
            setViewTreeLifecycleOwner(owner)
            setViewTreeSavedStateRegistryOwner(owner)
            setContent(content)
        }
        var flags = LayoutParams.FLAG_NOT_FOCUSABLE or LayoutParams.FLAG_LAYOUT_NO_LIMITS or LayoutParams.FLAG_LAYOUT_IN_SCREEN
        if (!touchable) flags = flags or LayoutParams.FLAG_NOT_TOUCHABLE
        val params = LayoutParams(width, height, LayoutParams.TYPE_APPLICATION_OVERLAY, flags, PixelFormat.TRANSLUCENT).apply {
            gravity = Gravity.TOP or Gravity.START
            fitInsetsTypes = 0
            layoutInDisplayCutoutMode = LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
            setCanPlayMoveAnimation(false)  // the bubble moves with the finger, not after it
            if (!touchable) alpha = passThroughAlpha
            title = "Natro"
        }
        windows.addView(view, params)
        return Overlay(view, params)
    }

    // Where things go

    private fun field(): Field {
        val metrics = windows.currentWindowMetrics
        val bars = metrics.windowInsets.getInsetsIgnoringVisibility(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
        return Field(metrics.bounds.width().toFloat(), metrics.bounds.height().toFloat(), bars.top.toFloat(),
            bars.bottom.toFloat(), density)
    }

    private fun parkedPlace(field: Field): Landing =
        Prefs.parked.value?.let { Landing.Edge(it.right, field.edgeY(it.y * field.height)) } ?: Landing.Home

    private fun spot(place: Landing, field: Field) = when (place) {
        is Landing.Edge -> Offset(field.edgeX(place.right), field.edgeY(place.y))
        else -> Offset(field.homeX, field.homeY)
    }

    /** After the screen turned: the same place on the new screen. */
    private fun settleAgain() {
        motion?.cancel()
        val field = field()
        place = parkedPlace(field)
        center = spot(place, field)
        layout(field)
    }

    /** Every window to where it goes for the bubble's center now. */
    private fun layout(field: Field) {
        moveFace(center)
        label?.let {
            val side = when (val at = place) {
                is Landing.Edge -> if (at.right) LabelSide.Left else LabelSide.Right
                else -> LabelSide.Above
            }
            labelSide.value = side
            val radius = field.radius
            val gap = px(LABEL_GAP_DP)
            val shadow = px(LABEL_SHADOW_DP)  // the window is this much bigger than the label on every side
            it.params.x = when (side) {
                LabelSide.Above -> center.x - it.params.width / 2f
                LabelSide.Right -> center.x + radius + gap - shadow
                LabelSide.Left -> center.x - radius - gap - it.params.width + shadow
            }.roundToInt()
            it.params.y = when (side) {
                LabelSide.Above -> center.y - radius - gap - it.params.height + shadow
                else -> center.y - it.params.height / 2f
            }.roundToInt()
            windows.updateViewLayout(it.view, it.params)
        }
        target?.let {
            it.params.y = (field.height - it.params.height).roundToInt()
            targetY.floatValue = field.targetY - it.params.y
            windows.updateViewLayout(it.view, it.params)
        }
        placeCard(field)
        placeReply(field)
    }

    private fun moveFace(to: Offset) {
        center = to
        val face = face ?: return
        face.params.x = (to.x - face.params.width / 2f).roundToInt()
        face.params.y = (to.y - face.params.height / 2f).roundToInt()
        windows.updateViewLayout(face.view, face.params)
    }

    // Her question, as a card unfolding from the bubble

    private fun syncCard() {
        val asking = question.value != null && !moving.value
        if (asking) {
            folding?.cancel()
            folding = null
            if (card == null) {
                val width = minOf(px(CARD_WIDTH_DP), field().width.roundToInt() - 2 * px(CARD_MARGIN_DP)) + 2 * px(CARD_SHADOW_DP)
                card = overlay(width, LayoutParams.WRAP_CONTENT, touchable = true) { BubbleCard() }
                placeCard(field())
            }
        } else if (card != null && folding == null) {
            folding = scope.launch {
                delay(EXIT_MILLIS)  // the card folds away first (BubbleCard)
                card?.let { windows.removeView(it.view) }
                card = null
                folding = null
            }
        }
    }

    private fun placeCard(field: Field) {
        val card = card ?: return
        val above = center.y > field.height / 2
        cardAbove.value = above
        val gap = px(CARD_GAP_DP) - px(CARD_SHADOW_DP)
        val margin = px(CARD_MARGIN_DP) - px(CARD_SHADOW_DP)
        card.params.x = (center.x - card.params.width / 2f).roundToInt()
            .coerceIn(margin, maxOf(margin, field.width.roundToInt() - margin - card.params.width))
        if (above) {
            card.params.gravity = Gravity.BOTTOM or Gravity.START
            card.params.y = (field.height - (center.y - field.radius - gap)).roundToInt()
        } else {
            card.params.gravity = Gravity.TOP or Gravity.START
            card.params.y = (center.y + field.radius + gap).roundToInt()
        }
        windows.updateViewLayout(card.view, card.params)
    }

    // Her reply, as a card the bubble grows into

    private fun replied(new: ReplyText?) {
        reply.value = new ?: return
        if (new.text.isBlank()) return
        if (new.id != openedFor) {
            openedFor = new.id
            if (Prefs.showReplies.value && !Natro.onScreen.value && !Natro.recording.value) unfold()
        } else if (replyOpen.value) {
            timeReply()
        }
    }

    /** The bubble grows into the card with her (last) reply. */
    private fun unfold() {
        if (reply.value?.text.isNullOrBlank()) return
        lingering?.cancel()
        lingering = null
        replyOpen.value = true
        sync()
        timeReply()
    }

    /** The card folds back into the bubble (a tap on it, or a short reply read). */
    internal fun fold() {
        if (!replyOpen.value) return
        replyTimer?.cancel()
        replyOpen.value = false
        lingering = scope.launch {
            delay(LINGER_MILLIS)
            lingering = null
            sync()
        }
        sync()
    }

    /** A short reply, once it's whole, folds back after the time to read it; a touch on the card starts that time again. */
    internal fun timeReply() {
        replyTimer?.cancel()
        val shown = reply.value ?: return
        if (!replyOpen.value || !shown.done || !Markdown.isShort(shown.text)) return
        replyTimer = scope.launch {
            delay(Markdown.readingMillis(shown.text))
            fold()
        }
    }

    private fun syncReply() {
        if (replyOpen.value && !moving.value) {
            replyFolding?.cancel()
            replyFolding = null
            if (replyCard == null) {
                val field = field()
                val width = minOf((field.width * REPLY_WIDTH_SHARE).roundToInt(), px(REPLY_WIDTH_DP)) + 2 * px(CARD_SHADOW_DP)
                replyCard = overlay(width, LayoutParams.WRAP_CONTENT, touchable = true) { BubbleReply() }
                placeReply(field)
            }
        } else if (replyCard != null && replyFolding == null) {
            replyFolding = scope.launch {
                delay(EXIT_MILLIS)  // the card folds away first (BubbleReply)
                replyCard?.let { windows.removeView(it.view) }
                replyCard = null
                replyFolding = null
            }
        }
        // While the card is out the bubble hides under it, and takes no touches.
        face?.let {
            val flags = if (replyOpen.value) it.params.flags or LayoutParams.FLAG_NOT_TOUCHABLE
            else it.params.flags and LayoutParams.FLAG_NOT_TOUCHABLE.inv()
            if (flags != it.params.flags) {
                it.params.flags = flags
                windows.updateViewLayout(it.view, it.params)
            }
        }
    }

    /**
     * The reply card where the bubble is: from its side when it's parked on an edge, centered over it
     * at home; growing up from it in the lower half of the screen, down from it in the upper.
     */
    private fun placeReply(field: Field) {
        val card = replyCard ?: return
        val shadow = px(CARD_SHADOW_DP)
        val margin = px(CARD_MARGIN_DP)
        val radius = field.radius
        val width = card.params.width
        card.params.x = when (val at = place) {
            is Landing.Edge -> if (at.right) center.x + radius + shadow - width else center.x - radius - shadow
            else -> center.x - width / 2f
        }.roundToInt().coerceIn(margin - shadow, maxOf(margin - shadow, field.width.roundToInt() - margin + shadow - width))
        val down = center.y < field.height / 2
        val room = if (down) field.height - field.bottom - margin - (center.y - radius) else center.y + radius - field.top - margin
        replyMaxHeight.floatValue = minOf(room, field.height * REPLY_HEIGHT_SHARE) / density
        if (down) {
            card.params.gravity = Gravity.TOP or Gravity.START
            card.params.y = (center.y - radius - shadow).roundToInt()
        } else {
            card.params.gravity = Gravity.BOTTOM or Gravity.START
            card.params.y = (field.height - (center.y + radius) - shadow).roundToInt()
        }
        val inside = (width - 2 * shadow).toFloat()
        replyFrom.value = TransformOrigin(((center.x - card.params.x - shadow) / inside).coerceIn(0f, 1f), if (down) 0f else 1f)
        windows.updateViewLayout(card.view, card.params)
    }

    // The finger

    @SuppressLint("ClickableViewAccessibility")  // tap and hold are said in the bubble's content description
    private fun touch(view: View, event: MotionEvent): Boolean {
        // Moving the window moves the event's own coordinates with it: the screen's stay put.
        val onScreen = MotionEvent.obtain(event).apply { setLocation(event.rawX, event.rawY) }
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                caught = motion?.isActive == true
                motion?.cancel()
                tracker?.recycle()
                tracker = VelocityTracker.obtain().also { it.addMovement(onScreen) }
                down = Offset(event.rawX, event.rawY)
                grabbedAt = center
                dragged = false
                held = false
                pressed.value = true
                holding?.cancel()
                holding = scope.launch {
                    delay(ViewConfiguration.getLongPressTimeout().toLong())
                    held = true
                    pressed.value = false
                    view.haptic(HapticFeedbackConstants.LONG_PRESS)
                    openApp()
                }
            }
            MotionEvent.ACTION_MOVE -> {
                tracker?.addMovement(onScreen)
                val moved = Offset(event.rawX, event.rawY) - down
                if (!dragged && !held && moved.getDistance() > ViewConfiguration.get(view.context).scaledTouchSlop) {
                    holding?.cancel()
                    dragged = true
                    dragField = field()
                    pressed.value = false
                    dragging.value = true
                    moving.value = true
                    syncCard()
                    view.haptic(HapticFeedbackConstants.DRAG_START)
                }
                if (dragged) drag(view, grabbedAt + moved)
            }
            MotionEvent.ACTION_UP -> {
                tracker?.addMovement(onScreen)
                letGo(view, cancelled = false)
            }
            MotionEvent.ACTION_CANCEL -> letGo(view, cancelled = true)
        }
        onScreen.recycle()
        return true
    }

    private fun drag(view: View, to: Offset) {
        val field = dragField ?: return
        val over = Snap.onTarget(to.x, to.y, field)
        if (over != overTarget.value) {
            overTarget.value = over
            view.haptic(if (over) HapticFeedbackConstants.GESTURE_THRESHOLD_ACTIVATE else HapticFeedbackConstants.GESTURE_THRESHOLD_DEACTIVATE)
        }
        if (over) {
            val onIt = Offset(field.targetX, field.targetY)
            if (center != onIt && motion?.isActive != true) glide(onIt, MAGNET)
        } else {
            motion?.cancel()
            moveFace(to)
        }
    }

    private fun letGo(view: View, cancelled: Boolean) {
        holding?.cancel()
        pressed.value = false
        val velocity = tracker?.let {
            it.computeCurrentVelocity(1000, ViewConfiguration.get(view.context).scaledMaximumFlingVelocity.toFloat())
            Offset(it.xVelocity, it.yVelocity)
        } ?: Offset.Zero
        tracker?.recycle()
        tracker = null
        if (!dragged) {
            if (caught) {  // it didn't get there: put it there now
                moving.value = false
                settleAgain()
                sync()
            }
            if (!cancelled && !held && Natro.recording.value) {
                view.haptic(HapticFeedbackConstants.CONTEXT_CLICK)
                Natro.stopRecording()
            } else if (!cancelled && !held && !reply.value?.text.isNullOrBlank()) {
                view.haptic(HapticFeedbackConstants.CONTEXT_CLICK)
                unfold()
            }
            held = false
            return
        }
        dragged = false
        dragging.value = false
        val field = field()
        val landing = if (overTarget.value) Landing.Dismiss else Snap.landing(center.x, center.y, velocity.x, velocity.y, field)
        if (landing == Landing.Dismiss) {
            dismiss(view)
            return
        }
        place = landing
        Prefs.setParked((landing as? Landing.Edge)?.let { Parked(it.right, it.y / field.height) })
        glide(spot(landing, field), SETTLE, velocity) {
            moving.value = false
            view.haptic(HapticFeedbackConstants.SEGMENT_TICK)
            layout(field())
            sync()
        }
    }

    /** Dropped on the ✕ or flung away: whatever Natro is doing for the phone stops, and the bubble goes. */
    private fun dismiss(view: View) {
        view.haptic(HapticFeedbackConstants.GESTURE_END)
        place = Landing.Home
        moving.value = false
        Natro.cancel()
        Prefs.setParked(null)
        sync()
        if (leaving == null) overTarget.value = false  // still wanted (it shrinks into the ✕ otherwise)
    }

    private fun glide(to: Offset, spec: AnimationSpec<Offset>, velocity: Offset = Offset.Zero, then: () -> Unit = {}) {
        motion?.cancel()
        motion = scope.launch {
            Animatable(center, Offset.VectorConverter).animateTo(to, spec, velocity) { moveFace(value) }
            then()
        }
    }

    private fun openApp() {
        app.startActivity(Intent(app, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    /**
     * Screen control is about to tap at (x, y) on the screen. If the bubble covers that spot, it lets
     * touches through (and hides) for a moment, so the tap reaches the app. Called off the main thread.
     */
    fun stepAside(x: Float, y: Float) {
        if (!showing) return
        val done = CountDownLatch(1)
        Handler(Looper.getMainLooper()).post {
            val face = face
            if (face != null && hypot(x - center.x, y - center.y) <= face.params.width / 2f) {
                face.params.alpha = 0f
                face.params.flags = face.params.flags or LayoutParams.FLAG_NOT_TOUCHABLE
                windows.updateViewLayout(face.view, face.params)
                scope.launch {
                    delay(STEP_ASIDE_MILLIS)
                    if (this@Bubble.face === face) {
                        face.params.alpha = 1f
                        face.params.flags = face.params.flags and LayoutParams.FLAG_NOT_TOUCHABLE.inv()
                        windows.updateViewLayout(face.view, face.params)
                    }
                }
            }
            done.countDown()
        }
        done.await(300, TimeUnit.MILLISECONDS)
    }

    private fun View.haptic(kind: Int) {
        performHapticFeedback(kind)
    }

    private fun px(dp: Float) = (dp * density).roundToInt()

    private const val FACE_DP = 96f  // the 64 dp bubble, with room for its shadow and its voice's glow
    private const val LABEL_WIDTH_DP = 260f
    private const val LABEL_HEIGHT_DP = 56f
    private const val LABEL_GAP_DP = 6f
    /** The label's window is this much bigger on every side than the label, for its shadow (BubbleLabel). */
    internal const val LABEL_SHADOW_DP = 14f
    private const val TARGET_HEIGHT_DP = 200f
    private const val CARD_WIDTH_DP = 360f
    /** The reply card: at most this wide (and this share of the screen's width), and this share of its height. */
    private const val REPLY_WIDTH_DP = 420f
    private const val REPLY_WIDTH_SHARE = 0.9f
    private const val REPLY_HEIGHT_SHARE = 0.45f
    private const val LINGER_MILLIS = 1_500L
    private const val CARD_MARGIN_DP = 16f
    private const val CARD_GAP_DP = 14f
    /** The card's window is this much bigger on every side than the card, for its shadow (BubbleCard). */
    internal const val CARD_SHADOW_DP = 12f
    private const val EXIT_MILLIS = 240L
    private const val STEP_ASIDE_MILLIS = 700L
    private const val PASS_THROUGH_ALPHA = 0.8f
    private val SETTLE: AnimationSpec<Offset> = spring(dampingRatio = 0.72f, stiffness = Spring.StiffnessMediumLow)
    private val MAGNET: AnimationSpec<Offset> = spring(dampingRatio = 0.8f, stiffness = Spring.StiffnessHigh)
}

/** What a window that isn't an activity's needs for Compose: a lifecycle and a saved-state registry. */
private class WindowOwner : LifecycleOwner, SavedStateRegistryOwner {
    private val registry = LifecycleRegistry(this)
    private val saved = SavedStateRegistryController.create(this)

    init {
        saved.performAttach()
        saved.performRestore(null)
        registry.currentState = Lifecycle.State.RESUMED
    }

    override val lifecycle: Lifecycle get() = registry
    override val savedStateRegistry: SavedStateRegistry get() = saved.savedStateRegistry
}
