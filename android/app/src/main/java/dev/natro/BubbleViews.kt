package dev.natro

import android.content.Intent
import android.graphics.BlurMaskFilter
import android.net.Uri
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.SizeTransform
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.MutableTransitionState
import androidx.compose.animation.core.animateDpAsState
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
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.ClipOp
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.LinearGradientShader
import androidx.compose.ui.graphics.Paint
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.Shader
import androidx.compose.ui.graphics.ShaderBrush
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.addOutline
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.roundToInt

// The desktop dock's look (desktop/src/app.css and Dock.svelte): see-through glass with a soft
// shadow and a lit top edge, light or dark with the phone, and the dot. The glass is a little
// thicker than the desktop's, as any app can be behind it.

/** One look of the glass: its tint (thick for the cards' words), edge, lit top edge, shadow, words, quiet buttons and links. */
internal class Glass(
    val fill: Color,
    val thick: Color,
    val border: Color,
    val highlight: Color,
    val shadow: Color,
    val text: Color,
    val dim: Color,
    val button: Color,
    val link: Color,
)

private val LIGHT_GLASS = Glass(
    fill = Color.White.copy(alpha = 0.6f),
    thick = Color.White.copy(alpha = 0.82f),
    border = Color.White.copy(alpha = 0.6f),
    highlight = Color.White,
    shadow = Color.Black.copy(alpha = 0.16f),
    text = Color(0xFF333333),
    dim = Color(0xFF333333).copy(alpha = 0.4f),
    button = Color.Black.copy(alpha = 0.08f),
    link = Color(0xFF0A7A0D),
)

private val DARK_GLASS = Glass(
    fill = Color(0xFF1E1E1E).copy(alpha = 0.6f),
    thick = Color(0xFF1E1E1E).copy(alpha = 0.82f),
    border = Color.White.copy(alpha = 0.2f),
    highlight = Color.White.copy(alpha = 0.4f),
    shadow = Color.Black.copy(alpha = 0.35f),
    text = Color.White,
    dim = Color.White.copy(alpha = 0.4f),
    button = Color.White.copy(alpha = 0.14f),
    link = Color(0xFF4CD64F),
)

@Composable
private fun glass() = if (isSystemInDarkTheme()) DARK_GLASS else LIGHT_GLASS

// The dot's colours, each a gradient from deep to bright as on the desktop: green, amber while she
// asks, red when something is wrong, grey while she's off.
internal val GREEN = Color(0xFF0FB912)
private val GREEN_DEEP = Color(0xFF10820C)
private val AMBER = Color(0xFFF5A623)
private val AMBER_DEEP = Color(0xFFC27803)
private val RED = Color(0xFFFF453A)
private val RED_DEEP = Color(0xFFB3261E)
private val GREY = Color(0xFFA0A0A0)
private val GREY_DEEP = Color(0xFF707070)

private val ORB = 18.dp
private val SHADOW_BLUR = 10.dp
private val SHADOW_DROP = 3.dp

/**
 * The desktop's glass on a shape: a soft shadow around it (not under it: the glass is see-through),
 * the tint, and a thin edge lit along the top.
 */
private fun Modifier.glass(shape: Shape, look: Glass, fill: Color = look.fill) = this
    .drawWithCache {
        val outline = Path().apply { addOutline(shape.createOutline(size, layoutDirection, this@drawWithCache)) }
        val paint = Paint().apply {
            color = look.shadow
            asFrameworkPaint().maskFilter = BlurMaskFilter(SHADOW_BLUR.toPx(), BlurMaskFilter.Blur.NORMAL)
        }
        val drop = SHADOW_DROP.toPx()
        onDrawBehind {
            clipPath(outline, ClipOp.Difference) {
                drawIntoCanvas {
                    it.translate(0f, drop)
                    it.drawPath(outline, paint)
                    it.translate(0f, -drop)
                }
            }
        }
    }
    .clip(shape)
    .background(fill)
    .border(1.dp, Brush.verticalGradient(0f to look.highlight, 0.3f to look.border), shape)

/** The bubble itself: round glass, and the dot that shows what Natro is doing. */
@Composable
internal fun BubbleFace() {
    val shown by Bubble.shown
    val replying by Bubble.replyOpen
    val visible = remember { MutableTransitionState(false) }
    // While her reply is out, the bubble is the card (BubbleReply).
    LaunchedEffect(shown, replying) { visible.targetState = shown && !replying }
    val look = glass()
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        AnimatedVisibility(
            visibleState = visible,
            enter = scaleIn(spring(dampingRatio = 0.55f, stiffness = 500f), initialScale = 0.3f) + fadeIn(tween(140)),
            exit = scaleOut(tween(200), targetScale = 0.3f) + fadeOut(tween(180)),
        ) {
            val pressed by Bubble.pressed
            val over by Bubble.overTarget
            val scale by animateFloatAsState(
                when {
                    over -> 0.8f
                    pressed -> 0.9f
                    else -> 1f
                },
                spring(dampingRatio = 0.6f, stiffness = 700f), label = "press",
            )
            Box(
                Modifier
                    .size(64.dp)
                    .graphicsLayer {
                        scaleX = scale
                        scaleY = scale
                    }
                    .glass(CircleShape, look),
                contentAlignment = Alignment.Center,
            ) {
                Dot(Bubble.status.value?.kind, look)
            }
        }
    }
}

@Composable
private fun Dot(kind: BubbleKind?, look: Glass) {
    // A step is still thinking: only the label changes.
    val shows = if (kind == BubbleKind.Step) BubbleKind.Thinking else kind
    AnimatedContent(
        targetState = shows,
        transitionSpec = {
            (fadeIn(tween(200)) + scaleIn(spring(dampingRatio = 0.6f, stiffness = 500f), initialScale = 0.4f)) togetherWith
                (fadeOut(tween(160)) + scaleOut(tween(160), targetScale = 0.4f))
        },
        contentAlignment = Alignment.Center,
        label = "dot",
    ) { now ->
        when (now) {
            BubbleKind.Listening -> Orb(GREEN_DEEP, GREEN, pulse = true, voice = true)
            BubbleKind.Thinking, BubbleKind.Step -> Spinner(look.text)
            BubbleKind.Asking -> Orb(AMBER_DEEP, AMBER, pulse = true)
            BubbleKind.Done -> Check()
            BubbleKind.Problem -> Orb(RED_DEEP, RED)
            BubbleKind.Off -> Orb(GREY_DEEP, GREY)
            null -> Orb(GREEN_DEEP, GREEN)
        }
    }
}

/**
 * The desktop's dot: a glowing ball. With a pulse a soft ring keeps leaving it (listening, asking);
 * with the voice it swells and glows with the owner's loudness.
 */
@Composable
private fun Orb(deep: Color, bright: Color, pulse: Boolean = false, voice: Boolean = false) {
    val level by Bubble.level
    val smooth by animateFloatAsState(if (voice) level else 0f, tween(90), label = "level")
    val ring by rememberInfiniteTransition(label = "pulse").animateFloat(
        0f, 1f, infiniteRepeatable(tween(1700, easing = CubicBezierEasing(0.2f, 0.6f, 0.35f, 1f))), label = "ring",
    )
    Canvas(Modifier.size(56.dp)) {
        val radius = ORB.toPx() / 2 * (1 + smooth * 0.6f)
        if (pulse) drawCircle(bright.copy(alpha = 0.55f * (1 - ring)), radius * (1 + 2 * ring))
        val glow = (2.dp + 12.dp * smooth).toPx()
        drawCircle(
            Brush.radialGradient(
                radius / (radius + glow) to bright.copy(alpha = 0.7f), 1f to Color.Transparent,
                center = center, radius = radius + glow,
            ),
            radius + glow,
        )
        drawCircle(
            Brush.linearGradient(listOf(deep, bright), start = center - Offset(radius, radius), end = center + Offset(radius, radius)),
            radius,
        )
    }
}

/** A thin ring fading in as it turns, as on the desktop while she thinks. */
@Composable
private fun Spinner(color: Color) {
    val angle by rememberInfiniteTransition(label = "thinking").animateFloat(
        0f, 360f, infiniteRepeatable(tween(800, easing = LinearEasing)), label = "angle",
    )
    Canvas(Modifier.size(26.dp)) {
        val stroke = 2.5.dp.toPx()
        rotate(angle) {
            drawCircle(
                Brush.sweepGradient(0f to Color.Transparent, 0.1f to Color.Transparent, 1f to color),
                radius = size.minDimension / 2 - stroke / 2,
                style = Stroke(stroke),
            )
        }
    }
}

/** A green circle whose check draws itself (the desktop's 16-unit drawing). */
@Composable
private fun Check() {
    val drawn = remember { Animatable(0f) }
    LaunchedEffect(Unit) { drawn.animateTo(1f, tween(320, delayMillis = 120)) }
    Canvas(Modifier.size(28.dp)) {
        drawCircle(GREEN)
        val unit = size.minDimension / 16
        val points = listOf(Offset(4.6f, 8.3f), Offset(6.8f, 10.5f), Offset(11.4f, 5.7f)).map { it * unit }
        val first = (points[1] - points[0]).getDistance()
        val whole = first + (points[2] - points[1]).getDistance()
        val length = whole * drawn.value
        val path = Path().apply {
            moveTo(points[0].x, points[0].y)
            if (length <= first) {
                val at = points[0] + (points[1] - points[0]) * (length / first)
                lineTo(at.x, at.y)
            } else {
                lineTo(points[1].x, points[1].y)
                val at = points[1] + (points[2] - points[1]) * ((length - first) / (whole - first))
                lineTo(at.x, at.y)
            }
        }
        drawPath(path, Color.White, style = Stroke(1.8f * unit, cap = StrokeCap.Round, join = StrokeJoin.Round))
    }
}

/** What Natro is doing, in a few words, beside the bubble (or above it, at home): a glass pill. */
@Composable
internal fun BubbleLabel() {
    val status by Bubble.status
    val side by Bubble.labelSide
    val shown by Bubble.shown
    val moving by Bubble.moving
    val look = glass()
    val last = rememberLast(status?.label) ?: ""
    val thinking = status?.kind == BubbleKind.Thinking
    val replying by Bubble.replyOpen
    val visible = shown && !moving && !replying && status != null && status?.kind != BubbleKind.Asking
    val origin = when (side) {
        LabelSide.Right -> TransformOrigin(0f, 0.5f)
        LabelSide.Left -> TransformOrigin(1f, 0.5f)
        LabelSide.Above -> TransformOrigin(0.5f, 1f)
    }
    Box(
        // Room for the pill's shadow inside its window (Bubble.layout leaves it).
        Modifier.fillMaxSize().padding(Bubble.LABEL_SHADOW_DP.dp),
        contentAlignment = when (side) {
            LabelSide.Right -> Alignment.CenterStart
            LabelSide.Left -> Alignment.CenterEnd
            LabelSide.Above -> Alignment.BottomCenter
        },
    ) {
        AnimatedVisibility(
            visible = visible,
            enter = scaleIn(spring(dampingRatio = 0.7f, stiffness = 600f), initialScale = 0.4f, transformOrigin = origin) +
                fadeIn(tween(160)),
            exit = scaleOut(tween(160), targetScale = 0.6f, transformOrigin = origin) + fadeOut(tween(140)),
        ) {
            // Android draws this window see-through (it lets touches through): a thicker tint makes it match the bubble.
            val fill = look.fill.copy(alpha = minOf(1f, look.fill.alpha / Bubble.passThroughAlpha))
            Box(Modifier.glass(RoundedCornerShape(22.dp), look, fill).padding(horizontal = 20.dp, vertical = 12.dp)) {
                AnimatedContent(
                    targetState = last,
                    transitionSpec = {
                        (slideInVertically(tween(260, delayMillis = 70)) { it / 2 } + fadeIn(tween(260, delayMillis = 70))) togetherWith
                            (slideOutVertically(tween(160)) { -it / 2 } + fadeOut(tween(140))) using
                            SizeTransform(clip = false)
                    },
                    label = "label",
                ) { text ->
                    val style = TextStyle(color = look.text, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                    Text(text, style = if (thinking) style.copy(brush = shimmer(look)) else style, maxLines = 1,
                        overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 220.dp))
                }
            }
        }
    }
}

/** The desktop's "Thinking…": a dim band sweeping over the words. */
@Composable
private fun shimmer(look: Glass): Brush {
    val at by rememberInfiniteTransition(label = "shimmer").animateFloat(
        0f, 1f, infiniteRepeatable(tween(1500, easing = LinearEasing)), label = "at",
    )
    return object : ShaderBrush() {
        override fun createShader(size: Size): Shader {
            // A gradient three times the words' width, slid from -2 widths to 0 (background-size: 300%).
            val start = -2 * size.width * (1 - at)
            return LinearGradientShader(Offset(start, 0f), Offset(start + 3 * size.width, 0f),
                listOf(look.text, look.dim, look.text), listOf(0.3f, 0.5f, 0.7f))
        }
    }
}

/** The ✕ the bubble is dropped on to cancel, and a shade along the bottom, while it's dragged. Red with the bubble on it. */
@Composable
internal fun BubbleTarget() {
    val dragging by Bubble.dragging
    val over by Bubble.overTarget
    val y by Bubble.targetY
    val look = glass()
    AnimatedVisibility(dragging, enter = fadeIn(tween(160)), exit = fadeOut(tween(200))) {
        Box(Modifier.fillMaxSize().background(Brush.verticalGradient(listOf(Color.Transparent, Color.Black.copy(alpha = 0.45f))))) {
            val size by animateDpAsState(if (over) 68.dp else 56.dp, spring(dampingRatio = 0.55f, stiffness = 600f), label = "target")
            val fill by animateColorAsState(if (over) RED else look.fill, tween(150), label = "fill")
            val mark by animateColorAsState(if (over) Color.White else look.text, tween(150), label = "mark")
            val rise = remember { Animatable(1f) }
            LaunchedEffect(Unit) { rise.animateTo(0f, spring(dampingRatio = 0.75f, stiffness = 500f)) }
            Box(
                Modifier
                    .align(Alignment.TopCenter)
                    .offset { IntOffset(0, (y - size.toPx() / 2 + rise.value * 40.dp.toPx()).roundToInt()) }
                    .size(size)
                    .glass(CircleShape, look, fill),
                contentAlignment = Alignment.Center,
            ) {
                Icon(Glyph.Close, mark, size = 22.dp)
            }
        }
    }
}

/**
 * Her reply, grown out of the bubble as a glass card, filling in as she writes: a tap folds it back
 * into the bubble (so does its ✕). It's only as tall as its words, at most Bubble.replyMaxHeight,
 * and scrolls inside. Links open in the browser.
 */
@Composable
internal fun BubbleReply() {
    val open by Bubble.replyOpen
    val origin by Bubble.replyFrom
    val maxHeight by Bubble.replyMaxHeight
    val last = rememberLast(Bubble.reply.value)
    val look = glass()
    val visible = remember { MutableTransitionState(false) }
    LaunchedEffect(open) { visible.targetState = open }
    val haptics = LocalHapticFeedback.current
    val context = LocalContext.current
    // Not an activity's window: the browser opens in its own task.
    val links = remember(context) {
        object : UriHandler {
            override fun openUri(uri: String) {
                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(uri)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
        }
    }
    AnimatedVisibility(
        visibleState = visible,
        enter = scaleIn(spring(dampingRatio = 0.75f, stiffness = 420f), initialScale = 0.15f, transformOrigin = origin) +
            fadeIn(tween(140)),
        exit = scaleOut(tween(220), targetScale = 0.15f, transformOrigin = origin) + fadeOut(tween(200)),
    ) {
        val reply = last ?: return@AnimatedVisibility
        val blocks = remember(reply.text) { Markdown.blocks(reply.text) }
        val words = Markdown.Look(link = look.link, code = look.button)
        val scroll = rememberScrollState()
        // While she writes, keep the newest words in view.
        LaunchedEffect(scroll) {
            snapshotFlow { scroll.maxValue }.collect { if (Bubble.reply.value?.done == false) scroll.scrollTo(it) }
        }
        fun fold() {
            haptics.performHapticFeedback(HapticFeedbackType.ContextClick)
            Bubble.fold()
        }
        CompositionLocalProvider(LocalUriHandler provides links) {
            Column(
                Modifier
                    .padding(Bubble.CARD_SHADOW_DP.dp)
                    .fillMaxWidth()
                    .heightIn(max = maxHeight.dp)
                    .glass(RoundedCornerShape(24.dp), look, look.thick)
                    // A touch on the card gives a short reply its time to be read again.
                    .pointerInput(Unit) {
                        awaitEachGesture {
                            awaitFirstDown(requireUnconsumed = false)
                            Bubble.timeReply()
                        }
                    }
                    .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = ::fold),
            ) {
                Row(Modifier.padding(start = 18.dp, end = 10.dp, top = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                    WritingDot(!reply.done)
                    Spacer(Modifier.width(8.dp))
                    Text("Natro", color = look.dim, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                    Box(
                        Modifier.size(28.dp).clip(CircleShape).background(look.button).clickable(onClick = ::fold),
                        contentAlignment = Alignment.Center,
                    ) {
                        Icon(Glyph.Close, look.text, size = 12.dp, weight = 2.dp)
                    }
                }
                Column(
                    Modifier.weight(1f, fill = false).verticalScroll(scroll)
                        .padding(start = 18.dp, end = 18.dp, top = 4.dp, bottom = 16.dp),
                ) {
                    blocks.forEachIndexed { i, block ->
                        val list = block is Markdown.Block.Item && blocks.getOrNull(i - 1) is Markdown.Block.Item
                        if (i > 0) Spacer(Modifier.height(if (list) 4.dp else 8.dp))
                        ReplyBlock(block, look, words)
                    }
                }
            }
        }
    }
}

@Composable
private fun ReplyBlock(block: Markdown.Block, look: Glass, words: Markdown.Look) {
    val size = 15.sp
    val height = 21.sp
    when (block) {
        is Markdown.Block.Paragraph ->
            Text(Markdown.inline(block.lines.joinToString("\n"), words), color = look.text, fontSize = size, lineHeight = height)
        is Markdown.Block.Heading ->
            Text(Markdown.inline(block.text, words), color = look.text, fontSize = size, lineHeight = height,
                fontWeight = FontWeight.SemiBold)
        is Markdown.Block.Item -> Row {
            Text(block.marker, color = look.dim, fontSize = size, lineHeight = height, modifier = Modifier.widthIn(min = 16.dp))
            Spacer(Modifier.width(4.dp))
            Text(Markdown.inline(block.text, words), color = look.text, fontSize = size, lineHeight = height)
        }
        is Markdown.Block.Code ->
            Text(block.text, color = look.text, fontSize = 13.sp, lineHeight = 18.sp, fontFamily = FontFamily.Monospace,
                modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(look.button).padding(10.dp))
    }
}

/** The desktop's dot beside "Natro": green, and a ring leaving it while she writes. */
@Composable
private fun WritingDot(writing: Boolean) {
    val ring by rememberInfiniteTransition(label = "writing").animateFloat(
        0f, 1f, infiniteRepeatable(tween(1700, easing = CubicBezierEasing(0.2f, 0.6f, 0.35f, 1f))), label = "ring",
    )
    Canvas(Modifier.size(8.dp)) {
        if (writing) drawCircle(GREEN.copy(alpha = 0.55f * (1 - ring)), size.minDimension / 2 * (1 + 2 * ring))
        drawCircle(Brush.linearGradient(listOf(GREEN_DEEP, GREEN)))
    }
}

/** Natro's question, unfolding from the bubble as a glass card, with No and Yes. */
@Composable
internal fun BubbleCard() {
    val asked by Bubble.question
    val shown by Bubble.shown
    val above by Bubble.cardAbove
    val look = glass()
    val last = rememberLast(asked)
    val visible = remember { MutableTransitionState(false) }
    LaunchedEffect(asked != null && shown) { visible.targetState = asked != null && shown }
    val haptics = LocalHapticFeedback.current
    AnimatedVisibility(
        visibleState = visible,
        enter = scaleIn(spring(dampingRatio = 0.7f, stiffness = 500f), initialScale = 0.85f,
            transformOrigin = TransformOrigin(0.5f, if (above) 1f else 0f)) + fadeIn(tween(160)),
        exit = scaleOut(tween(180), targetScale = 0.9f) + fadeOut(tween(160)),
    ) {
        val question = last ?: return@AnimatedVisibility
        Column(
            Modifier
                .padding(Bubble.CARD_SHADOW_DP.dp)
                .fillMaxWidth()
                .glass(RoundedCornerShape(24.dp), look, look.thick)
                .padding(18.dp),
        ) {
            Text(question.text, color = look.text, fontSize = 16.sp, fontWeight = FontWeight.SemiBold, lineHeight = 21.sp)
            if (question.details.isNotBlank()) {
                Spacer(Modifier.height(4.dp))
                Text(question.details, color = look.text.copy(alpha = 0.65f), fontSize = 13.sp, maxLines = 3,
                    overflow = TextOverflow.Ellipsis)
            }
            Spacer(Modifier.height(16.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                CardButton("No", SolidColor(look.button), look.text, Modifier.weight(1f)) {
                    haptics.performHapticFeedback(HapticFeedbackType.Reject)
                    Natro.answer(false)
                }
                CardButton("Yes", Brush.linearGradient(listOf(GREEN_DEEP, GREEN)), Color.White, Modifier.weight(1f)) {
                    haptics.performHapticFeedback(HapticFeedbackType.Confirm)
                    Natro.answer(true)
                }
            }
        }
    }
}

@Composable
private fun CardButton(text: String, background: Brush, color: Color, modifier: Modifier, onClick: () -> Unit) {
    Box(
        modifier.height(46.dp).clip(RoundedCornerShape(23.dp)).background(background).clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, color = color, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
    }
}
