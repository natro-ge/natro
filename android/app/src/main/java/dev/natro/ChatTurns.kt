package dev.natro

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.SizeTransform
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.LinearEasing
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
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlin.math.PI
import kotlin.math.sin

// The chat's turns (ChatScreen.kt holds them): what he asked, her reply or what she's doing, her questions about the
// PC's requests, pictures, and what's folded under a reply (ChatDetails.kt).

@Composable
internal fun Conversation(turns: List<Turn>, modifier: Modifier = Modifier.fillMaxSize()) {
    val list = rememberLazyListState()
    val newest = turns.lastOrNull()?.id
    // The newest turn is at the bottom (the list is upside down): keep it in view, unless the owner scrolled back.
    LaunchedEffect(newest) {
        if (list.firstVisibleItemIndex <= 1) list.animateScrollToItem(0)
    }
    LazyColumn(
        state = list,
        reverseLayout = true,
        modifier = modifier,
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
        } else if (turn.reminder || turn.alert) {
            ReminderLabel(if (turn.alert) "Heads-up" else "Reminder")
            Theirs(turn)
        } else if (turn.job.isNotBlank()) {
            JobLabel(turn.job, turn.routine)
            Theirs(turn)
            OtherQuestion(turn)
            if (turn.hasDetails) DetailsToggle(turn, open) { open = !open }
        } else {
            if (turn.device.isNotBlank() || turn.queued.isNotBlank()) Where(turn)
            if (turn.sent) {
                Pictures(turn.pictures, mine = true)
            } else {
                Mine(turn)
                Spacer(Modifier.height(10.dp))
                Theirs(turn)
                if (turn.from == Turn.From.Other) OtherQuestion(turn)
                if (turn.pictures.isNotEmpty()) Pictures(turn.pictures, mine = false)
                if (turn.hasDetails) DetailsToggle(turn, open) { open = !open }
            }
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

/** Where a request was made, when it wasn't here: "On your PC". */
@Composable
private fun Where(turn: Turn) {
    val text = if (turn.queued.isNotBlank()) "Asked on your ${turn.queued} while the phone was offline"
        else "On your ${turn.device}"
    Text(text, color = palette.faint, fontSize = 11.5.sp, fontWeight = FontWeight.Medium, textAlign = TextAlign.End,
        modifier = Modifier.fillMaxWidth().padding(end = 6.dp, bottom = 4.dp))
}

/** Above a reminder that went off (or what a watcher saw), on her side. */
@Composable
private fun ReminderLabel(text: String) {
    Text(text, color = palette.faint, fontSize = 11.5.sp, fontWeight = FontWeight.Medium,
        modifier = Modifier.fillMaxWidth().padding(start = 6.dp, bottom = 4.dp))
}

/** Above a background job, on her side: its task (or the routine's name, for a routine's run), on one line. */
@Composable
private fun JobLabel(task: String, routine: String) {
    Text(if (routine.isNotBlank()) "Routine: $routine" else "In the background: $task", color = palette.faint, fontSize = 11.5.sp, fontWeight = FontWeight.Medium,
        maxLines = 1, overflow = TextOverflow.Ellipsis,
        modifier = Modifier.fillMaxWidth().padding(start = 6.dp, end = 40.dp, bottom = 4.dp))
}

/** A question about the PC's request (or a background job's): answered here too. */
@Composable
private fun OtherQuestion(turn: Turn) {
    val colors = palette
    val asked = turn.questions.lastOrNull()?.takeIf { it.answer.isEmpty() && turn.working && it.id.isNotBlank() } ?: return
    Column(
        Modifier.padding(top = 8.dp, end = 40.dp).clip(RoundedCornerShape(20.dp)).background(colors.surface)
            .border(1.dp, colors.line, RoundedCornerShape(20.dp)).padding(14.dp),
    ) {
        Text(asked.question, color = colors.text, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            PillButton("No", colors.raised, colors.text, Modifier.width(96.dp)) { Natro.answerFor(asked, false) }
            PillButton("Yes", colors.accent, colors.onAccent, Modifier.width(96.dp)) { Natro.answerFor(asked, true) }
        }
    }
}

/** The chat's pictures: on the right if he sent them, else under her reply. A tap opens one large (PictureViewer). */
@Composable
private fun Pictures(ids: List<String>, mine: Boolean) {
    val colors = palette
    val all by Natro.pictures.collectAsStateWithLifecycle()
    var viewing by remember { mutableStateOf<String?>(null) }
    Column(
        Modifier.fillMaxWidth().padding(top = if (mine) 0.dp else 8.dp),
        horizontalAlignment = if (mine) Alignment.End else Alignment.Start,
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        ids.forEach { id ->
            val bytes = all[id]
            val image = remember(id, bytes) { bytes?.let { Pictures.decode(it, THUMB_PIXELS)?.asImageBitmap() } }
            if (image == null) {
                Text("Picture no longer here", color = colors.faint, fontSize = 13.sp, modifier = Modifier.padding(6.dp))
            } else {
                // At most 300 wide and 360 tall: a phone's screenshot is tall, and mostly as dark as the chat.
                val ratio = image.width.toFloat() / image.height
                val shape = RoundedCornerShape(18.dp)
                Image(
                    image, contentDescription = "Picture", contentScale = ContentScale.Fit,
                    modifier = Modifier.padding(start = if (mine) 48.dp else 0.dp, end = if (mine) 0.dp else 40.dp)
                        .width(minOf(300f, 360f * ratio).dp).aspectRatio(ratio)
                        .clip(shape).border(1.dp, colors.line, shape).clickable { viewing = id },
                )
            }
        }
    }
    viewing?.let { id -> all[id]?.let { PictureViewer(it) { viewing = null } } }
}

private const val THUMB_PIXELS = 900

/** What the owner asked: on the right, in green (a little quieter when he asked on the PC). */
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
            Modifier.padding(start = 48.dp).clip(RoundedCornerShape(22.dp, 22.dp, 6.dp, 22.dp))
                .background(if (turn.from == Turn.From.Other) colors.mine.copy(alpha = 0.72f) else colors.mine)
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
