package dev.natro

import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// What's folded under one of her replies in the chat: what she heard, her steps, the tools, her questions, the model
// and the cost; and a request made on the PC that used the phone.

@Composable
internal fun DetailsToggle(turn: Turn, open: Boolean, toggle: () -> Unit) {
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
internal fun FromPc(turn: Turn, open: Boolean, toggle: () -> Unit) {
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
internal fun Details(turn: Turn) {
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
