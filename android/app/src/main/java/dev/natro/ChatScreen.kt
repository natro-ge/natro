package dev.natro

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.SizeTransform
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.delay
import kotlin.math.PI
import kotlin.math.sin

/** The chat: the conversation, her questions, and a field to type or talk. */
@Composable
fun ChatScreen(setupLeft: Int, openSettings: () -> Unit, actions: AppActions) {
    val colors = palette
    val turns by Natro.turns.collectAsStateWithLifecycle()
    val status by Natro.status.collectAsStateWithLifecycle()
    val question by Natro.question.collectAsStateWithLifecycle()
    val power by Prefs.power.collectAsStateWithLifecycle()
    Box(Modifier.fillMaxSize().background(colors.background)) {
        Column(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Horizontal))) {
            TopBar(status, openSettings)
            AnimatedVisibility(setupLeft > 0) { SetupBanner(setupLeft, openSettings) }
            Box(Modifier.weight(1f).fillMaxWidth()) {
                if (turns.isEmpty()) EmptyChat() else Conversation(turns)
            }
            val asked = rememberLast(question)
            AnimatedVisibility(
                visible = question != null,
                enter = expandVertically(spring(dampingRatio = 0.8f, stiffness = 500f)) + fadeIn(),
                exit = shrinkVertically(tween(200)) + fadeOut(tween(150)),
            ) {
                asked?.let { QuestionCard(it) }
            }
            if (power) Composer(actions) else OffBar(actions)
        }
        Notice(Modifier.align(Alignment.TopCenter))
    }
}

@Composable
private fun TopBar(status: Natro.Status, openSettings: () -> Unit) {
    val colors = palette
    Row(
        Modifier.fillMaxWidth().statusBarsPadding().padding(start = 16.dp, end = 8.dp, top = 6.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Logo(40.dp)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text("Natro", color = colors.text, fontSize = 20.sp, fontWeight = FontWeight.SemiBold)
            StatusLine(status)
        }
        RoundIcon(Glyph.Settings, "Settings", openSettings)
    }
}

@Composable
fun StatusLine(status: Natro.Status, modifier: Modifier = Modifier) {
    val colors = palette
    val (text, color) = when (status) {
        Natro.Status.Online -> "Online" to colors.accent
        Natro.Status.Connecting -> "Connecting…" to colors.warning
        Natro.Status.Off -> "Off" to colors.faint
        is Natro.Status.Offline -> "Offline · tap to retry" to colors.danger
    }
    val pulsing = rememberInfiniteTransition(label = "status")
    val pulse by pulsing.animateFloat(0.35f, 1f, infiniteRepeatable(tween(700), RepeatMode.Reverse), label = "pulse")
    Row(
        modifier.clip(RoundedCornerShape(8.dp)).clickable(enabled = status is Natro.Status.Offline) { Natro.reconnect() },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier.size(7.dp)
                .graphicsLayer { alpha = if (status == Natro.Status.Connecting) pulse else 1f }
                .clip(CircleShape).background(color),
        )
        Spacer(Modifier.width(6.dp))
        AnimatedContent(text, transitionSpec = { fadeIn(tween(200)) togetherWith fadeOut(tween(150)) }, label = "status") {
            Text(it, color = colors.muted, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

@Composable
private fun SetupBanner(left: Int, open: () -> Unit) {
    val colors = palette
    Row(
        Modifier.padding(horizontal = 12.dp, vertical = 4.dp).fillMaxWidth().clip(RoundedCornerShape(18.dp))
            .background(colors.accent.copy(alpha = 0.12f)).clickable(onClick = open)
            .padding(start = 16.dp, end = 10.dp, top = 12.dp, bottom = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("Finish setting up Natro", color = colors.text, fontSize = 15.sp, fontWeight = FontWeight.Medium,
            modifier = Modifier.weight(1f))
        Text(if (left == 1) "1 step" else "$left steps", color = colors.accentText, fontSize = 13.sp, fontWeight = FontWeight.Medium)
        Icon(Glyph.Chevron, colors.accentText, Modifier.graphicsLayer { rotationZ = -90f }, size = 18.dp)
    }
}

@Composable
private fun EmptyChat() {
    val colors = palette
    Column(
        Modifier.fillMaxSize().padding(horizontal = 40.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Logo(76.dp)
        Spacer(Modifier.height(22.dp))
        Text("Hi, I'm Natro.", color = colors.text, fontSize = 22.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(8.dp))
        Text("Hold the mic and talk, or tap it and I'll listen until you stop. From anywhere, long-press the power " +
            "button or say \"OK Natro\".", color = colors.muted, fontSize = 15.sp, lineHeight = 21.sp, textAlign = TextAlign.Center)
    }
}

@Composable
private fun Conversation(turns: List<Turn>) {
    val list = rememberLazyListState()
    val newest = turns.lastOrNull()?.id
    // The newest turn is at the bottom (the list is upside down): keep it in view, unless the owner scrolled back.
    LaunchedEffect(newest) {
        if (list.firstVisibleItemIndex <= 1) list.animateScrollToItem(0)
    }
    LazyColumn(
        state = list,
        reverseLayout = true,
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(top = 8.dp, bottom = 12.dp),
    ) {
        items(turns.asReversed(), key = { "${it.id}@${it.time}" }) { turn ->
            TurnView(turn, Modifier.animateItem(fadeInSpec = tween(220), fadeOutSpec = tween(150)))
        }
    }
}

@Composable
private fun TurnView(turn: Turn, modifier: Modifier) {
    var open by rememberSaveable(turn.id) { mutableStateOf(false) }
    Column(modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 7.dp)) {
        if (turn.from == Turn.From.Pc) {
            FromPc(turn, open) { open = !open }
        } else {
            Mine(turn)
            Spacer(Modifier.height(10.dp))
            Theirs(turn)
            if (turn.hasDetails) DetailsToggle(turn, open) { open = !open }
        }
        AnimatedVisibility(
            visible = open,
            enter = expandVertically(spring(dampingRatio = 0.85f, stiffness = 450f)) + fadeIn(tween(200)),
            exit = shrinkVertically(tween(220)) + fadeOut(tween(150)),
        ) {
            Details(turn)
        }
    }
}

/** What the owner asked: on the right, in green. */
@Composable
private fun Mine(turn: Turn) {
    val colors = palette
    val text = turn.asked.ifBlank {
        when (turn.state) {
            Turn.State.Working -> "Voice message…"
            Turn.State.Done -> "(not understood)"
            else -> "Voice message"
        }
    }
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
        Row(
            Modifier.padding(start = 48.dp).clip(RoundedCornerShape(22.dp, 22.dp, 6.dp, 22.dp)).background(colors.mine)
                .animateContentSize().padding(horizontal = 16.dp, vertical = 11.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (turn.from == Turn.From.Spoken) {
                Icon(Glyph.Mic, colors.onMine.copy(alpha = 0.55f), size = 16.dp, weight = 1.8.dp)
                Spacer(Modifier.width(6.dp))
            }
            Text(text, color = colors.onMine, style = MaterialTheme.typography.bodyLarge)
        }
    }
}

/** Natro's answer: on the left, or what she's doing while she works on it. */
@Composable
private fun Theirs(turn: Turn) {
    val colors = palette
    when {
        turn.state == Turn.State.Cancelled -> Text("Cancelled", color = colors.faint, fontSize = 13.sp,
            modifier = Modifier.padding(start = 6.dp))
        turn.working && turn.reply.isBlank() -> Working(turn.step)
        else -> Column(
            Modifier.padding(end = 40.dp).clip(RoundedCornerShape(22.dp, 22.dp, 22.dp, 6.dp)).background(colors.surface)
                .animateContentSize(spring(stiffness = 600f)).padding(horizontal = 16.dp, vertical = 11.dp),
        ) {
            if (turn.reply.isNotBlank()) Text(turn.reply, color = colors.text, style = MaterialTheme.typography.bodyLarge)
            if (turn.state == Turn.State.Failed) {
                if (turn.reply.isNotBlank()) Spacer(Modifier.height(4.dp))
                Text(turn.problem.ifBlank { "Something went wrong." }, color = colors.danger, fontSize = 14.sp)
            }
        }
    }
}

/** Three dots and her current step, while she works. */
@Composable
private fun Working(step: String) {
    val colors = palette
    Row(
        Modifier.clip(RoundedCornerShape(22.dp, 22.dp, 22.dp, 6.dp)).background(colors.surface)
            .animateContentSize().padding(horizontal = 16.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Dots(colors.accent)
        AnimatedContent(
            targetState = step,
            transitionSpec = {
                (slideInVertically { it / 2 } + fadeIn()) togetherWith (slideOutVertically { -it / 2 } + fadeOut()) using
                    SizeTransform(clip = false)
            },
            label = "step",
        ) { now ->
            if (now.isNotBlank()) {
                Text(now, color = colors.muted, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.padding(start = 10.dp))
            }
        }
    }
}

@Composable
private fun Dots(color: Color) {
    val wave = rememberInfiniteTransition(label = "dots")
    val phase by wave.animateFloat(0f, 1f, infiniteRepeatable(tween(1100, easing = LinearEasing)), label = "phase")
    Row(horizontalArrangement = Arrangement.spacedBy(5.dp), verticalAlignment = Alignment.CenterVertically) {
        repeat(3) { i ->
            val lift = sin(2 * PI * (phase - i * 0.16f)).toFloat().coerceAtLeast(0f)
            Box(
                Modifier.size(7.dp)
                    .graphicsLayer {
                        translationY = -lift * 3.dp.toPx()
                        alpha = 0.35f + 0.65f * lift
                    }
                    .clip(CircleShape).background(color),
            )
        }
    }
}

@Composable
private fun DetailsToggle(turn: Turn, open: Boolean, toggle: () -> Unit) {
    val colors = palette
    val turned by animateFloatAsState(if (open) 180f else 0f, spring(stiffness = 500f), label = "chevron")
    Row(
        Modifier.padding(top = 4.dp).clip(RoundedCornerShape(10.dp)).clickable(onClick = toggle)
            .padding(horizontal = 6.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(summary(turn), color = colors.faint, fontSize = 12.5.sp, fontWeight = FontWeight.Medium)
        Spacer(Modifier.width(2.dp))
        Icon(Glyph.Chevron, colors.faint, Modifier.graphicsLayer { rotationZ = turned }, size = 16.dp)
    }
}

private fun summary(turn: Turn) = buildList {
    add("Details")
    if (turn.tools.isNotEmpty()) add(if (turn.tools.size == 1) "1 tool" else "${turn.tools.size} tools")
    turn.seconds?.let { add("%.1f s".format(it)) }
}.joinToString(" · ")

/** A request made on the PC that used the phone. */
@Composable
private fun FromPc(turn: Turn, open: Boolean, toggle: () -> Unit) {
    val colors = palette
    val turned by animateFloatAsState(if (open) 180f else 0f, label = "chevron")
    val names = turn.tools.map { it.name.removePrefix("phone_") }.distinct().joinToString(", ")
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
        Row(
            Modifier.clip(RoundedCornerShape(14.dp)).clickable(onClick = toggle).padding(horizontal = 10.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("From the PC · used $names", color = colors.faint, fontSize = 12.5.sp, maxLines = 1,
                overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 280.dp))
            Icon(Glyph.Chevron, colors.faint, Modifier.graphicsLayer { rotationZ = turned }, size = 16.dp)
        }
    }
}

/** Everything folded under a reply: what she heard, her steps, the tools, her questions, the model and the cost. */
@Composable
private fun Details(turn: Turn) {
    val colors = palette
    Column(
        Modifier.padding(top = 6.dp).fillMaxWidth().clip(RoundedCornerShape(18.dp))
            .background(colors.raised.copy(alpha = if (colors.dark) 0.7f else 0.8f)).padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (turn.heard.isNotBlank()) Section("Heard") { Text(turn.heard, color = colors.text, fontSize = 14.sp) }
        if (turn.steps.isNotEmpty()) {
            Section("Steps") {
                turn.steps.forEach { Text("· $it", color = colors.text, fontSize = 14.sp, lineHeight = 20.sp) }
            }
        }
        if (turn.tools.isNotEmpty()) {
            Section("Tools") {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) { turn.tools.forEach { ToolView(it) } }
            }
        }
        if (turn.questions.isNotEmpty()) {
            Section("Asked") {
                turn.questions.forEach {
                    Text("${it.question} → ${it.answer.ifBlank { "no answer" }}", color = colors.text, fontSize = 14.sp)
                }
            }
        }
        val about = listOfNotNull(
            turn.model.takeIf { it.isNotBlank() },
            turn.cost?.let { "$%.4f".format(it) },
            turn.seconds?.let { "%.1f s".format(it) },
        )
        if (about.isNotEmpty()) Text(about.joinToString(" · "), color = colors.faint, fontSize = 12.sp, fontFamily = FontFamily.Monospace)
    }
}

@Composable
private fun Section(title: String, content: @Composable () -> Unit) {
    Column {
        Text(title.uppercase(), color = palette.faint, fontSize = 11.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.8.sp)
        Spacer(Modifier.height(4.dp))
        content()
    }
}

/** One tool that ran: whether it worked, its name and arguments, and (tapped) its whole result. */
@Composable
private fun ToolView(run: ToolRun) {
    val colors = palette
    var whole by remember { mutableStateOf(false) }
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).clickable { whole = !whole }.animateContentSize()) {
        val (glyph, color) = when {
            run.declined -> Glyph.Close to colors.faint
            run.ok -> Glyph.Check to colors.accentText
            else -> Glyph.Close to colors.danger
        }
        Icon(glyph, color, Modifier.padding(top = 1.dp), size = 16.dp, weight = 2.2.dp)
        Spacer(Modifier.width(8.dp))
        Column(Modifier.weight(1f)) {
            Text(run.name + if (run.declined) " (you said no)" else "", color = colors.text, fontSize = 13.sp,
                fontWeight = FontWeight.SemiBold, fontFamily = FontFamily.Monospace)
            if (run.args.isNotBlank() && run.args != "{}") {
                Text(run.args, color = colors.muted, fontSize = 12.sp, fontFamily = FontFamily.Monospace,
                    maxLines = if (whole) Int.MAX_VALUE else 2, overflow = TextOverflow.Ellipsis)
            }
            if (run.result.isNotBlank()) {
                Text(run.result, color = colors.muted, fontSize = 12.sp, fontFamily = FontFamily.Monospace, lineHeight = 16.sp,
                    maxLines = if (whole) Int.MAX_VALUE else 3, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.padding(top = 2.dp))
            }
        }
    }
}

/** Natro's question before something risky, with No and Yes. */
@Composable
private fun QuestionCard(question: Natro.Question) {
    val colors = palette
    val haptics = LocalHapticFeedback.current
    Column(
        Modifier.padding(horizontal = 12.dp, vertical = 6.dp).fillMaxWidth().clip(RoundedCornerShape(24.dp))
            .background(colors.surface).border(1.dp, colors.line, RoundedCornerShape(24.dp)).padding(18.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(8.dp).clip(CircleShape).background(colors.accent))
            Spacer(Modifier.width(8.dp))
            Text("Natro asks", color = colors.muted, fontSize = 12.sp, fontWeight = FontWeight.Medium)
        }
        Spacer(Modifier.height(8.dp))
        Text(question.text, color = colors.text, fontSize = 17.sp, fontWeight = FontWeight.SemiBold, lineHeight = 23.sp)
        if (question.details.isNotBlank()) {
            Spacer(Modifier.height(4.dp))
            Text(question.details, color = colors.muted, fontSize = 13.sp, maxLines = 4, overflow = TextOverflow.Ellipsis)
        }
        Spacer(Modifier.height(16.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            PillButton("No", colors.raised, colors.text, Modifier.weight(1f)) {
                haptics.performHapticFeedback(HapticFeedbackType.Reject)
                Natro.answer(false)
            }
            PillButton("Yes", colors.accent, colors.onAccent, Modifier.weight(1f)) {
                haptics.performHapticFeedback(HapticFeedbackType.Confirm)
                Natro.answer(true)
            }
        }
        Spacer(Modifier.height(10.dp))
        Text("Or say \"OK Natro, yes\" or \"no\".", color = colors.faint, fontSize = 12.sp,
            modifier = Modifier.align(Alignment.CenterHorizontally))
    }
}

/** The text field, and the mic button that turns into send once something is typed. */
@Composable
private fun Composer(actions: AppActions) {
    val colors = palette
    val haptics = LocalHapticFeedback.current
    var typed by rememberSaveable { mutableStateOf("") }
    val recording by Natro.recording.collectAsStateWithLifecycle()
    val handsFree by Natro.handsFree.collectAsStateWithLifecycle()
    val level by Natro.level.collectAsStateWithLifecycle()

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
        Box(
            Modifier.weight(1f).heightIn(min = 52.dp).clip(RoundedCornerShape(26.dp)).background(colors.raised)
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
                        modifier = Modifier.fillMaxWidth(),
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
private fun OffBar(actions: AppActions) {
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

/** A short message from Natro (say, the wake word couldn't start), for a few seconds. */
@Composable
private fun Notice(modifier: Modifier) {
    val colors = palette
    val notice by Natro.notice.collectAsStateWithLifecycle()
    val shown = rememberLast(notice) ?: ""
    LaunchedEffect(notice) {
        if (notice != null) {
            delay(4_000)
            Natro.notice.value = null
        }
    }
    AnimatedVisibility(
        visible = notice != null,
        modifier = modifier.statusBarsPadding().padding(top = 60.dp, start = 24.dp, end = 24.dp),
        enter = slideInVertically { -it } + fadeIn(),
        exit = slideOutVertically { -it } + fadeOut(),
    ) {
        Text(shown, color = colors.background, fontSize = 14.sp, textAlign = TextAlign.Center,
            modifier = Modifier.clip(RoundedCornerShape(16.dp)).background(colors.text)
                .clickable { Natro.notice.value = null }.padding(horizontal = 16.dp, vertical = 10.dp))
    }
}

@Composable
fun PillButton(text: String, background: Color, color: Color, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Box(
        modifier.height(44.dp).clip(RoundedCornerShape(22.dp)).background(background).clickable(onClick = onClick)
            .padding(horizontal = 16.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, color = color, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
fun RoundIcon(glyph: Glyph, description: String, onClick: () -> Unit, size: Dp = 44.dp) {
    Box(
        Modifier.size(size).clip(CircleShape).clickable(onClick = onClick).semantics { contentDescription = description },
        contentAlignment = Alignment.Center,
    ) {
        Icon(glyph, palette.text, size = 22.dp)
    }
}
