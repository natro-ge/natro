package dev.natro

import org.junit.Assert.assertEquals
import org.junit.Test

class ConversionsTest {
    @Test
    fun aFileCanBecomeWhatItIsnt() {
        assertEquals(listOf("docx", "odt", "txt"), Conversions.targets("report.PDF"))
        assertEquals(listOf("pdf", "odt", "rtf", "txt", "html"), Conversions.targets("letter.docx"))
        // An old Word file can become a new one.
        assertEquals(listOf("pdf", "docx", "odt", "rtf", "txt", "html"), Conversions.targets("letter.doc"))
        assertEquals(listOf("png", "pdf", "webp", "gif", "tiff", "bmp"), Conversions.targets("photo.jpeg"))
        assertEquals(emptyList<String>(), Conversions.targets("setup.exe"))
        assertEquals(emptyList<String>(), Conversions.targets("README"))
    }

    @Test
    fun severalFilesOfferWhatTheyCanAllBecome() {
        assertEquals(listOf("pdf", "webp", "gif", "tiff", "bmp"), Conversions.common(listOf("a.jpg", "b.png")))
        assertEquals(listOf("pdf"), Conversions.common(listOf("a.jpg", "b.docx")))
        assertEquals(emptyList<String>(), Conversions.common(listOf("a.mp3", "b.docx")))
        assertEquals(emptyList<String>(), Conversions.common(emptyList()))
    }

    @Test
    fun itSaysWhyNot() {
        assertEquals("Natro can't convert .exe files.", Conversions.whyNot(listOf("a.pdf", "setup.exe")))
        assertEquals("Natro can't tell what kind of file README is.", Conversions.whyNot(listOf("README")))
        assertEquals("Those files have no format in common to become.", Conversions.whyNot(listOf("a.mp3", "b.docx")))
    }

    @Test
    fun formatsReadAsPeopleCallThem() {
        assertEquals(listOf("Word", "Excel", "PDF", "MP3"), listOf("docx", "xlsx", "pdf", "mp3").map(Conversions::label))
    }
}
