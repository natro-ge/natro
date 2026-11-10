package dev.natro

import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.lifecycleScope
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

/**
 * "Convert with Natro" in Android's share sheet. It asks which format (what the shared files can all become:
 * Conversions), copies them into a folder of Natro's while it's open (Android takes the sharing app's permission
 * back when it closes), then asks her as a request with them attached and closes. She brings them up and sends
 * the result to Downloads (server/natro_agent/convert.py), with a notification that opens it.
 */
class ConvertActivity : ComponentActivity() {
    private sealed interface Screen {
        data class Choosing(val what: String, val formats: List<String>) : Screen
        data class Working(val status: String) : Screen
    }

    private val screen = mutableStateOf<Screen?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val shared = sharedFiles(intent)
        when {
            shared.isEmpty() -> return done("There was no file to convert.")
            !Prefs.power.value -> return done("Natro is off: turn her on to convert files.")
        }
        NatroService.start(this)
        setContent { NatroTheme { Card(screen.value, ::finish) { format -> convert(shared, format) } } }
        lifecycleScope.launch {
            val files = withContext(Dispatchers.IO) { shared.map(Transfers::described) }
            val names = files.map { it.first }
            val big = files.firstOrNull { it.second > Conversions.MOST_BYTES }
            val formats = Conversions.common(names)
            when {
                big != null -> done("${big.first} is ${FileNames.size(big.second)}; Natro converts at most " +
                    "${FileNames.size(Conversions.MOST_BYTES)}.")
                formats.isEmpty() -> done(Conversions.whyNot(names))
                else -> screen.value = Screen.Choosing(if (names.size == 1) names[0] else "${names.size} files", formats)
            }
        }
    }

    private fun convert(shared: List<Uri>, format: String) {
        val what = if (shared.size == 1) "it" else "${shared.size} files"
        screen.value = Screen.Working("Getting $what ready…")
        lifecycleScope.launch {
            val files = mutableListOf<File>()
            val problem = withContext(Dispatchers.IO) {
                try {
                    shared.forEach { files += Transfers.forNatro(it) }
                    null
                } catch (e: ToolFailure) {
                    e.message.orEmpty()
                }
            }
            if (problem != null) return@launch done(problem)
            screen.value = Screen.Working("Waiting for Natro's connection…")
            val online = withTimeoutOrNull(CONNECT_MILLIS) { Natro.status.first { it == Natro.Status.Online } } != null
            if (!online) return@launch done("Natro isn't connected, so nothing was converted.")
            val label = Conversions.label(format)
            Natro.sendText(if (files.size == 1) "Convert it to $label." else "Convert each of them to $label.", files)
            val named = if (files.size == 1) files[0].name else "${files.size} files"
            done("Converting $named to $label: it'll land in Downloads.")
        }
    }

    private fun done(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
        finish()
    }

    private companion object {
        /** How long the card waits for Natro to connect, which takes a moment if she wasn't. */
        const val CONNECT_MILLIS = 20_000L
    }

    /** A small card over the app that shared; a tap beside it closes it. */
    @OptIn(ExperimentalLayoutApi::class)
    @Composable
    private fun Card(screen: Screen?, close: () -> Unit, choose: (String) -> Unit) {
        val quiet = remember { MutableInteractionSource() }
        Box(
            Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.35f))
                .clickable(quiet, null) { if (screen is Screen.Choosing) close() },
            contentAlignment = Alignment.Center,
        ) {
            if (screen == null) return@Box
            Column(
                Modifier.padding(32.dp).widthIn(max = 360.dp).fillMaxWidth()
                    .background(palette.surface, RoundedCornerShape(24.dp))
                    .clickable(quiet, null) {}
                    .padding(20.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Logo(32.dp)
                    Text("Convert with Natro", color = palette.text, fontSize = 17.sp)
                }
                when (screen) {
                    is Screen.Choosing -> {
                        Text("${screen.what} to:", color = palette.muted, fontSize = 14.sp, maxLines = 2,
                            overflow = TextOverflow.MiddleEllipsis)
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            screen.formats.forEach { format ->
                                Text(
                                    Conversions.label(format),
                                    Modifier.background(palette.raised, RoundedCornerShape(14.dp))
                                        .clickable { choose(format) }
                                        .padding(horizontal = 14.dp, vertical = 9.dp),
                                    color = palette.text,
                                    fontSize = 14.sp,
                                )
                            }
                        }
                    }
                    is Screen.Working -> {
                        Text(screen.status, color = palette.muted, fontSize = 14.sp)
                        LinearProgressIndicator(Modifier.fillMaxWidth(), color = palette.accent, trackColor = palette.raised)
                    }
                }
            }
        }
    }
}
