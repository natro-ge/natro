package dev.natro

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.os.Bundle
import android.provider.Settings
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService
import android.speech.RecognitionService
import android.speech.SpeechRecognizer
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Natro as the phone's digital assistant: long-pressing the power button (or the assistant
 * gesture) starts her listening, in the bubble (Bubble.kt), over whatever app is open. Being the
 * assistant also lets her open apps, alarms and calls while she isn't on screen: Android allows that
 * to the app whose VoiceInteractionService it keeps bound.
 *
 * The owner picks her in Settings > Apps > Default apps > Digital assistant app.
 */
class NatroVoiceService : VoiceInteractionService() {
    override fun onReady() {
        super.onReady()
        // Android binds the assistant when the phone starts, and keeps it: a moment to get Natro going.
        if (Prefs.power.value) NatroService.start(this)
    }
}

class NatroSessionService : VoiceInteractionSessionService() {
    override fun onNewSession(args: Bundle?): VoiceInteractionSession = NatroSession(this)
}

/**
 * The assistant gesture. Natro answers in the bubble, not in this session's window: the window is a
 * single invisible pixel that takes no touches, kept only until the microphone is Natro's service's
 * (Android hands it over while the assistant is showing), so the recording goes on after it hides.
 */
class NatroSession(private val app: Context) : VoiceInteractionSession(app) {
    private var scope: CoroutineScope? = null
    private var waiting: Job? = null

    override fun onCreate() {
        super.onCreate()
        // Nothing of the screen underneath is sent anywhere, so don't gather it.
        setDisabledShowContext(SHOW_WITH_ASSIST or SHOW_WITH_SCREENSHOT)
        window.window?.apply {
            setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
            setGravity(Gravity.TOP or Gravity.START)
            setLayout(1, 1)
            addFlags(WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE)
            clearFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
            setWindowAnimations(0)
        }
    }

    override fun onCreateContentView(): View = View(app)

    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        val bubble = Settings.canDrawOverlays(app)
        when {
            !Prefs.power.value -> {
                if (bubble) Natro.flashOff() else openApp(listen = false)
                hide()
            }
            // Without the microphone or the bubble, the app opens instead: it asks for what's missing.
            !bubble || app.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED -> {
                openApp(listen = true)
                hide()
            }
            else -> {
                Natro.listen()
                NatroService.start(app)  // starts it if needed; either way it takes the microphone now
                NatroService.instance?.refresh()
                hideWhenTheMicrophoneIsNatros()
            }
        }
    }

    private fun hideWhenTheMicrophoneIsNatros() {
        waiting?.cancel()
        val mine = scope ?: CoroutineScope(SupervisorJob() + Dispatchers.Main).also { scope = it }
        waiting = mine.launch {
            // If Android doesn't hand it over, this session stays (unseen) until the recording ends instead.
            withTimeoutOrNull(MICROPHONE_WAIT_MILLIS) { NatroService.mic.first { it } }
                ?: Natro.recording.first { !it }
            hide()
        }
    }

    private fun openApp(listen: Boolean) {
        startAssistantActivity(Intent(app, MainActivity::class.java).putExtra(MainActivity.LISTEN, listen))
    }

    override fun onHide() {
        waiting?.cancel()
        super.onHide()
    }

    override fun onDestroy() {
        scope?.cancel()
        super.onDestroy()
    }

    private companion object {
        const val MICROPHONE_WAIT_MILLIS = 3_000L
    }
}

/** An assistant must name a speech recognizer; Natro's speech goes to her brain instead, so this one declines. */
class NatroRecognitionService : RecognitionService() {
    override fun onStartListening(recognizerIntent: Intent?, listener: Callback?) {
        listener?.error(SpeechRecognizer.ERROR_CLIENT)
    }

    override fun onCancel(listener: Callback?) {}

    override fun onStopListening(listener: Callback?) {}
}
