package dev.natro

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * "Send to PC" in Android's share sheet. It copies what was shared into Natro's outbox while it's open
 * (Android takes the sharing app's permission back when it closes), then closes; Transfers sends the
 * files on to the PC as soon as Natro is connected, with a notification for how it goes.
 */
class ShareActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val shared = shared(intent)
        when {
            shared.isEmpty() -> return done("There was no file to send.")
            !Prefs.power.value -> return done("Natro is off: turn her on to send files to your PC.")
        }
        NatroService.start(this)
        val what = if (shared.size == 1) "it" else "${shared.size} files"
        val status = mutableStateOf("Getting $what ready for your PC…")
        setContent { NatroTheme { Preparing(status.value) } }
        lifecycleScope.launch {
            val names = mutableListOf<String>()
            val problems = mutableListOf<String>()
            withContext(Dispatchers.IO) {
                for ((index, uri) in shared.withIndex()) {
                    if (shared.size > 1) status.value = "Getting file ${index + 1} of ${shared.size} ready for your PC…"
                    try {
                        names += Transfers.share(uri)
                    } catch (e: ToolFailure) {
                        problems += e.message.orEmpty()
                    }
                }
            }
            done(when {
                names.isEmpty() -> problems.joinToString(" ")
                names.size == 1 -> "Sending ${names[0]} to your PC. ${problems.joinToString(" ")}"
                else -> "Sending ${names.size} files to your PC. ${problems.joinToString(" ")}"
            }.trim())
        }
    }

    private fun done(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
        finish()
    }

    private fun shared(intent: Intent?): List<Uri> = when (intent?.action) {
        Intent.ACTION_SEND -> listOfNotNull(intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java))
        Intent.ACTION_SEND_MULTIPLE -> intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java).orEmpty()
        else -> emptyList()
    }
}

/** A small card over the app that shared, while the files are copied. */
@Composable
private fun Preparing(status: String) {
    Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.35f)), contentAlignment = Alignment.Center) {
        Column(
            Modifier.padding(32.dp).widthIn(max = 360.dp).fillMaxWidth()
                .background(palette.surface, RoundedCornerShape(24.dp)).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Logo(32.dp)
                Text("Send to PC", color = palette.text, fontSize = 17.sp)
            }
            Text(status, color = palette.muted, fontSize = 14.sp)
            LinearProgressIndicator(Modifier.fillMaxWidth(), color = palette.accent, trackColor = palette.raised)
        }
    }
}
