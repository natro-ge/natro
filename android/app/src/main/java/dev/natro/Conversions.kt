package dev.natro

import java.util.Locale

/**
 * What Natro's server can convert to what (server/natro_agent/convert.py; a test there reads this file, so the two
 * stay alike), for the formats ConvertActivity offers. Each kind's targets come most wanted first.
 */
object Conversions {
    /** The biggest file the server converts. */
    const val MOST_BYTES = 300L shl 20

    private val KINDS = mapOf(
        "writer" to setOf("doc", "docx", "odt", "rtf", "txt", "html", "htm"),
        "calc" to setOf("xls", "xlsx", "ods", "csv"),
        "impress" to setOf("ppt", "pptx", "odp"),
        "pdf" to setOf("pdf"),
        "markdown" to setOf("md"),
        "picture" to setOf("jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff", "heic", "heif"),
        "sound" to setOf("mp3", "m4a", "aac", "wav", "ogg", "opus", "flac", "amr", "wma"),
        "video" to setOf("mp4", "mov", "mkv", "webm", "avi", "3gp", "m4v"),
    )
    private val TARGETS = mapOf(
        "writer" to listOf("pdf", "docx", "odt", "rtf", "txt", "html"),
        "calc" to listOf("pdf", "xlsx", "ods", "csv", "html"),
        "impress" to listOf("pdf", "pptx", "odp"),
        "pdf" to listOf("docx", "odt", "txt"),
        "markdown" to listOf("pdf", "docx", "html", "odt"),
        "picture" to listOf("jpg", "png", "pdf", "webp", "gif", "tiff", "bmp"),
        "sound" to listOf("mp3", "m4a", "wav", "ogg", "opus", "flac"),
        "video" to listOf("mp4", "mp3", "gif", "webm", "mov", "mkv", "m4a", "wav", "ogg", "opus", "flac"),
    )
    /** Two names for one format. */
    private val SAME = mapOf("jpeg" to "jpg", "tif" to "tiff", "htm" to "html", "heif" to "heic")
    private val NAMES = mapOf("docx" to "Word", "xlsx" to "Excel", "pptx" to "PowerPoint")

    private fun extension(name: String) = name.substringAfterLast('.', "").lowercase(Locale.ROOT)

    /** What a file of this name can become: none if Natro can't convert it. */
    fun targets(name: String): List<String> {
        val have = extension(name)
        val kind = KINDS.entries.firstOrNull { have in it.value }?.key ?: return emptyList()
        return TARGETS.getValue(kind) - (SAME[have] ?: have)
    }

    /** What all of these can become, in the first one's order. */
    fun common(names: List<String>): List<String> =
        names.map(::targets).reduceOrNull { all, next -> all.filter { it in next } }.orEmpty()

    /** Why common(names) is empty. */
    fun whyNot(names: List<String>): String {
        val cant = names.firstOrNull { targets(it).isEmpty() }
        return when {
            cant == null -> "Those files have no format in common to become."
            extension(cant).isEmpty() -> "Natro can't tell what kind of file $cant is."
            else -> "Natro can't convert .${extension(cant)} files."
        }
    }

    fun label(format: String) = NAMES[format] ?: format.uppercase(Locale.ROOT)
}
