package dev.natro

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Environment
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.webkit.MimeTypeMap
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.io.OutputStream
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong

/**
 * Files between the phone and the PC, passed along by Natro's server (agent/natro_agent/files.py; the
 * messages are in server.py). Files from the PC land in Downloads, with a notification that opens them.
 * Files go to the PC from the share sheet (ShareActivity), or when Natro is asked ("send my last photo
 * to the PC": the brain finds it with search_files and asks for it with file_request).
 *
 * Shared files are copied into the outbox first, as Android takes the sharing app's permission back when
 * the share screen closes, and go from there as soon as Natro is connected (again after a lost
 * connection); the copy is deleted once the server has the file. A file the brain asks for is read
 * where it is, with "All files access".
 *
 * Either way a file goes in parts, and the sender sends on only while less than WINDOW_BYTES of it is
 * unanswered, so it never fills OkHttp's queue (which closes the connection at 16 MB) nor holds a
 * voice request up for long.
 */
object Transfers {
    /** The biggest file Natro passes on (the server's limit too). */
    const val MAX_BYTES = 1L shl 30
    private const val PART_BYTES = 512 * 1024
    private const val WINDOW_BYTES = 2L shl 20
    private const val STALL_MILLIS = 60_000L
    /** How often a progress bar may move. */
    private const val PROGRESS_MILLIS = 700L
    private const val CHANNEL = "files"

    private lateinit var app: Context
    private lateinit var outbox: File
    private lateinit var notifications: NotificationManager
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val sequence = AtomicLong()
    /** Says the outbox may have something to send now. */
    private val wake = Channel<Unit>(Channel.CONFLATED)

    private sealed interface Outcome {
        /** The server has it; waiting: the other device is offline and gets it when it's back. */
        data class Sent(val waiting: Boolean) : Outcome
        data class Failed(val message: String) : Outcome
        /** The connection went (or stalled): an outbox file is tried again once connected. */
        data object Offline : Outcome
    }

    private class Sending(val name: String, val to: String) {
        val acked = MutableStateFlow(0L)
        val outcome = CompletableDeferred<Outcome>()
        var shownAt = 0L

        fun finish(result: Outcome) {
            outcome.complete(result)
            acked.value = Long.MAX_VALUE  // nothing waits for room any more
        }
    }

    private class Receiving(val from: String, val name: String, val size: Long, val uri: Uri, val stream: OutputStream) {
        var received = 0L
        var shownAt = 0L
    }

    private val sending = ConcurrentHashMap<String, Sending>()
    private val receiving = ConcurrentHashMap<String, Receiving>()
    /** Files on the server on their way to the other device, for the notification when they arrive or don't. */
    private val passedOn = ConcurrentHashMap<String, Sending>()
    /** The file whose piece is the next binary message (after its file_part). */
    @Volatile private var pieceOf: String? = null

    fun init(context: Context) {
        if (::app.isInitialized) return
        app = context.applicationContext
        outbox = File(app.filesDir, "outbox").apply { mkdirs() }
        notifications = app.getSystemService(NotificationManager::class.java)
        notifications.createNotificationChannel(NotificationChannel(CHANNEL, "Files", NotificationManager.IMPORTANCE_DEFAULT))
        scope.launch { for (signal in wake) sendOutbox() }
    }

    // The connection (Natro.kt)

    /** Connected: the outbox goes out. */
    fun online() {
        wake.trySend(Unit)
    }

    /** The connection went: files half sent wait for the next one, files half received are deleted. */
    fun lost() {
        pieceOf = null
        sending.values.forEach { it.finish(Outcome.Offline) }
        receiving.keys.toList().forEach { id -> receiving.remove(id)?.let { drop(id, it) } }
    }

    /** A file_* message from the brain. Runs on the connection's thread. */
    fun handle(data: JSONObject) {
        val id = data.optString("id")
        when (data.optString("type")) {
            "file_incoming" -> incoming(id, data.optString("from"), data.optString("name"), data.optLong("size", -1))
            "file_part" -> pieceOf = id
            "file_end" -> received(id)
            "file_got" -> sending[id]?.acked?.update { maxOf(it, data.optLong("bytes")) }
            "file_sent" -> sending[id]?.finish(Outcome.Sent(data.optBoolean("waiting")))
            "file_delivered" -> passedOn.remove(id)?.let {
                notify(id, "${data.optString("name").ifBlank { it.name }} is on your ${label(it.to)}",
                    "In its Downloads folder.")
            }
            "file_failed" -> failed(id, data.optString("message").ifBlank { "It didn't work." })
            "file_request" -> requested(id, data.optString("path"), data.optString("to"))
        }
    }

    /** The next piece of a file coming down. Runs on the connection's thread. */
    fun piece(bytes: ByteString) {
        val id = pieceOf ?: return
        pieceOf = null
        val file = receiving[id] ?: return
        try {
            if (file.received + bytes.size > file.size) throw IOException("it was bigger than it said")
            bytes.write(file.stream)
            file.received += bytes.size
        } catch (e: IOException) {
            return refuse(id, file, "The phone couldn't save ${file.name}: ${e.message}")
        }
        Natro.sendMessage(JSONObject().put("type", "file_got").put("id", id).put("bytes", file.received))
        if (now() - file.shownAt >= PROGRESS_MILLIS) {
            file.shownAt = now()
            notify(id, "${file.name} from your ${label(file.from)}", "Receiving…", percent(file.received, file.size))
        }
    }

    private fun failed(id: String, message: String) {
        sending[id]?.finish(Outcome.Failed(message))
        passedOn.remove(id)?.let { notify(id, "${it.name} didn't reach your ${label(it.to)}", message, alert = true) }
        receiving.remove(id)?.let { drop(id, it) }  // the server gave up sending it
    }

    // Files coming down

    private fun incoming(id: String, from: String, name: String, size: Long) {
        val safe = FileNames.safe(name)
        if (size !in 0..MAX_BYTES) {
            Natro.sendMessage(failure(id, "Files can be at most 1 GB."))
            return
        }
        val resolver = app.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, safe)
            put(MediaStore.MediaColumns.MIME_TYPE, mime(safe))
            put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
            put(MediaStore.MediaColumns.IS_PENDING, 1)  // hidden from other apps until it's whole
        }
        try {
            val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                ?: throw IOException("Downloads can't be written to")
            val stream = try {
                resolver.openOutputStream(uri) ?: throw IOException("Downloads can't be written to")
            } catch (e: Exception) {
                resolver.delete(uri, null, null)
                throw e
            }
            receiving[id] = Receiving(from, safe, size, uri, stream)
            notify(id, "$safe from your ${label(from)}", "Receiving…", 0)
        } catch (e: Exception) {
            Natro.sendMessage(failure(id, "The phone couldn't save $safe: ${e.message}"))
        }
    }

    private fun received(id: String) {
        val file = receiving.remove(id) ?: return
        val saved = try {
            file.stream.close()
            if (file.received != file.size) {
                throw IOException("only ${FileNames.size(file.received)} of ${FileNames.size(file.size)} arrived")
            }
            app.contentResolver.update(file.uri, ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }, null, null)
            // Android renames it if Downloads already has one of that name.
            app.contentResolver.query(file.uri, arrayOf(MediaStore.MediaColumns.DISPLAY_NAME), null, null, null)
                ?.use { if (it.moveToFirst()) it.getString(0) else null } ?: file.name
        } catch (e: Exception) {
            return refuse(id, file, "The phone couldn't save ${file.name}: ${e.message}")
        }
        Natro.sendMessage(JSONObject().put("type", "file_saved").put("id", id).put("name", saved))
        notify(id, "$saved, from your ${label(file.from)}", "In Downloads. Tap to open it.", alert = true,
            open = Intent(Intent.ACTION_VIEW).setDataAndType(file.uri, mime(saved)))
    }

    /** Couldn't take the file: tell the server, and delete what arrived. */
    private fun refuse(id: String, file: Receiving, message: String) {
        receiving.remove(id)
        drop(id, file)
        Natro.sendMessage(failure(id, message))
        notify(id, "Couldn't save ${file.name}", message, alert = true)
    }

    private fun drop(id: String, file: Receiving) {
        try {
            file.stream.close()
        } catch (e: IOException) {
        }
        app.contentResolver.delete(file.uri, null, null)
        notifications.cancel(id, 0)
    }

    // Files going up

    /**
     * Copy a file shared with Natro (ShareActivity) into the outbox, for the PC. Returns its name, or throws
     * ToolFailure saying why it can't go. Blocks: run it off the main thread, while the share screen is open.
     */
    fun share(uri: Uri, to: String = "pc"): String {
        val resolver = app.contentResolver
        var name = "file"
        var size = -1L
        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use {
            if (it.moveToFirst()) {
                name = it.getString(0) ?: name
                if (!it.isNull(1)) size = it.getLong(1)
            }
        }
        name = FileNames.safe(name)
        if (size > MAX_BYTES) throw ToolFailure("$name is ${FileNames.size(size)}; files can be at most 1 GB.")
        val id = "phone-${System.currentTimeMillis().toString(36)}-${sequence.incrementAndGet()}"
        val data = File(outbox, "$id.data")
        try {
            val input = resolver.openInputStream(uri) ?: throw IOException("it can't be opened")
            input.use { from ->
                data.outputStream().use { into ->
                    val buffer = ByteArray(1 shl 16)
                    var total = 0L
                    while (true) {
                        val count = from.read(buffer)
                        if (count < 0) break
                        total += count
                        if (total > MAX_BYTES) throw ToolFailure("$name is bigger than 1 GB, the most Natro sends.")
                        into.write(buffer, 0, count)
                    }
                }
            }
        } catch (e: Exception) {
            data.delete()
            throw e as? ToolFailure ?: ToolFailure("Couldn't get $name ready: ${e.message}")
        }
        File(outbox, "$id.json").writeText(JSONObject().put("name", name).put("to", to).toString())
        notify(id, "$name for your ${label(to)}", "Waiting for Natro's connection…")
        wake.trySend(Unit)
        return name
    }

    /** Sends what's in the outbox, oldest first, while Natro is connected. */
    private suspend fun sendOutbox() {
        while (Natro.status.value == Natro.Status.Online) {
            val next = outbox.listFiles { file -> file.name.endsWith(".json") }?.minByOrNull { it.lastModified() } ?: return
            val id = next.name.removeSuffix(".json")
            val data = File(outbox, "$id.data")
            val meta = runCatching { JSONObject(next.readText()) }.getOrNull()
            if (meta != null && data.isFile && send(id, data, meta.getString("name"), meta.optString("to", "pc")) == Outcome.Offline) {
                return  // tried again once connected
            }
            next.delete()
            data.delete()
        }
    }

    /** The brain asks for one of the phone's files (the owner asked Natro to send it). */
    private fun requested(id: String, path: String, to: String) = scope.launch {
        val file = File(path)
        val problem = when {
            !Environment.isExternalStorageManager() ->
                "Natro can't read the phone's files yet: he can allow \"All files access\" in the Natro app."
            !file.isFile -> "There's no file $path on the phone."
            !file.canRead() -> "Natro can't read $path."
            file.length() > MAX_BYTES -> "${file.name} is ${FileNames.size(file.length())}; files can be at most 1 GB."
            else -> null
        }
        if (problem != null) {
            Natro.sendMessage(failure(id, problem))
        } else {
            send(id, file, file.name, to)
        }
    }

    /** Sends one file up, and says in a notification how it went. */
    private suspend fun send(id: String, file: File, name: String, to: String): Outcome {
        val transfer = Sending(FileNames.safe(name), to)
        sending[id] = transfer
        val size = file.length()
        val outcome = try {
            sendParts(id, file, transfer, size)
        } catch (e: TimeoutCancellationException) {
            Outcome.Offline  // stalled
        } catch (e: IOException) {
            Natro.sendMessage(failure(id, "The phone couldn't read ${transfer.name}: ${e.message}"))
            Outcome.Failed("The phone couldn't read it: ${e.message}")
        } finally {
            sending.remove(id)
        }
        val there = label(to)
        when (outcome) {
            is Outcome.Sent -> if (outcome.waiting) {
                notify(id, "${transfer.name} will reach your $there", "Your $there is offline: it gets it when it's back.")
            } else {
                passedOn[id] = transfer
                notify(id, "${transfer.name} is on its way to your $there", "Almost there…")
            }
            is Outcome.Failed -> notify(id, "Couldn't send ${transfer.name}", outcome.message, alert = true)
            Outcome.Offline -> notify(id, "${transfer.name} for your $there", "Waiting for Natro's connection…")
        }
        return outcome
    }

    private suspend fun sendParts(id: String, file: File, transfer: Sending, size: Long): Outcome {
        val offer = JSONObject().put("type", "file_offer").put("id", id).put("to", transfer.to)
            .put("name", transfer.name).put("size", size)
        if (!Natro.sendMessage(offer)) return Outcome.Offline
        val part = JSONObject().put("type", "file_part").put("id", id)
        val buffer = ByteArray(PART_BYTES)
        var sent = 0L
        file.inputStream().use { input ->
            while (!transfer.outcome.isCompleted) {
                withTimeout(STALL_MILLIS) { transfer.acked.first { sent - it < WINDOW_BYTES } }
                if (transfer.outcome.isCompleted) break  // refused, or the server has it already
                val count = input.readNBytes(buffer, 0, PART_BYTES)
                if (count <= 0) break
                if (!Natro.sendMessage(part) || !Natro.sendBytes(buffer.toByteString(0, count))) return Outcome.Offline
                sent += count
                if (now() - transfer.shownAt >= PROGRESS_MILLIS) {
                    transfer.shownAt = now()
                    notify(id, "${transfer.name} for your ${label(transfer.to)}", "Sending…", percent(sent, size))
                }
            }
        }
        if (!transfer.outcome.isCompleted) Natro.sendMessage(JSONObject().put("type", "file_end").put("id", id))
        return withTimeout(STALL_MILLIS) { transfer.outcome.await() }
    }

    // Helpers

    private fun notify(id: String, title: String, text: String, progress: Int? = null, alert: Boolean = false,
                       open: Intent? = null) {
        val builder = NotificationCompat.Builder(app, CHANNEL)
            .setSmallIcon(R.drawable.ic_natro)
            .setContentTitle(title)
            .setContentText(text)
            .setSilent(!alert)
        if (progress != null) {
            builder.setProgress(100, progress, false).setOngoing(true)
        } else {
            builder.setAutoCancel(true)
        }
        if (open != null) {
            open.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
            builder.setContentIntent(PendingIntent.getActivity(app, id.hashCode(), open,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
        }
        try {
            notifications.notify(id, 0, builder.build())
        } catch (e: SecurityException) {  // notifications not allowed
        }
    }

    private fun failure(id: String, message: String) = JSONObject().put("type", "file_failed").put("id", id).put("message", message)

    private fun label(device: String) = if (device == "pc") "PC" else device

    private fun percent(done: Long, size: Long) = if (size <= 0) 100 else (done * 100 / size).toInt()

    private fun mime(name: String) = MimeTypeMap.getSingleton()
        .getMimeTypeFromExtension(name.substringAfterLast('.', "").lowercase(Locale.ROOT)) ?: "application/octet-stream"

    private fun now() = System.currentTimeMillis()
}

/** File names and sizes as the server words them (agent/natro_agent/files.py). */
object FileNames {
    private val UNSAFE = Regex("[\\x00-\\x1f\\x7f<>:\"/\\\\|?*]")
    private val RESERVED = setOf("con", "prn", "aux", "nul") + (1..9).flatMap { listOf("com$it", "lpt$it") }
    private const val LONGEST = 150

    /** A name that's safe to save anywhere: no folders, no characters Windows refuses, no reserved names. */
    fun safe(name: String): String {
        var safe = name.split('/', '\\').last().replace(UNSAFE, "_").trim(' ', '.')
        if (safe.substringBefore('.').lowercase(Locale.ROOT) in RESERVED) safe = "_$safe"
        if (safe.length > LONGEST) {
            val extension = safe.substringAfterLast('.', "")
            safe = if (extension.isNotEmpty() && extension.length <= 10) {
                safe.take(LONGEST - extension.length - 1) + "." + extension
            } else {
                safe.take(LONGEST)
            }
        }
        return safe.ifEmpty { "file" }
    }

    fun size(bytes: Long): String {
        var size = bytes.toDouble()
        for (unit in listOf("bytes", "KB", "MB")) {
            if (size < 1024) return if (unit == "bytes") "$bytes bytes" else String.format(Locale.US, "%.1f %s", size, unit)
            size /= 1024
        }
        return String.format(Locale.US, "%.2f GB", size)
    }
}
