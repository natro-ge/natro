package dev.natro

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.util.Log
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** A new build of this app, as the brain says of it (server/natro_agent/app_updates.py: app_update). */
data class AppUpdate(val version: Int, val size: Long, val sha256: String, val path: String) {
    /** Whether it's newer than the version running. */
    fun isFor(running: Int) = version > running

    /** Where it's fetched from: the brain's own address ("ws://host:8700"), over HTTP. */
    fun url(server: String) = server.trimEnd('/').replaceFirst("ws", "http") + path

    /** The next piece to ask for, with this much of it here already. */
    fun range(done: Long) = "bytes=$done-${minOf(done + PIECE_BYTES, size) - 1}"

    companion object {
        /** Each piece is a request of its own, as the brain's server drops a response that takes long to go out. */
        const val PIECE_BYTES = 1024 * 1024L

        /** What the row in Settings offers. version: the newer build known, if any. */
        fun offer(version: Int?, working: Boolean) = when {
            version == null -> "Check for updates"
            working -> "Getting version $version…"
            else -> "Update to version $version"
        }

        fun from(message: JSONObject): AppUpdate? {
            val update = AppUpdate(message.optInt("version"), message.optLong("size"), message.optString("sha256"),
                message.optString("path"))
            return update.takeIf { it.version > 0 && it.size > 0 && it.sha256.isNotBlank() && it.path.startsWith("/") }
        }
    }
}

/**
 * The app updates itself: told of a newer build, it fetches it (on any connection: his mobile data is unlimited), checks
 * it's what was said, waits until nothing's under way, and installs it, with no question where Android allows that (an app updating itself, with
 * UPDATE_PACKAGES_WITHOUT_USER_ACTION; else a notification asks for a tap). Android takes it only if it's signed with the
 * key this app was, so nobody else's build can get in this way. The app restarts itself after (BootReceiver).
 *
 * He can ask too, in Settings (a row under Version): it checks with the brain, and updates with no waiting. If he closes
 * Android's question, the update stays a tap away: in a notification, and in that row.
 */
object AppUpdates {
    private const val TAG = "NatroUpdate"
    private const val CHANNEL = "updates"
    private const val NOTICE = 7
    private const val WAIT_MILLIS = 1_000L
    private const val AGAIN_MILLIS = 5_000L
    private const val MOST_TRIES = 5

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val requestIds = AtomicInteger()
    private val client = OkHttpClient.Builder().readTimeout(2, TimeUnit.MINUTES).callTimeout(30, TimeUnit.MINUTES).build()
    @Volatile private var working = 0  // the version being fetched and installed
    @Volatile private var job: Job? = null
    // The install under way: one given up on for a new one says so too, which is nothing to tell him of.
    @Volatile var session = -1
        private set
    // He asked for it: no waiting until nothing's under way, and Android's question opens by itself.
    @Volatile private var asked = false

    /** For the row in Settings: the newest build known that's newer than this one, whether it's being got, and what
     * came of his asking. */
    val newer = MutableStateFlow<AppUpdate?>(null)
    val busy = MutableStateFlow(false)
    val said = MutableStateFlow("")

    fun came(context: Context, message: JSONObject) {
        val update = AppUpdate.from(message) ?: return
        if (!update.isFor(BuildConfig.VERSION_CODE)) return
        newer.value = update
        said.value = ""
        if (working < update.version) start(context.applicationContext, update)
    }

    /** He asked for it, in Settings or from the notification left when he closed Android's question. */
    fun now(context: Context) {
        val update = newer.value ?: return
        asked = true
        said.value = ""
        if (job?.isActive != true) start(context.applicationContext, update)
    }

    /** He asked whether there's a newer build: the brain says (came, or current). */
    fun check() {
        said.value = if (Natro.sendMessage(JSONObject().put("type", "app_update_check"))) "Checking…" else
            "Natro isn't connected right now."
    }

    /** This is the newest build there is. */
    fun current() {
        if (newer.value == null) said.value = "This is the newest version."
    }

    private fun start(app: Context, update: AppUpdate) {
        job?.cancel()
        working = update.version
        busy.value = true
        job = scope.launch {
            try {
                install(app, update, fetch(app, update))
            } catch (e: Exception) {
                Log.w(TAG, "Update to ${update.version} failed: $e")
                failed("Fetching it didn't work: try again.")
            }
        }
    }

    private suspend fun fetch(context: Context, update: AppUpdate): File {
        val file = File(context.cacheDir, "update-${update.version}.apk")
        context.cacheDir.listFiles()?.filter { it.name.startsWith("update-") && it != file }?.forEach { it.delete() }
        var tries = 0
        while (true) {
            val before = file.length()
            try {
                download(update, file)
                return file
            } catch (e: Exception) {
                // It starts again from what it has, so giving up is only for when it gets no further.
                tries = if (file.length() > before) 0 else tries + 1
                if (tries >= MOST_TRIES) throw e
                Log.i(TAG, "Fetching ${update.version} stopped at ${file.length()} of ${update.size}: $e")
                delay(AGAIN_MILLIS)
            }
        }
    }

    /** Fetches what's still missing, a piece at a time, each added to the file as it comes. */
    private fun download(update: AppUpdate, file: File) {
        if (file.length() > update.size) file.delete()
        while (file.length() < update.size) {
            val request = Request.Builder().url(update.url(BuildConfig.NATRO_SERVER))
                .header("X-Natro-Token", BuildConfig.NATRO_TOKEN).header("Range", update.range(file.length())).build()
            client.newCall(request).execute().use { response ->
                check(response.code == 206) { "HTTP ${response.code}" }
                FileOutputStream(file, true).use { out -> response.body.byteStream().use { it.copyTo(out) } }
            }
        }
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(64 * 1024)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        if (digest.digest().joinToString("") { "%02x".format(it) } != update.sha256) {
            file.delete()  // not to be added to
            error("it isn't the build that was said")
        }
    }

    private suspend fun install(context: Context, update: AppUpdate, file: File) {
        while (!Natro.idle() && !asked) delay(WAIT_MILLIS)  // not in the middle of a request: the app restarts
        val installer = context.packageManager.packageInstaller
        session = -1
        installer.mySessions.forEach { runCatching { installer.abandonSession(it.sessionId) } }  // a question left open
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(context.packageName)
            setSize(file.length())
            setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val id = installer.createSession(params)
        session = id
        installer.openSession(id).use { session ->
            session.openWrite("natro.apk", 0, file.length()).use { out ->
                file.inputStream().use { it.copyTo(out) }
                session.fsync(out)
            }
            val done = Intent(context, AppUpdateDone::class.java).putExtra("version", update.version)
            session.commit(PendingIntent.getBroadcast(context, requestIds.incrementAndGet(), done,
                PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT).intentSender)
        }
        Log.i(TAG, "Installing ${update.version}")
    }

    /** Android wants his tap this time: its question opens if he just asked, or a notification opens it. */
    fun askTap(context: Context, confirm: Intent, version: Int) {
        busy.value = false
        confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val opened = asked && Natro.canOpenScreens(context) && runCatching { context.startActivity(confirm) }.isSuccess
        asked = false
        if (opened) return
        notify(context, version, false, PendingIntent.getActivity(context, NOTICE, confirm,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
    }

    /** He closed Android's question: the update stays a tap away, with no fuss. */
    fun closed(context: Context, version: Int) {
        failed("")
        notify(context, version, true, PendingIntent.getBroadcast(context, NOTICE, Intent(context, AppUpdateAgain::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
    }

    private fun notify(context: Context, version: Int, quietly: Boolean, tapped: PendingIntent) {
        val notifications = context.getSystemService(NotificationManager::class.java)
        notifications.createNotificationChannel(NotificationChannel(CHANNEL, "Updates", NotificationManager.IMPORTANCE_DEFAULT))
        val notice = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_natro)
            .setContentTitle("Natro's update is ready")
            .setContentText("Tap to install version $version.")
            .setContentIntent(tapped)
            .setAutoCancel(true)
            .setSilent(quietly)
            .build()
        try {
            notifications.notify(NOTICE, notice)
        } catch (e: SecurityException) {  // notifications not allowed
        }
    }

    /** It didn't happen: the next time he connects or asks, or the next build. */
    fun failed(why: String) {
        working = 0
        asked = false
        busy.value = false
        said.value = why
    }
}

/** The notification left when he closed Android's question. */
class AppUpdateAgain : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) = AppUpdates.now(context)
}

/** How the install went. */
class AppUpdateDone : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        val version = intent.getIntExtra("version", 0)
        val current = intent.getIntExtra(PackageInstaller.EXTRA_SESSION_ID, -1) == AppUpdates.session
        when (status) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
                AppUpdates.askTap(context, confirm, version)
            }
            PackageInstaller.STATUS_SUCCESS -> Log.i("NatroUpdate", "Installed $version")
            PackageInstaller.STATUS_FAILURE_ABORTED -> if (current) AppUpdates.closed(context, version)
            else -> {
                Log.w("NatroUpdate", "Install of $version: $status ${intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)}")
                if (current) AppUpdates.failed("Android didn't install it: try again.")
            }
        }
    }
}
