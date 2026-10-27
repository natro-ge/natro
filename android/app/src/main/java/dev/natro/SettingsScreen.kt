package dev.natro

import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle

/** A setup step: what it lets Natro do, whether it's done, and the button that does it (or opens the right page). */
data class SetupStep(
    val title: String,
    val detail: String,
    val done: Boolean,
    /** Natro hardly works without it (the chat says so until it's done). */
    val essential: Boolean,
    val button: String,
    val action: () -> Unit,
)

/** Natro on or off, her voice, the setup, the chat and what she costs. */
@Composable
fun SettingsScreen(setup: List<SetupStep>, back: () -> Unit, openWakeTest: () -> Unit, actions: AppActions) {
    val colors = palette
    val power by Prefs.power.collectAsStateWithLifecycle()
    val wakeWord by Prefs.wakeWord.collectAsStateWithLifecycle()
    val speak by Prefs.speakReplies.collectAsStateWithLifecycle()
    val show by Prefs.showReplies.collectAsStateWithLifecycle()
    val messagesToPc by Prefs.messagesToPc.collectAsStateWithLifecycle()
    val paused by NatroService.wakePaused.collectAsStateWithLifecycle()
    val status by Natro.status.collectAsStateWithLifecycle()
    val spend by Natro.spend.collectAsStateWithLifecycle()
    var clearing by remember { mutableStateOf(false) }

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
            Text("Settings", color = colors.text, fontSize = 20.sp, fontWeight = FontWeight.SemiBold)
        }
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp).navigationBarsPadding(),
        ) {
            PowerCard(power, status, actions.setPower)

            Group("Voice") {
                SwitchRow(
                    "Listen for \"OK Natro\" or \"Hey Natro\"",
                    when {
                        !power -> "Off while Natro is off."
                        paused -> "Paused: it starts once this app is open (with the microphone allowed)."
                        else -> "Checked on the phone; nothing is sent before the wake word."
                    },
                    checked = wakeWord, enabled = power, onChange = actions.setWakeWord,
                )
                Line()
                LinkRow("Test the wake word", "Say it a few times and see which ones Natro catches.", enabled = power,
                    onClick = openWakeTest)
                Line()
                SwitchRow("Speak replies", "Natro says her answers out loud. Off, only what you ask her to say out loud.",
                    checked = speak, onChange = Prefs::setSpeakReplies)
                Line()
                SwitchRow("Show replies as text", "Her answer opens from the bubble over other apps; tap it to fold it back.",
                    checked = show, onChange = Prefs::setShowReplies)
            }

            Group("Your PC") {
                SwitchRow("Show messages on the PC", "Messages that come here (WhatsApp, Messenger, texts...) show " +
                    "on your PC while it's on, to read and answer there. Needs notification access.",
                    checked = messagesToPc, onChange = Prefs::setMessagesToPc)
            }

            val left = setup.count { !it.done }
            Group(if (left == 0) "Set up · all done" else "Set up · $left to do") {
                setup.forEachIndexed { i, step ->
                    if (i > 0) Line()
                    SetupRow(step)
                }
            }

            Group("Chat") {
                Row(
                    Modifier.fillMaxWidth().clickable { clearing = true }.padding(horizontal = 18.dp, vertical = 16.dp),
                ) {
                    Text("New chat (here and on the PC)", color = colors.danger, fontSize = 16.sp)
                }
            }

            Group("About") {
                InfoRow("This month", spend?.let { "$%.2f of $%.0f".format(it.spent, it.budget) } ?: "—")
                Line()
                InfoRow("Brain", BuildConfig.NATRO_SERVER.removePrefix("ws://").removePrefix("wss://"))
                Line()
                InfoRow("Version", BuildConfig.VERSION_NAME)
            }
            Spacer(Modifier.height(24.dp))
        }
    }

    if (clearing) {
        AlertDialog(
            onDismissRequest = { clearing = false },
            title = { Text("Start a new chat?") },
            text = { Text("The chat goes, here and on the PC, and Natro starts a new conversation. Her memory stays.") },
            confirmButton = {
                TextButton(onClick = {
                    Natro.newChat()
                    clearing = false
                }) { Text("New chat", color = colors.danger) }
            },
            dismissButton = { TextButton(onClick = { clearing = false }) { Text("Keep", color = colors.text) } },
            containerColor = colors.surface,
        )
    }
}

/** The switch for all of Natro, big, at the top. */
@Composable
private fun PowerCard(on: Boolean, status: Natro.Status, setPower: (Boolean) -> Unit) {
    val colors = palette
    val haptics = LocalHapticFeedback.current
    val round by animateColorAsState(if (on) colors.accent else colors.raised, label = "power")
    val glyph by animateColorAsState(if (on) colors.onAccent else colors.muted, label = "glyph")
    fun change(now: Boolean) {
        haptics.performHapticFeedback(if (now) HapticFeedbackType.ToggleOn else HapticFeedbackType.ToggleOff)
        setPower(now)
    }
    Row(
        Modifier.padding(top = 8.dp).fillMaxWidth().clip(RoundedCornerShape(24.dp)).background(colors.surface)
            .clickable { change(!on) }.padding(18.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(48.dp).clip(CircleShape).background(round), contentAlignment = Alignment.Center) {
            Icon(Glyph.Power, glyph, size = 24.dp, weight = 2.4.dp)
        }
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Text("Natro", color = colors.text, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(2.dp))
            if (on) {
                StatusLine(status)
            } else {
                Text("Off: no connection, no listening, no bubble.", color = colors.muted, fontSize = 13.sp)
            }
        }
        Spacer(Modifier.width(8.dp))
        Switch(checked = on, onCheckedChange = ::change, colors = switchColors())
    }
}

@Composable
internal fun Group(title: String, content: @Composable ColumnScope.() -> Unit) {
    val colors = palette
    Spacer(Modifier.height(22.dp))
    Text(title.uppercase(), color = colors.faint, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.8.sp,
        modifier = Modifier.padding(start = 6.dp, bottom = 8.dp))
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(22.dp)).background(colors.surface), content = content)
}

@Composable
internal fun Line() {
    Box(Modifier.padding(start = 18.dp).fillMaxWidth().height(1.dp).background(palette.line))
}

@Composable
private fun SwitchRow(title: String, detail: String, checked: Boolean, enabled: Boolean = true, onChange: (Boolean) -> Unit) {
    val colors = palette
    val haptics = LocalHapticFeedback.current
    fun change(now: Boolean) {
        haptics.performHapticFeedback(if (now) HapticFeedbackType.ToggleOn else HapticFeedbackType.ToggleOff)
        onChange(now)
    }
    Row(
        Modifier.fillMaxWidth().clickable(enabled = enabled) { change(!checked) }.padding(horizontal = 18.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, color = if (enabled) colors.text else colors.faint, fontSize = 16.sp)
            Spacer(Modifier.height(2.dp))
            Text(detail, color = colors.muted, fontSize = 13.sp, lineHeight = 18.sp)
        }
        Spacer(Modifier.width(12.dp))
        Switch(checked = checked && enabled, onCheckedChange = ::change, enabled = enabled, colors = switchColors())
    }
}

/** A row that opens another page. */
@Composable
private fun LinkRow(title: String, detail: String, enabled: Boolean = true, onClick: () -> Unit) {
    val colors = palette
    Row(
        Modifier.fillMaxWidth().clickable(enabled = enabled, onClick = onClick).padding(horizontal = 18.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, color = if (enabled) colors.text else colors.faint, fontSize = 16.sp)
            Spacer(Modifier.height(2.dp))
            Text(detail, color = colors.muted, fontSize = 13.sp, lineHeight = 18.sp)
        }
        Spacer(Modifier.width(12.dp))
        Icon(Glyph.Chevron, colors.faint, Modifier.rotate(-90f), size = 18.dp)  // the chevron points down
    }
}

@Composable
private fun SetupRow(step: SetupStep) {
    val colors = palette
    Row(
        Modifier.fillMaxWidth().clickable(enabled = !step.done, onClick = step.action).padding(horizontal = 18.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier.size(24.dp).clip(CircleShape)
                .background(if (step.done) colors.accent else Color.Transparent)
                .border(if (step.done) 0.dp else 1.5.dp, if (step.done) Color.Transparent else colors.faint, CircleShape),
            contentAlignment = Alignment.Center,
        ) {
            if (step.done) Icon(Glyph.Check, colors.onAccent, size = 16.dp, weight = 2.4.dp)
        }
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Text(step.title, color = colors.text, fontSize = 16.sp)
            Spacer(Modifier.height(2.dp))
            Text(step.detail, color = colors.muted, fontSize = 13.sp, lineHeight = 18.sp)
        }
        if (!step.done) {
            Spacer(Modifier.width(10.dp))
            Box(
                Modifier.clip(RoundedCornerShape(16.dp)).background(colors.accent.copy(alpha = 0.14f))
                    .padding(horizontal = 14.dp, vertical = 7.dp),
            ) {
                Text(step.button, color = colors.accentText, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            }
        }
    }
}

@Composable
private fun InfoRow(title: String, value: String) {
    val colors = palette
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 14.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(title, color = colors.text, fontSize = 16.sp)
        Spacer(Modifier.width(16.dp))
        Text(value, color = colors.muted, fontSize = 15.sp)
    }
}

@Composable
private fun switchColors() = palette.let {
    SwitchDefaults.colors(
        checkedThumbColor = Color.White,
        checkedTrackColor = it.accent,
        checkedBorderColor = it.accent,
        uncheckedThumbColor = it.faint,
        uncheckedTrackColor = it.raised,
        uncheckedBorderColor = it.line,
    )
}
