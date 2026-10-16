package dev.natro

import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
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
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle

/**
 * Settings > Test the wake word: the owner says it a few times and sees which tries Natro caught,
 * with either microphone mode, to compare them. While this is open the wake listener only listens
 * for the test (WakeTest), even with the switch off.
 */
@Composable
fun WakeTestScreen(back: () -> Unit, actions: AppActions) {
    val colors = palette
    val context = LocalContext.current
    val power by Prefs.power.collectAsStateWithLifecycle()
    val listening by NatroService.mic.collectAsStateWithLifecycle()
    val tries by WakeTest.tries.collectAsStateWithLifecycle()
    val mic by WakeTest.mic.collectAsStateWithLifecycle()
    val level by WakeTest.level.collectAsStateWithLifecycle()
    DisposableEffect(Unit) {
        WakeTest.start(context.cacheDir)
        onDispose { WakeTest.stop() }
    }

    Column(
        Modifier.fillMaxSize().background(colors.background)
            .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Horizontal)),
    ) {
        Row(
            Modifier.fillMaxWidth().statusBarsPadding().padding(start = 6.dp, end = 16.dp, top = 6.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            RoundIcon(Glyph.Back, "Back", back)
            Spacer(Modifier.width(6.dp))
            Text("Test the wake word", color = colors.text, fontSize = 20.sp, fontWeight = FontWeight.SemiBold)
        }
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp).navigationBarsPadding(),
        ) {
            Column(
                Modifier.padding(top = 8.dp).fillMaxWidth().clip(RoundedCornerShape(24.dp)).background(colors.surface)
                    .padding(horizontal = 20.dp, vertical = 24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                LevelDot(if (listening) level else 0f)
                Spacer(Modifier.height(16.dp))
                Text("Say \"OK Natro\" or \"Hey Natro\"", color = colors.text, fontSize = 18.sp, fontWeight = FontWeight.SemiBold,
                    textAlign = TextAlign.Center)
                Spacer(Modifier.height(6.dp))
                Text(
                    when {
                        !power -> "Turn Natro on to test."
                        !actions.microphoneAllowed() -> "Natro needs the microphone first."
                        !listening -> "Starting the microphone…"
                        else -> "A few times, as you would to the phone, with a pause after each. Nothing is sent anywhere."
                    },
                    color = colors.muted, fontSize = 14.sp, lineHeight = 19.sp, textAlign = TextAlign.Center,
                )
                if (power && !actions.microphoneAllowed()) {
                    Spacer(Modifier.height(12.dp))
                    PillButton("Allow the microphone", colors.accent, colors.onAccent, onClick = actions.askMicrophone)
                }
            }

            Group("Microphone") {
                Row(Modifier.fillMaxWidth().padding(6.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    WakeTest.Mic.entries.forEach { mode ->
                        val chosen = mode == mic
                        Box(
                            Modifier.weight(1f).height(42.dp).clip(RoundedCornerShape(16.dp))
                                .background(if (chosen) colors.accent.copy(alpha = 0.16f) else colors.surface)
                                .clickable { WakeTest.use(mode) },
                            contentAlignment = Alignment.Center,
                        ) {
                            Text(mode.label, color = if (chosen) colors.accentText else colors.muted, fontSize = 14.sp,
                                fontWeight = if (chosen) FontWeight.SemiBold else FontWeight.Normal)
                        }
                    }
                }
                Text("Try both: the one that catches more can become Natro's.", color = colors.muted, fontSize = 13.sp,
                    modifier = Modifier.padding(start = 18.dp, end = 18.dp, bottom = 14.dp))
            }

            val caught = tries.count { it.phrase != null }
            Group(if (tries.isEmpty()) "Tries" else "Tries · $caught of ${tries.size} caught") {
                if (tries.isEmpty()) {
                    Text("None yet.", color = colors.faint, fontSize = 15.sp, modifier = Modifier.padding(18.dp))
                }
                Column(Modifier.animateContentSize()) {
                    tries.asReversed().forEachIndexed { i, attempt ->
                        if (i > 0) Line()
                        TryRow(attempt)
                    }
                }
            }
            if (tries.isNotEmpty()) {
                Spacer(Modifier.height(12.dp))
                Text("Start over", color = colors.danger, fontSize = 15.sp,
                    modifier = Modifier.clip(RoundedCornerShape(12.dp)).clickable { WakeTest.clear() }.padding(horizontal = 12.dp, vertical = 8.dp))
            }
            Spacer(Modifier.height(10.dp))
            Text("Each try is kept on the phone (the app's cache, wake-test) to tune the wake word to your voice.",
                color = colors.faint, fontSize = 12.sp, lineHeight = 17.sp, modifier = Modifier.padding(horizontal = 6.dp))
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun TryRow(attempt: WakeTest.Try) {
    val colors = palette
    val caught = attempt.phrase != null
    Row(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier.size(28.dp).clip(CircleShape).background((if (caught) colors.accent else colors.danger).copy(alpha = 0.16f)),
            contentAlignment = Alignment.Center,
        ) {
            Icon(if (caught) Glyph.Check else Glyph.Close, if (caught) colors.accentText else colors.danger, size = 16.dp, weight = 2.4.dp)
        }
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Text(if (caught) "Caught: ${attempt.phrase}" else "Missed", color = colors.text, fontSize = 16.sp)
            Spacer(Modifier.height(2.dp))
            Text("${attempt.mic.label} · ${loudnessWords(attempt.loudness)}", color = colors.muted, fontSize = 13.sp)
        }
        Text("#${attempt.number}", color = colors.faint, fontSize = 13.sp)
    }
}

/** How loud a try was, in words with the number (Loudness, raw phone levels; speech starts at SpeechEnd.MIN_SPEECH). */
private fun loudnessWords(loudness: Double) = when {
    loudness < 100 -> "quiet"
    loudness < 1_000 -> "normal"
    else -> "loud"
} + " (${loudness.toInt()})"

/** A green dot that swells with the microphone's level. */
@Composable
private fun LevelDot(level: Float) {
    val colors = palette
    val smooth by animateFloatAsState(level, tween(90), label = "level")
    Canvas(Modifier.size(72.dp)) {
        val radius = 14.dp.toPx() * (1 + smooth * 0.6f)
        drawCircle(colors.accent.copy(alpha = 0.18f), radius * (1.4f + smooth))
        drawCircle(colors.accent, radius)
    }
}
