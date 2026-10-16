package dev.natro

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

/**
 * Natro's one background service, running while she is on. It keeps the connection to the brain
 * open with the screen off (so the brain can use the phone's tools even for a request made on the
 * PC), listens for the wake word when that's on (or its test runs), and keeps a request's recording
 * going after the assistant gesture. One quiet notification says how she is.
 *
 * Android lets a service in the background use the microphone only as a "microphone" foreground
 * service, and lets it become one only while Natro may use the microphone (the app on screen, or
 * the assistant gesture). So it is one while the wake word is on or tested, or a request is being
 * recorded, and not otherwise.
 */
class NatroService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private lateinit var notifications: NotificationManager
    private var ears: WakeListener? = null
    private var hasMic = false

    override fun onCreate() {
        super.onCreate()
        instance = this
        notifications = getSystemService(NotificationManager::class.java)
        notifications.deleteNotificationChannel("wake-word")  // the old app's second notification
        notifications.createNotificationChannel(NotificationChannel(CHANNEL, "Natro", NotificationManager.IMPORTANCE_LOW))
        foreground(wantMic())
        if (!Prefs.power.value) return  // restarted by Android after Natro was turned off: onStartCommand stops it
        Natro.start(this)
        scope.launch {
            combine(Natro.status, Prefs.wakeWord, WakeTest.running, Natro.recording) { _, _, _, _ -> }.collect { refresh() }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == TURN_OFF || !Prefs.power.value) {
            foreground(false)  // every start must be answered so, even one that stops it
            Power.set(this, false)
            return START_NOT_STICKY
        }
        foreground(wantMic())  // answers this start, as Android requires of every startForegroundService
        refresh()
        return START_STICKY
    }

    override fun onBind(intent: Intent?) = null

    override fun onDestroy() {
        instance = null
        mic.value = false
        wakePaused.value = false
        scope.cancel()
        ears?.stop()
        ears = null
        Natro.stop()
        super.onDestroy()
    }

    /** Match the microphone, the wake word and the notification to what's wanted now. */
    fun refresh() {
        val wanted = wantMic()
        if (wanted != hasMic) foreground(wanted)
        val listen = (Prefs.wakeWord.value || WakeTest.running.value) && hasMic
        if (listen && ears == null) {
            ears = WakeListener(assets).also { it.start() }
        } else if (!listen && ears != null) {
            ears?.stop()
            ears = null
        }
        wakePaused.value = Prefs.wakeWord.value && !listen
        notifications.notify(NOTIFICATION, notification())
    }

    private fun wantMic() = micAllowed(this) && (Prefs.wakeWord.value || WakeTest.running.value || Natro.recording.value)

    private fun foreground(mic: Boolean) {
        hasMic = mic && try {
            ServiceCompat.startForeground(this, NOTIFICATION, notification(),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
            true
        } catch (e: Exception) {  // Android won't hand over the microphone from the background right now
            false
        }
        if (!hasMic) {
            ServiceCompat.startForeground(this, NOTIFICATION, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        }
        Companion.mic.value = hasMic
    }

    private fun notification(): Notification {
        val text = when (val status = Natro.status.value) {
            Natro.Status.Online -> "Connected"
            Natro.Status.Connecting, Natro.Status.Off -> "Connecting…"
            is Natro.Status.Offline -> "Offline: ${status.reason}"
        }
        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_natro)
            .setContentTitle(if (ears != null) "Natro · listening for \"OK\" or \"Hey Natro\"" else "Natro")
            .setContentText(text)
            .setOngoing(true)
            .setSilent(true)
            .setContentIntent(PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
                PendingIntent.FLAG_IMMUTABLE))
            .addAction(0, "Turn off", PendingIntent.getService(this, 1,
                Intent(this, NatroService::class.java).setAction(TURN_OFF), PendingIntent.FLAG_IMMUTABLE))
            .build()
    }

    companion object {
        private const val CHANNEL = "connection"
        private const val NOTIFICATION = 1
        private const val TURN_OFF = "dev.natro.TURN_OFF"

        @Volatile var instance: NatroService? = null
            private set

        /** Whether the service holds the microphone (as a microphone foreground service). */
        val mic = MutableStateFlow(false)

        /** "OK Natro" is on, but Android didn't let the microphone start in the background: it will once the app opens. */
        val wakePaused = MutableStateFlow(false)

        /** Start the service, or have it check what's wanted. False if Android doesn't allow starting it from here now. */
        fun start(context: Context): Boolean = try {
            context.startForegroundService(Intent(context, NatroService::class.java))
            true
        } catch (e: IllegalStateException) {  // ForegroundServiceStartNotAllowedException
            false
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, NatroService::class.java))
        }

        fun micAllowed(context: Context) =
            context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
    }
}
