package dev.natro

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Path
import android.graphics.Rect
import android.os.Build
import android.os.Bundle
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import java.io.ByteArrayOutputStream
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.roundToInt

/**
 * Controlling apps by their screen (the owner turns it on in Settings > Accessibility > Natro).
 *
 * describe() turns the screen into numbered lines ("[4] button: Search (tap)"); the brain then
 * taps, types or scrolls by number, and each action returns the new screen. Buttons whose text
 * looks like sending, buying, paying or deleting are refused by tap() and need tapRisky(), which
 * the brain only runs after the owner's yes. Everything on screen is personal: only Claude sees it.
 */
class ScreenControl : AccessibilityService() {
    private var elements: List<AccessibilityNodeInfo> = emptyList()
    // What each element was when the screen was read, and whose screen it was: to find it again.
    private var seen: List<Seen> = emptyList()
    private var screenApp = ""

    override fun onServiceConnected() {
        current = this
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        if (current === this) current = null
        return super.onUnbind(intent)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}

    override fun onInterrupt() {}

    /** The screen as a JPEG, the longest side at most SHOT_PIXELS (blocks for up to a few seconds). */
    fun screenshot(): ByteArray {
        val done = CountDownLatch(1)
        var shot: Bitmap? = null
        var problem: String? = null
        takeScreenshot(Display.DEFAULT_DISPLAY, mainExecutor, object : TakeScreenshotCallback {
            override fun onSuccess(result: ScreenshotResult) {
                val buffer = result.hardwareBuffer
                shot = Bitmap.wrapHardwareBuffer(buffer, result.colorSpace)?.copy(Bitmap.Config.ARGB_8888, false)
                buffer.close()
                done.countDown()
            }

            override fun onFailure(errorCode: Int) {
                problem = when (errorCode) {
                    ERROR_TAKE_SCREENSHOT_SECURE_WINDOW -> "The app on screen doesn't allow screenshots."
                    ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT -> "Too soon after the last screenshot: try again."
                    else -> "The phone couldn't take a screenshot (error $errorCode)."
                }
                done.countDown()
            }
        })
        if (!done.await(SHOT_SECONDS, TimeUnit.SECONDS)) throw ToolFailure("The phone didn't take the screenshot in time.")
        val bitmap = shot ?: throw ToolFailure(problem ?: "The phone couldn't take a screenshot.")
        val scale = minOf(1f, SHOT_PIXELS.toFloat() / max(bitmap.width, bitmap.height))
        val small = if (scale < 1f) {
            Bitmap.createScaledBitmap(bitmap, (bitmap.width * scale).roundToInt(), (bitmap.height * scale).roundToInt(), true)
        } else bitmap
        return ByteArrayOutputStream().also { small.compress(Bitmap.CompressFormat.JPEG, SHOT_QUALITY, it) }.toByteArray()
    }

    fun describe(): String {
        val root = rootInActiveWindow ?: throw ToolFailure("Can't read the screen right now (it may be locked or off).")
        val found = mutableListOf<AccessibilityNodeInfo>()
        val lines = mutableListOf<String>()
        fun visit(node: AccessibilityNodeInfo) {
            if (found.size >= MAX_ELEMENTS || !node.isVisibleToUser) return
            val label = label(node)
            val actions = buildList {
                if (node.isClickable) add("tap")
                if (node.isEditable) add("edit")
                if (node.isScrollable) add("scroll")
                if (node.isCheckable) add(if (checked(node)) "on" else "off")
                if (!node.isEnabled) add("disabled")
            }
            if (label.isNotBlank() || node.isEditable || node.isScrollable || (node.isClickable && childText(node).isNotBlank())) {
                found += node
                val shown = label.ifBlank { childText(node) }
                lines += "[${found.size}] ${kind(node)}: ${Text.short(shown)}" + if (actions.isEmpty()) "" else " (${actions.joinToString(", ")})"
            }
            for (i in 0 until node.childCount) node.getChild(i)?.let(::visit)
        }
        visit(root)
        elements = found
        seen = found.map { seenOf(it) }
        val app = root.packageName?.toString().orEmpty()
        screenApp = app
        val cut = if (found.size >= MAX_ELEMENTS) "\n(more on screen than shown)" else ""
        return "Screen of $app:\n" + lines.joinToString("\n") + cut
    }

    fun tap(number: Int, risky: Boolean): String {
        // A risky tap ran after his yes, which he may have given in Natro's own screen, covering the app.
        val node = if (risky) elementAfterQuestion(number) else element(number)
        val target = clickableSelfOrParent(node) ?: node
        val words = listOf(label(node), label(target), childText(target)).joinToString(" ")
        if (!risky && Text.RISKY.containsMatchIn(words)) {
            throw ToolFailure("[$number] (${Text.short(words, 40)}) looks like it sends, buys, pays or deletes: use " +
                "tap_risky, which asks him first.")
        }
        if (!target.performAction(AccessibilityNodeInfo.ACTION_CLICK)) tapAt(target)
        return after("Tapped [$number].")
    }

    fun type(number: Int, text: String): String {
        val node = element(number)
        if (!node.isEditable) throw ToolFailure("[$number] isn't a text field.")
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        val args = Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text) }
        if (!node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) throw ToolFailure("Couldn't type into [$number].")
        return after("Typed into [$number].")
    }

    fun scroll(down: Boolean, number: Int?): String {
        val node = if (number != null) element(number) else elements.firstOrNull { it.refresh() && it.isScrollable }
            ?: throw ToolFailure("Nothing on this screen scrolls (read the screen again first).")
        val action = if (down) AccessibilityNodeInfo.ACTION_SCROLL_FORWARD else AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
        if (!node.performAction(action)) throw ToolFailure("It won't scroll further that way.")
        return after("Scrolled ${if (down) "down" else "up"}.")
    }

    fun press(key: String): String {
        val action = mapOf(
            "back" to GLOBAL_ACTION_BACK, "home" to GLOBAL_ACTION_HOME, "recents" to GLOBAL_ACTION_RECENTS,
            "notifications" to GLOBAL_ACTION_NOTIFICATIONS, "quick_settings" to GLOBAL_ACTION_QUICK_SETTINGS,
        )[key] ?: throw ToolFailure("The key is back, home, recents, notifications or quick_settings.")
        if (!performGlobalAction(action)) throw ToolFailure("Android didn't take that.")
        return after("Pressed $key.")
    }

    /** Tap an element found some other way (Sending's Send): itself or the nearest parent that taps. */
    fun click(node: AccessibilityNodeInfo) {
        val target = clickableSelfOrParent(node) ?: node
        if (!target.performAction(AccessibilityNodeInfo.ACTION_CLICK)) tapAt(target)
    }

    private fun element(number: Int): AccessibilityNodeInfo {
        val node = elements.getOrNull(number - 1) ?: throw ToolFailure("There's no [$number]: read the screen first.")
        if (!node.refresh()) throw screenChanged()
        return node
    }

    /**
     * The element he said yes to. If its app is no longer in front (he answered in Natro's screen), bring the app
     * back first. Then the very same element, or one that is unmistakably it: the only one of its kind, id and
     * label at the same place. Anything else means the screen really changed, and he is asked again.
     */
    private fun elementAfterQuestion(number: Int): AccessibilityNodeInfo {
        val node = elements.getOrNull(number - 1) ?: throw ToolFailure("There's no [$number]: read the screen first.")
        if (node.refresh()) return node
        val was = seen.getOrNull(number - 1) ?: throw screenChanged()
        if (!bringToFront(screenApp)) throw screenChanged()
        if (node.refresh()) return node
        describe()
        val again = was.findIn(seen)
        if (again == null) {
            // The numbers he and Claude saw no longer mean anything: a tap now needs a new read.
            elements = emptyList()
            seen = emptyList()
            throw screenChanged()
        }
        return elements[again]
    }

    /** Puts the app in front again (as opening it from the launcher does: same task, same screen). */
    private fun bringToFront(app: String): Boolean {
        if (frontApp() == app) return true
        val open = packageManager.getLaunchIntentForPackage(app) ?: return false
        startActivity(open.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        repeat(FRONT_WAIT_MILLIS / 100) {
            Thread.sleep(100)
            if (frontApp() == app) {
                Thread.sleep(SETTLE_MILLIS)
                return true
            }
        }
        return false
    }

    private fun frontApp() = rootInActiveWindow?.packageName?.toString()

    /** The new screen, once the app has had a moment to react. */
    private fun after(done: String): String {
        Thread.sleep(SETTLE_MILLIS)
        return "$done Now:\n${describe()}"
    }

    private fun tapAt(node: AccessibilityNodeInfo) {
        val bounds = Rect().also(node::getBoundsInScreen)
        Bubble.stepAside(bounds.exactCenterX(), bounds.exactCenterY())  // so the tap reaches the app, not the bubble
        val path = Path().apply { moveTo(bounds.exactCenterX(), bounds.exactCenterY()) }
        dispatchGesture(GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(path, 0, 60)).build(),
            null, null)
    }

    companion object {
        private const val MAX_ELEMENTS = 150
        // Screenshots: the longest side at most this, as JPEG, so one is a few hundred KB.
        private const val SHOT_PIXELS = 1920
        private const val SHOT_QUALITY = 80
        private const val SHOT_SECONDS = 5L
        private const val SETTLE_MILLIS = 900L
        private const val FRONT_WAIT_MILLIS = 3000

        private fun screenChanged() = ToolFailure("The screen changed since it was read: read it again.")

        private fun seenOf(node: AccessibilityNodeInfo): Seen {
            val bounds = Rect().also(node::getBoundsInScreen)
            return Seen(kind(node), node.viewIdResourceName.orEmpty(), label(node).ifBlank { childText(node) },
                bounds.left, bounds.top, bounds.right, bounds.bottom)
        }

        private fun label(node: AccessibilityNodeInfo): String =
            listOfNotNull(node.text, node.contentDescription, node.hintText).joinToString(" · ") { it.toString() }.trim()

        private fun childText(node: AccessibilityNodeInfo): String {
            val texts = mutableListOf<String>()
            fun visit(child: AccessibilityNodeInfo, depth: Int) {
                if (depth > 3 || texts.size >= 4) return
                label(child).takeIf { it.isNotBlank() }?.let(texts::add)
                for (i in 0 until child.childCount) child.getChild(i)?.let { visit(it, depth + 1) }
            }
            for (i in 0 until node.childCount) node.getChild(i)?.let { visit(it, 0) }
            return texts.joinToString(" · ")
        }

        private fun clickableSelfOrParent(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
            var at: AccessibilityNodeInfo? = node
            repeat(6) {
                if (at?.isClickable == true) return at
                at = at?.parent
            }
            return null
        }

        private fun checked(node: AccessibilityNodeInfo): Boolean =
            if (Build.VERSION.SDK_INT >= 36) node.checked == AccessibilityNodeInfo.CHECKED_STATE_TRUE
            else @Suppress("DEPRECATION") node.isChecked

        private fun kind(node: AccessibilityNodeInfo): String = when {
            node.isEditable -> "field"
            node.isCheckable -> "switch"
            node.className?.contains("Button") == true -> "button"
            node.className?.contains("Image") == true -> "image"
            node.isScrollable -> "list"
            node.isClickable -> "item"
            else -> "text"
        }

        @Volatile var current: ScreenControl? = null
    }
}

/** A screen element as it was read: enough to recognize it once the app has rebuilt its screen. */
data class Seen(val kind: String, val id: String, val label: String,
                val left: Int, val top: Int, val right: Int, val bottom: Int) {
    /** Its place on a new screen: the one element with the same kind, id and label at the same place, if any. */
    fun findIn(screen: List<Seen>): Int? {
        if (label.isBlank()) return null
        return screen.indices.singleOrNull { screen[it].kind == kind && screen[it].id == id && screen[it].label == label }
            ?.takeIf { screen[it].near(this) }
    }

    private fun near(other: Seen) = abs(left - other.left) <= NEAR && abs(top - other.top) <= NEAR &&
        abs(right - other.right) <= NEAR && abs(bottom - other.bottom) <= NEAR

    private companion object {
        const val NEAR = 24  // pixels
    }
}
