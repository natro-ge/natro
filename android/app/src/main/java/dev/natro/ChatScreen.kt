package dev.natro

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import java.text.DateFormat
import java.util.Date
import kotlinx.coroutines.delay

/** The chat, shared with the PC: the conversation, her questions, requests kept for the phone, and a field to type
 * or talk (or send a picture). The turns are ChatTurns.kt's (what's under a reply ChatDetails.kt's), and the field
 * ChatInput.kt's. */
@Composable
fun ChatScreen(setupLeft: Int, openSettings: () -> Unit, actions: AppActions) {
    val colors = palette
    val turns by Natro.turns.collectAsStateWithLifecycle()
    val status by Natro.status.collectAsStateWithLifecycle()
    val question by Natro.question.collectAsStateWithLifecycle()
    val kept by Natro.kept.collectAsStateWithLifecycle()
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
            kept.forEach { KeptCard(it) }
            if (power) Composer(actions) else OffBar(actions)
        }
        Notice(Modifier.align(Alignment.TopCenter))
    }
}

@Composable
private fun TopBar(status: Natro.Status, openSettings: () -> Unit) {
    val colors = palette
    var starting by remember { mutableStateOf(false) }
    if (starting) {
        AlertDialog(
            onDismissRequest = { starting = false },
            title = { Text("Start a new chat?") },
            text = { Text("The chat goes, here and on the PC, and Natro starts a new conversation.") },
            confirmButton = {
                TextButton(onClick = {
                    Natro.newChat()
                    starting = false
                }) { Text("New chat", color = colors.accentText) }
            },
            dismissButton = { TextButton(onClick = { starting = false }) { Text("Keep", color = colors.text) } },
            containerColor = colors.surface,
        )
    }
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
        WorksOnChip()
        RoundIcon(Glyph.NewChat, "New chat", { starting = true })
        RoundIcon(Glyph.Settings, "Settings", openSettings)
    }
}

/**
 * Where Natro works for what's asked here (WorksOn): this phone, or green when it's the PC, dimmed while the
 * PC is offline (she works here then). A tap moves her to the other device.
 */
@Composable
private fun WorksOnChip() {
    val colors = palette
    val context = LocalContext.current
    val haptics = LocalHapticFeedback.current
    val worksOn by Prefs.worksOn.collectAsStateWithLifecycle()
    val pcOnline by Natro.pcOnline.collectAsStateWithLifecycle()
    val onPc = worksOn == WorksOn.PC
    val fill by animateColorAsState(if (onPc) colors.accent else Color.Transparent, tween(200), label = "chip")
    val ink = if (onPc) colors.onAccent else colors.muted
    val description = when {
        !onPc -> "Natro works on this phone. Tap to work on the PC."
        pcOnline -> "Natro works on your PC. Tap to work on this phone."
        else -> "Natro is set to work on your PC, which is offline, so she works here. Tap to work on this phone."
    }
    Row(
        Modifier
            .padding(end = 4.dp)
            .graphicsLayer { alpha = if (onPc && !pcOnline) 0.5f else 1f }
            .clip(RoundedCornerShape(16.dp))
            .background(fill)
            .border(1.dp, if (onPc) Color.Transparent else colors.faint.copy(alpha = 0.5f), RoundedCornerShape(16.dp))
            .clickable {
                haptics.performHapticFeedback(HapticFeedbackType.Confirm)
                WorksOn.set(context, if (onPc) WorksOn.PHONE else WorksOn.PC)
            }
            .semantics { contentDescription = description }
            .padding(start = 9.dp, end = 11.dp, top = 6.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(if (onPc) Glyph.Pc else Glyph.Phone, ink, size = 16.dp, weight = 1.8.dp)
        Spacer(Modifier.width(5.dp))
        Text(if (onPc) "PC" else "Phone", color = ink, fontSize = 13.sp, fontWeight = FontWeight.Medium)
    }
}

@Composable
fun StatusLine(status: Natro.Status, modifier: Modifier = Modifier) {
    val colors = palette
    val worksOn by Prefs.worksOn.collectAsStateWithLifecycle()
    val pcOnline by Natro.pcOnline.collectAsStateWithLifecycle()
    val (text, color) = when (status) {
        Natro.Status.Online -> (WorksOn.status(worksOn, pcOnline)?.let { "Online · $it" } ?: "Online") to colors.accent
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

/** A request he made on the PC while the phone was offline: Run or Skip. */
@Composable
private fun KeptCard(kept: Kept) {
    val colors = palette
    val haptics = LocalHapticFeedback.current
    val time = remember(kept.time) { DateFormat.getTimeInstance(DateFormat.SHORT).format(Date(kept.time)) }
    Column(
        Modifier.padding(horizontal = 12.dp, vertical = 6.dp).fillMaxWidth().clip(RoundedCornerShape(24.dp))
            .background(colors.surface).border(1.dp, colors.line, RoundedCornerShape(24.dp)).padding(18.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(8.dp).clip(CircleShape).background(colors.accent))
            Spacer(Modifier.width(8.dp))
            Text("While your phone was offline, on your ${kept.from} at $time", color = colors.muted, fontSize = 12.sp,
                fontWeight = FontWeight.Medium)
        }
        Spacer(Modifier.height(8.dp))
        Text(kept.text, color = colors.text, fontSize = 17.sp, fontWeight = FontWeight.SemiBold, lineHeight = 23.sp)
        Spacer(Modifier.height(16.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            PillButton("Skip", colors.raised, colors.text, Modifier.weight(1f)) {
                haptics.performHapticFeedback(HapticFeedbackType.Reject)
                Natro.runKept(kept.id, run = false)
            }
            PillButton("Run", colors.accent, colors.onAccent, Modifier.weight(1f)) {
                haptics.performHapticFeedback(HapticFeedbackType.Confirm)
                Natro.runKept(kept.id, run = true)
            }
        }
    }
}

/** Natro's question before something risky, with No and Yes. */
@Composable
internal fun QuestionCard(question: Natro.Question) {
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
