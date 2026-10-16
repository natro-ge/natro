package dev.natro

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.font.FontWeight
import dev.natro.Markdown.Block
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MarkdownTest {
    private val look = Markdown.Look(link = Color.Green, code = Color.Gray)

    @Test
    fun plainSentencesStayParagraphs() {
        assertEquals(
            listOf(Block.Paragraph(listOf("It's sunny, 24 degrees.", "Rain later.")), Block.Paragraph(listOf("Anything else?"))),
            Markdown.blocks("It's sunny, 24 degrees.\nRain later.\n\nAnything else?"),
        )
    }

    @Test
    fun listsHeadingsAndCode() {
        val blocks = Markdown.blocks("## Today\nHere's your day:\n- **9:30** standup\n- lunch with Nino\n  at the cafe\n2. dentist\n```\nnpm run dev\n```")
        assertEquals(
            listOf(
                Block.Heading("Today"),
                Block.Paragraph(listOf("Here's your day:")),
                Block.Item("•", "**9:30** standup"),
                Block.Item("•", "lunch with Nino at the cafe"),
                Block.Item("2.", "dentist"),
                Block.Code("npm run dev"),
            ),
            blocks,
        )
    }

    @Test
    fun boldItalicsCodeAndLinks() {
        val text = Markdown.inline("**Bold**, *soft*, `code`, [the map](https://maps.example.com) and https://example.com/a.", look)
        assertEquals("Bold, soft, code, the map and https://example.com/a.", text.text)
        assertEquals(FontWeight.SemiBold, text.spanStyles.first { text.text.substring(it.start, it.end) == "Bold" }.item.fontWeight)
        val links = text.getLinkAnnotations(0, text.length).map { (it.item as LinkAnnotation.Url).url }
        assertEquals(listOf("https://maps.example.com", "https://example.com/a"), links)  // the full stop isn't the link's
    }

    @Test
    fun aStarAloneIsJustAStar() {
        assertEquals("5 * 3 = 15", Markdown.inline("5 * 3 = 15", look).text)
    }

    @Test
    fun shortRepliesFoldBackByThemselves() {
        assertTrue(Markdown.isShort("Paused the music."))
        assertFalse(Markdown.isShort("Here's your day:\n- standup"))
        assertFalse(Markdown.isShort("a".repeat(141)))
        assertEquals(5_020L, Markdown.readingMillis("Paused the music."))
        assertEquals(12_000L, Markdown.readingMillis("a".repeat(500)))
    }
}
