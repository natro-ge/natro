package dev.natro

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle

/**
 * Natro's reply, formatted for the bubble's reply card (and as the desktop's reply box does,
 * desktop/src/format.ts). She's asked to write plain spoken sentences (agent/identity/NATRO.md), so
 * mostly this keeps paragraphs and line breaks; when she does use Markdown it shows lists, **bold**,
 * *italics*, `code`, headings and links.
 */
object Markdown {
    sealed interface Block {
        data class Paragraph(val lines: List<String>) : Block
        /** A list item: "•", or its number ("2."). */
        data class Item(val marker: String, val text: String) : Block
        data class Heading(val text: String) : Block
        data class Code(val text: String) : Block
    }

    /** Colors for the words: links, and code's background. */
    class Look(val link: Color, val code: Color)

    private val BULLET = Regex("""^\s*[-*•]\s+(.*)$""")
    private val NUMBERED = Regex("""^\s*(\d+)[.)]\s+(.*)$""")
    private val HEADING = Regex("""^\s*#{1,6}\s+(.*)$""")
    private val INLINE = Regex(
        """`([^`]+)`|\[([^\]]+)]\((https?://[^\s)]+)\)|\*\*([^*]+)\*\*|__([^_]+)__|(?<![*\w])\*([^*\s][^*]*)\*(?!\*)|(https?://[^\s<)]+[^\s<).,!?;:])""",
    )

    fun blocks(text: String): List<Block> {
        val blocks = mutableListOf<Block>()
        var paragraph = mutableListOf<String>()
        var code: MutableList<String>? = null
        fun endParagraph() {
            if (paragraph.isNotEmpty()) blocks += Block.Paragraph(paragraph)
            paragraph = mutableListOf()
        }
        for (line in text.replace("\r\n", "\n").trim().lines()) {
            code?.let { open ->
                if (line.trim().startsWith("```")) {
                    blocks += Block.Code(open.joinToString("\n"))
                    code = null
                } else {
                    open += line
                }
                continue
            }
            val bullet = BULLET.matchEntire(line)
            val numbered = NUMBERED.matchEntire(line)
            val heading = HEADING.matchEntire(line)
            val last = blocks.lastOrNull()
            when {
                line.trim().startsWith("```") -> {
                    endParagraph()
                    code = mutableListOf()
                }
                bullet != null -> {
                    endParagraph()
                    blocks += Block.Item("•", bullet.groupValues[1])
                }
                numbered != null -> {
                    endParagraph()
                    blocks += Block.Item("${numbered.groupValues[1]}.", numbered.groupValues[2])
                }
                heading != null -> {
                    endParagraph()
                    blocks += Block.Heading(heading.groupValues[1])
                }
                line.isBlank() -> endParagraph()
                // A list item going on on the next line.
                paragraph.isEmpty() && last is Block.Item && line.startsWith("  ") ->
                    blocks[blocks.lastIndex] = last.copy(text = last.text + " " + line.trim())
                else -> paragraph += line.trim()
            }
        }
        code?.let { blocks += Block.Code(it.joinToString("\n")) }
        endParagraph()
        return blocks
    }

    /** One block's words with bold, italics, code and links. */
    fun inline(text: String, look: Look): AnnotatedString = buildAnnotatedString {
        var at = 0
        for (match in INLINE.findAll(text)) {
            append(text.substring(at, match.range.first))
            val (code, label, url, bold, bolder, italic, bare) = match.destructured
            when {
                code.isNotEmpty() -> withStyle(SpanStyle(fontFamily = FontFamily.Monospace, background = look.code)) {
                    append(code)
                }
                url.isNotEmpty() -> link(url, label, look)
                bold.isNotEmpty() || bolder.isNotEmpty() -> withStyle(SpanStyle(fontWeight = FontWeight.SemiBold)) {
                    append(bold.ifEmpty { bolder })
                }
                italic.isNotEmpty() -> withStyle(SpanStyle(fontStyle = FontStyle.Italic)) { append(italic) }
                else -> link(bare, bare, look)
            }
            at = match.range.last + 1
        }
        append(text.substring(at))
    }

    private fun AnnotatedString.Builder.link(url: String, label: String, look: Look) {
        val style = TextLinkStyles(SpanStyle(color = look.link, textDecoration = TextDecoration.Underline))
        withLink(LinkAnnotation.Url(url, style)) { append(label) }
    }

    /** A short reply (one line) folds back by itself; anything longer stays until it's tapped. */
    fun isShort(text: String): Boolean = text.trim().let { it.length <= 140 && '\n' !in it }

    /** How long a short reply stays: time to read it, between 4 and 12 seconds. */
    fun readingMillis(text: String): Long = minOf(12_000L, 4_000L + 60L * text.trim().length)
}
