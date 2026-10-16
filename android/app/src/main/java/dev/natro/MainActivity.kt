package dev.natro

import android.Manifest
import android.app.Application
import android.app.role.RoleManager
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.core.app.NotificationManagerCompat

/** Starts what every part of Natro needs, whichever part Android starts first (the app, a service, the bubble). */
class NatroApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        Prefs.init(this)
        Natro.init(this)
        Transfers.init(this)
        Bubble.init(this)
    }
}

/** What the screens may ask of the activity. */
class AppActions(
    val microphoneAllowed: () -> Boolean,
    val askMicrophone: () -> Unit,
    val setWakeWord: (Boolean) -> Unit,
    val setPower: (Boolean) -> Unit,
)

/**
 * Natro's app: the chat, and her settings beside it. Opened from the bubble or the launcher; opened
 * by the assistant gesture only when the bubble can't show (no microphone or overlay permission
 * yet), and then it listens right away.
 */
class MainActivity : ComponentActivity() {
    private var wakeWordAfterMicrophone = false
    private val askMicrophone = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (!granted) Natro.notice.value = "Natro needs the microphone to hear you. Typing works without it."
        if (granted && wakeWordAfterMicrophone) Prefs.setWakeWord(true)
        wakeWordAfterMicrophone = false
        refreshSetup()
        NatroService.instance?.refresh()
    }
    private val askPermissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        refreshSetup()
        NatroService.instance?.refresh()
    }
    private val setup = mutableStateOf(emptyList<SetupStep>())

    private val actions = AppActions(
        microphoneAllowed = { granted(Manifest.permission.RECORD_AUDIO) },
        askMicrophone = { askMicrophone.launch(Manifest.permission.RECORD_AUDIO) },
        setWakeWord = { on ->
            if (on && !granted(Manifest.permission.RECORD_AUDIO)) {
                wakeWordAfterMicrophone = true
                askMicrophone.launch(Manifest.permission.RECORD_AUDIO)
            } else {
                Prefs.setWakeWord(on)
            }
        },
        setPower = { on -> Power.set(this, on) },
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            NatroTheme { NatroApp(setup.value, actions) }
        }
        listenIfAsked(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        listenIfAsked(intent)
    }

    override fun onStart() {
        super.onStart()
        Natro.onScreen.value = true
        // On screen, Android lets the service take the microphone: "OK Natro" starts again if it was paused.
        if (Prefs.power.value) NatroService.start(this)
    }

    override fun onResume() {
        super.onResume()
        refreshSetup()
        Bubble.sync()
    }

    override fun onStop() {
        Natro.onScreen.value = false
        super.onStop()
    }

    private fun listenIfAsked(intent: Intent?) {
        if (intent?.getBooleanExtra(LISTEN, false) != true) return
        intent.removeExtra(LISTEN)
        if (granted(Manifest.permission.RECORD_AUDIO)) Natro.listen() else askMicrophone.launch(Manifest.permission.RECORD_AUDIO)
    }

    private fun refreshSetup() {
        setup.value = listOf(
            SetupStep("Microphone and notifications", "To hear you, and to show that Natro is connected.",
                granted(Manifest.permission.RECORD_AUDIO) && granted(Manifest.permission.POST_NOTIFICATIONS),
                essential = true, button = "Allow") {
                askPermissions.launch(arrayOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS))
            },
            SetupStep("Digital assistant", "Long-press the power button to talk to Natro from anywhere.",
                getSystemService(RoleManager::class.java).isRoleHeld(RoleManager.ROLE_ASSISTANT),
                essential = true, button = "Open") {
                startActivity(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS))
            },
            SetupStep("Display over other apps", "For the bubble, over whatever app is open.",
                Settings.canDrawOverlays(this), essential = true, button = "Open") {
                startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
            },
            SetupStep("Contacts, calls and texts", "She always asks before calling or texting.",
                PHONE_PERMISSIONS.all(::granted), essential = false, button = "Allow") {
                askPermissions.launch(PHONE_PERMISSIONS)
            },
            SetupStep("Notification access", "For \"what did I miss?\".",
                packageName in NotificationManagerCompat.getEnabledListenerPackages(this), essential = false, button = "Open") {
                startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS).putExtra(
                    Settings.EXTRA_NOTIFICATION_LISTENER_COMPONENT_NAME,
                    ComponentName(this, NotificationReader::class.java).flattenToString()))
            },
            SetupStep("Screen control", "So Natro can use apps for you (Accessibility > Natro).",
                ScreenControl.current != null, essential = false, button = "Open") {
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            },
            SetupStep("All files access", "So Natro can find files on the phone and send them to your PC when you ask.",
                Environment.isExternalStorageManager(), essential = false, button = "Open") {
                startActivity(Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:$packageName")))
            },
        )
    }

    private fun granted(permission: String) = checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED

    companion object {
        const val LISTEN = "dev.natro.LISTEN"
        val PHONE_PERMISSIONS = arrayOf(Manifest.permission.READ_CONTACTS, Manifest.permission.CALL_PHONE,
            Manifest.permission.SEND_SMS, Manifest.permission.READ_SMS)
    }
}

/** In order: each slides in over the one before it, from the right. */
private enum class Page { Chat, Settings, WakeTest }

/** The chat, with the settings sliding over it from the right, and the wake-word test over them. */
@Composable
fun NatroApp(setup: List<SetupStep>, actions: AppActions) {
    var page by rememberSaveable { mutableStateOf(Page.Chat) }
    BackHandler(enabled = page != Page.Chat) { page = Page.entries[page.ordinal - 1] }
    AnimatedContent(
        targetState = page,
        transitionSpec = {
            if (targetState > initialState) {
                (slideInHorizontally(tween(320)) { it } + fadeIn(tween(220))) togetherWith
                    (slideOutHorizontally(tween(320)) { -it / 5 } + fadeOut(tween(220)))
            } else {
                (slideInHorizontally(tween(320)) { -it / 5 } + fadeIn(tween(220))) togetherWith
                    (slideOutHorizontally(tween(320)) { it } + fadeOut(tween(220)))
            }
        },
        label = "page",
    ) { now ->
        when (now) {
            Page.Chat -> ChatScreen(setup.count { it.essential && !it.done }, openSettings = { page = Page.Settings }, actions)
            Page.Settings -> SettingsScreen(setup, back = { page = Page.Chat }, openWakeTest = { page = Page.WakeTest }, actions)
            Page.WakeTest -> WakeTestScreen(back = { page = Page.Settings }, actions)
        }
    }
}
