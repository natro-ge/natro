package dev.natro

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.requiredSize
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.Dp

/** The app's colors: the phone's light or dark, with Natro's green. */
@Immutable
data class Palette(
    val dark: Boolean,
    val background: Color,
    /** Cards and Natro's chat bubbles. */
    val surface: Color,
    /** The text field, the folded-out details, quieter buttons. */
    val raised: Color,
    val line: Color,
    val text: Color,
    val muted: Color,
    val faint: Color,
    val accent: Color,
    val onAccent: Color,
    /** Natro's green, dark or light enough to read as text. */
    val accentText: Color,
    /** The owner's chat bubbles. */
    val mine: Color,
    val onMine: Color,
    val danger: Color,
    val warning: Color,
)

private val Light = Palette(
    dark = false,
    background = Color(0xFFF4F6F4),
    surface = Color(0xFFFFFFFF),
    raised = Color(0xFFE9ECE9),
    line = Color(0xFFDFE3DF),
    text = Color(0xFF111412),
    muted = Color(0xFF5C645E),
    faint = Color(0xFF959D97),
    accent = Color(0xFF0FB912),
    onAccent = Color(0xFF04230A),
    accentText = Color(0xFF0A870D),
    mine = Color(0xFFD9F4D9),
    onMine = Color(0xFF0B290E),
    danger = Color(0xFFD33A3F),
    warning = Color(0xFFC98A0B),
)

private val Dark = Palette(
    dark = true,
    background = Color(0xFF0C0E0D),
    surface = Color(0xFF181B19),
    raised = Color(0xFF222624),
    line = Color(0xFF2A2F2C),
    text = Color(0xFFECEFEC),
    muted = Color(0xFF9AA29C),
    faint = Color(0xFF66706A),
    accent = Color(0xFF1EC421),
    onAccent = Color(0xFF04230A),
    accentText = Color(0xFF4CD64F),
    mine = Color(0xFF1C3A20),
    onMine = Color(0xFFDDF5DD),
    danger = Color(0xFFFF6B6B),
    warning = Color(0xFFF5B83D),
)

private val LocalPalette = staticCompositionLocalOf { Light }

val palette: Palette
    @Composable @ReadOnlyComposable get() = LocalPalette.current

@Composable
fun NatroTheme(content: @Composable () -> Unit) {
    val colors = if (isSystemInDarkTheme()) Dark else Light
    val scheme = if (colors.dark) {
        darkColorScheme(primary = colors.accent, onPrimary = colors.onAccent, background = colors.background,
            onBackground = colors.text, surface = colors.background, onSurface = colors.text,
            surfaceContainerHigh = colors.surface, onSurfaceVariant = colors.muted, outline = colors.line, error = colors.danger)
    } else {
        lightColorScheme(primary = colors.accent, onPrimary = colors.onAccent, background = colors.background,
            onBackground = colors.text, surface = colors.background, onSurface = colors.text,
            surfaceContainerHigh = colors.surface, onSurfaceVariant = colors.muted, outline = colors.line, error = colors.danger)
    }
    MaterialTheme(colorScheme = scheme) {
        CompositionLocalProvider(LocalPalette provides colors, content = content)
    }
}

/** The latest non-null value, kept for content that animates away after the value became null. */
@Composable
fun <T : Any> rememberLast(value: T?): T? {
    val last = remember { arrayOfNulls<Any>(1) }
    if (value != null) last[0] = value
    @Suppress("UNCHECKED_CAST")
    return last[0] as T?
}

/** Natro's mark, as on her icon: the N ending in the green dot, on her dark round. */
@Composable
fun Logo(size: Dp, modifier: Modifier = Modifier) {
    Box(modifier.size(size).clip(CircleShape).background(Color(0xFF14171B)), contentAlignment = Alignment.Center) {
        // The icon's drawing has an adaptive icon's margins: drawn larger, the round shows its middle
        // (up to 1.42 times, the dot's glow still fits inside).
        Image(painterResource(R.drawable.ic_launcher_foreground), contentDescription = null,
            modifier = Modifier.requiredSize(size * 1.34f))
    }
}
