package dev.natro

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlin.math.PI
import kotlin.math.sin
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

// The bottom of the chat: the text field and a picture to send, the mic button (held, or tapped for hands-free) that
// turns into send once something is typed, and the bar that says Natro is off.

/**
 * The text field, and the mic button that turns into send once something is typed.
 *
 * glass: the field is a glass pill of its own and there's no picture button (the assistant's chat, over other
 * apps: AssistantChat). onTyping: he went for the field (a tap on it, or on "Listening…"): whatever was recording
 * stops, and the window may take the keyboard.
 */
@Composable
internal fun Composer(actions: AppActions, glass: Boolean = false, onTyping: (() -> Unit)? = null) {
    val colors = palette
    val haptics = LocalHapticFeedback.current
    var typed by rememberSaveable { mutableStateOf("") }
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val pick = if (glass) null else rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri != null) scope.launch {
            try {
                val bytes = withContext(Dispatchers.IO) { Pictures.forChat(context.contentResolver, uri) }
                Natro.sendPicture(bytes, "image/jpeg", uri.lastPathSegment?.let { "$it.jpg" } ?: "picture.jpg")
            } catch (e: Exception) {
                Natro.notice.value = "Couldn't read that picture."
            }
        }
    }
    val recording by Natro.recording.collectAsStateWithLifecycle()
    val handsFree by Natro.handsFree.collectAsStateWithLifecycle()
    val level by Natro.level.collectAsStateWithLifecycle()
    val keyboard = LocalSoftwareKeyboardController.current
    val typingField = remember { FocusRequester() }
    var focusWhenQuiet by remember { mutableStateOf(false) }
    // A tap on "Listening…": the recording goes, and the field he reached for opens for him.
    LaunchedEffect(recording, focusWhenQuiet) {
        if (recording || !focusWhenQuiet) return@LaunchedEffect
        focusWhenQuiet = false
        repeat(10) {
            try {
                typingField.requestFocus()
                return@LaunchedEffect
            } catch (e: IllegalStateException) {
                delay(50)  // the field isn't drawn yet
            }
        }
    }

    fun send() {
        val text = typed.trim()
        if (text.isEmpty()) return
        haptics.performHapticFeedback(HapticFeedbackType.Confirm)
        Natro.sendText(text)
        typed = ""
    }

    Row(
        Modifier.fillMaxWidth().navigationBarsPadding().imePadding().padding(start = 12.dp, end = 12.dp, top = 6.dp, bottom = 10.dp),
        verticalAlignment = Alignment.Bottom,
    ) {
        if (pick != null) {
            Box(Modifier.height(52.dp), contentAlignment = Alignment.Center) {
                RoundIcon(Glyph.Picture, "Send a picture", {
                    pick.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
                })
            }
            Spacer(Modifier.width(4.dp))
        }
        val pill = RoundedCornerShape(26.dp)
        Box(
            Modifier.weight(1f).heightIn(min = 52.dp).clip(pill).background(if (glass) colors.surface else colors.raised)
                .then(if (glass) Modifier.border(1.dp, colors.line, pill) else Modifier)
                .then(
                    if (recording && onTyping != null) {
                        Modifier.pointerInput(Unit) {
                            detectTapGestures {
                                onTyping()
                                focusWhenQuiet = true
                            }
                        }
                    } else {
                        Modifier
                    },
                )
                .padding(horizontal = 20.dp, vertical = 14.dp),
            contentAlignment = Alignment.CenterStart,
        ) {
            AnimatedContent(recording, transitionSpec = { fadeIn(tween(180)) togetherWith fadeOut(tween(120)) }, label = "field") { now ->
                if (now) {
                    Listening(level, handsFree)
                } else {
                    BasicTextField(
                        value = typed,
                        onValueChange = { typed = it },
                        modifier = Modifier.fillMaxWidth().focusRequester(typingField).onFocusChanged {
                            if (it.isFocused && onTyping != null) scope.launch {
                                onTyping()
                                delay(80)  // the window takes the keyboard first
                                keyboard?.show()
                            }
                        },
                        textStyle = MaterialTheme.typography.bodyLarge.copy(color = colors.text),
                        cursorBrush = SolidColor(colors.accent),
                        keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Send),
                        keyboardActions = KeyboardActions(onSend = { send() }),
                        maxLines = 5,
                        decorationBox = { field ->
                            if (typed.isEmpty()) Text("Message Natro", color = colors.faint, style = MaterialTheme.typography.bodyLarge)
                            field()
                        },
                    )
                }
            }
        }
        Spacer(Modifier.width(8.dp))
        AnimatedContent(
            targetState = typed.isNotBlank() && !recording,
            transitionSpec = {
                (scaleIn(spring(dampingRatio = 0.6f, stiffness = 600f), initialScale = 0.6f) + fadeIn()) togetherWith
                    (scaleOut(targetScale = 0.6f) + fadeOut(tween(100)))
            },
            label = "button",
        ) { canSend ->
            if (canSend) {
                Box(
                    Modifier.size(52.dp).clip(CircleShape).background(colors.accent).clickable(onClick = ::send)
                        .semantics { contentDescription = "Send" },
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(Glyph.Send, colors.onAccent, size = 22.dp, weight = 2.4.dp)
                }
            } else {
                MicButton(recording, level, actions)
            }
        }
    }
}

/**
 * The mic: held, it records until let go; tapped, Natro listens until the owner stops talking.
 * While she listens on her own (a tap, the gesture, "OK Natro"), a tap sends what she has.
 */
@Composable
private fun MicButton(recording: Boolean, level: Float, actions: AppActions) {
    val colors = palette
    val haptics = LocalHapticFeedback.current
    val scale by animateFloatAsState(if (recording) 1.1f else 1f, spring(dampingRatio = 0.5f, stiffness = 500f), label = "mic")
    val halo by animateFloatAsState(if (recording) level else 0f, tween(90), label = "halo")
    Box(
        Modifier.size(52.dp)
            .graphicsLayer {
                scaleX = scale
                scaleY = scale
            }
            .drawBehind {
                if (recording) drawCircle(colors.accent.copy(alpha = 0.25f), radius = size.minDimension / 2 * (1.12f + halo * 0.55f))
            }
            .clip(CircleShape)
            .background(colors.accent)
            .semantics { contentDescription = if (recording) "Send what Natro heard" else "Hold to talk, or tap" }
            .pointerInput(Unit) {
                detectTapGestures(onPress = {
                    if (Natro.recording.value) {
                        Natro.stopRecording()
                        return@detectTapGestures
                    }
                    if (!actions.microphoneAllowed()) {
                        actions.askMicrophone()
                        return@detectTapGestures
                    }
                    haptics.performHapticFeedback(HapticFeedbackType.ToggleOn)
                    Natro.startRecording()
                    val pressed = System.currentTimeMillis()
                    val released = tryAwaitRelease()
                    when {
                        !released -> Natro.dropRecording()
                        System.currentTimeMillis() - pressed < TAP_MILLIS -> Natro.keepListening()
                        else -> {
                            haptics.performHapticFeedback(HapticFeedbackType.ToggleOff)
                            Natro.stopRecording()
                        }
                    }
                })
            },
        contentAlignment = Alignment.Center,
    ) {
        Icon(Glyph.Mic, colors.onAccent, size = 24.dp, weight = 2.2.dp)
    }
}

private const val TAP_MILLIS = 300L

/** Bars that move with the owner's voice, while she listens. */
@Composable
private fun Listening(level: Float, handsFree: Boolean) {
    val colors = palette
    val wave = rememberInfiniteTransition(label = "listening")
    val phase by wave.animateFloat(0f, 1f, infiniteRepeatable(tween(900, easing = LinearEasing)), label = "phase")
    Row(verticalAlignment = Alignment.CenterVertically) {
        Row(horizontalArrangement = Arrangement.spacedBy(3.dp), verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.height(22.dp)) {
            listOf(0.55f, 0.85f, 1f, 0.75f, 0.5f).forEachIndexed { i, share ->
                val sway = 0.75f + 0.25f * sin(2 * PI * (phase + i * 0.2f)).toFloat()
                Box(Modifier.width(3.dp).height(4.dp + 18.dp * share * (0.15f + 0.85f * level) * sway)
                    .clip(CircleShape).background(colors.accent))
            }
        }
        Spacer(Modifier.width(12.dp))
        Text(if (handsFree) "Listening… tap the mic to send" else "Listening… let go to send", color = colors.muted,
            style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
internal fun OffBar(actions: AppActions) {
    val colors = palette
    Row(
        Modifier.fillMaxWidth().navigationBarsPadding().padding(12.dp).clip(RoundedCornerShape(26.dp))
            .background(colors.raised).padding(start = 18.dp, end = 6.dp, top = 6.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(Glyph.Power, colors.muted, size = 20.dp)
        Spacer(Modifier.width(10.dp))
        Text("Natro is off", color = colors.muted, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
        PillButton("Turn on", colors.accent, colors.onAccent, Modifier.width(110.dp)) { actions.setPower(true) }
    }
}
