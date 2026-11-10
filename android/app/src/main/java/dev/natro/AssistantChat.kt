package dev.natro

import android.content.Context
import android.content.Intent
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.view.Display
import android.view.Gravity
import android.view.KeyEvent
import android.view.WindowManager
import android.view.WindowManager.LayoutParams
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.AndroidUiDispatcher
import androidx.compose.ui.platform.AbstractComposeView
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.setViewTreeLifecycleOwner
import androidx.savedstate.setViewTreeSavedStateRegistryOwner
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/**
 * The assistant gesture's chat: Natro's chat over whatever app is open, in the PC chat's glass: the conversation
 * floating above a glass bar, with a button at the top right to open the app. She listens as it opens; a tap on the
 * bar (or on "Listening…") stops that, and the window takes the keyboard (only then, as the PC's does) so he can
 * type. A tap anywhere else, or Back once it has the keyboard, closes it. Meanwhile the bubble stays away
 * (Bubble.wanted): everything it would show is here.
 */
object AssistantChat {
    private val opened = MutableStateFlow(false)
    val open: StateFlow<Boolean> = opened

    private var windows: WindowManager? = null
    private var view: ChatWindowView? = null
    private var params: LayoutParams? = null
    private var appContext: Context? = null
    private val scope = CoroutineScope(SupervisorJob() + AndroidUiDispatcher.Main)
    private var watching: Job? = null

    /** Open it (on the main thread), unless Natro's own app is in front, which is the chat already. */
    fun show(context: Context) {
        if (view != null || Natro.onScreen.value) return
        val app = context.applicationContext
        appContext = app
        val display = app.getSystemService(DisplayManager::class.java).getDisplay(Display.DEFAULT_DISPLAY)
        val window = app.createWindowContext(display, LayoutParams.TYPE_APPLICATION_OVERLAY, null)
        val manager = window.getSystemService(WindowManager::class.java)
        val owner = WindowOwner()
        val chat = ChatWindowView(window).apply {
            setViewTreeLifecycleOwner(owner)
            setViewTreeSavedStateRegistryOwner(owner)
        }
        // Not focusable (the app underneath keeps its keyboard and Back) until he goes for the field: takeKeyboard.
        val layout = LayoutParams(
            LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT, LayoutParams.TYPE_APPLICATION_OVERLAY,
            LayoutParams.FLAG_NOT_FOCUSABLE or LayoutParams.FLAG_LAYOUT_NO_LIMITS or LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            fitInsetsTypes = 0
            layoutInDisplayCutoutMode = LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
            softInputMode = LayoutParams.SOFT_INPUT_ADJUST_NOTHING
            title = "Natro chat"
        }
        manager.addView(chat, layout)
        windows = manager
        view = chat
        params = layout
        opened.value = true
        // Natro's own app opening (the button, or anything else) makes this chat redundant.
        watching = scope.launch {
            Natro.onScreen.first { it }
            hide()
        }
    }

    fun hide() {
        val chat = view ?: return
        watching?.cancel()
        stopRecording()
        view = null
        params = null
        runCatching { windows?.removeView(chat) }
        windows = null
        opened.value = false
    }

    /** He went for the field: nothing recorded is sent, and the window takes the keyboard. */
    internal fun typing() {
        stopRecording()
        val chat = view ?: return
        val layout = params ?: return
        if (layout.flags and LayoutParams.FLAG_NOT_FOCUSABLE != 0) {
            layout.flags = layout.flags and LayoutParams.FLAG_NOT_FOCUSABLE.inv()
            windows?.updateViewLayout(chat, layout)
        }
    }

    /** What was being recorded (hers to hear, or listening for him to go on) is dropped, not sent. */
    private fun stopRecording() {
        if (Natro.listeningForMore.value) Natro.endListeningForMore() else if (Natro.recording.value) Natro.dropRecording()
    }

    internal fun openApp() {
        val app = appContext ?: return
        app.startActivity(Intent(app, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        hide()
    }

    /** Back closes it, once the window has the keyboard (before that, Back is the app underneath's). */
    private class ChatWindowView(context: Context) : AbstractComposeView(context) {
        @Composable
        override fun Content() {
            AssistantChatContent()
        }

        override fun dispatchKeyEvent(event: KeyEvent): Boolean {
            if (event.keyCode == KeyEvent.KEYCODE_BACK) {
                if (event.action == KeyEvent.ACTION_UP) hide()
                return true
            }
            return super.dispatchKeyEvent(event)
        }
    }
}

/** What the window draws: the conversation and the bar at the bottom, the open-app button at the top right. */
@Composable
private fun AssistantChatContent() {
    GlassTheme {
        val turns by Natro.turns.collectAsStateWithLifecycle()
        val question by Natro.question.collectAsStateWithLifecycle()
        val context = androidx.compose.ui.platform.LocalContext.current
        val actions = remember {
            AppActions(
                microphoneAllowed = { context.checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED },
                askMicrophone = { AssistantChat.openApp() },  // the app asks for it
                setWakeWord = {},
                setPower = {},
                setPlaces = {},
            )
        }
        var shown by remember { mutableStateOf(false) }
        LaunchedEffect(Unit) { shown = true }
        val tallest = (LocalConfiguration.current.screenHeightDp * 0.55f).dp
        val colors = palette
        // Taps on empty glass-less space close it; what is drawn takes its own.
        Box(Modifier.fillMaxSize().pointerInput(Unit) { detectTapGestures { AssistantChat.hide() } }) {
            AnimatedVisibility(
                visible = shown,
                modifier = Modifier.align(Alignment.TopEnd),
                enter = fadeIn(tween(200)),
                exit = fadeOut(tween(120)),
            ) {
                Box(
                    Modifier.statusBarsPadding().padding(12.dp).size(44.dp).clip(CircleShape).background(colors.surface)
                        .border(1.dp, colors.line, CircleShape).clickable { AssistantChat.openApp() }
                        .semantics { contentDescription = "Open Natro" },
                    contentAlignment = Alignment.Center,
                ) {
                    Logo(26.dp)
                }
            }
            AnimatedVisibility(
                visible = shown,
                modifier = Modifier.align(Alignment.BottomCenter),
                enter = slideInVertically(tween(320)) { it / 3 } + fadeIn(tween(220)),
                exit = slideOutVertically(tween(200)) { it / 3 } + fadeOut(tween(150)),
            ) {
                Column(Modifier.fillMaxWidth().statusBarsPadding().padding(top = 56.dp)) {
                    if (turns.isNotEmpty()) {
                        Box(Modifier.weight(1f, fill = false).heightIn(max = tallest).pointerInput(Unit) { detectTapGestures { } }) {
                            Conversation(
                                turns,
                                Modifier.fillMaxWidth().graphicsLayer { compositingStrategy = CompositingStrategy.Offscreen }
                                    .drawWithContent {
                                        drawContent()
                                        // Fading out at the top, as the PC's list does.
                                        val fade = (40.dp.toPx() / size.height).coerceIn(0f, 1f)
                                        drawRect(
                                            Brush.verticalGradient(0f to Color.Transparent, fade to Color.Black),
                                            blendMode = BlendMode.DstIn,
                                        )
                                    },
                            )
                        }
                    }
                    question?.let { asked ->
                        Box(Modifier.pointerInput(Unit) { detectTapGestures { } }) { QuestionCard(asked) }
                    }
                    Box(Modifier.pointerInput(Unit) { detectTapGestures { } }) {
                        Composer(actions, glass = true, onTyping = AssistantChat::typing)
                    }
                }
            }
        }
    }
}
