package dev.natro

import org.junit.Assert.assertEquals
import org.junit.Test

/** The same cases as the server's (server/tests/test_files.py), so both sides name a file alike. */
class FileNamesTest {
    @Test
    fun namesAreSafeToSave() {
        assertEquals("report.pdf", FileNames.safe("report.pdf"))
        assertEquals("evil.exe", FileNames.safe("..\\..\\Windows\\evil.exe"))
        assertEquals("passwd", FileNames.safe("/etc/passwd"))
        assertEquals("a_b_c_d_e_.txt", FileNames.safe("a<b>c:d|e?.txt"))
        assertEquals("_CON.txt", FileNames.safe("CON.txt"))
        assertEquals("hidden", FileNames.safe("  .hidden. "))
        assertEquals("file", FileNames.safe(""))
        assertEquals("x".repeat(146) + ".jpg", FileNames.safe("x".repeat(300) + ".jpg"))
    }

    @Test
    fun sizesReadWell() {
        assertEquals(listOf("8 bytes", "2.0 KB", "5.5 MB", "3.00 GB"),
            listOf(8L, 2048L, 5L * (1 shl 20) + (1 shl 19), 3L shl 30).map(FileNames::size))
    }
}
