package dev.natro

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AppUpdateTest {
    private val message = JSONObject("""{"type": "app_update", "version": 300, "size": 66000000, "sha256": "abc",
        "path": "/app/natro.apk"}""")

    @Test
    fun onlyANewerBuildIsAnUpdate() {
        val update = AppUpdate.from(message)!!
        assertTrue(update.isFor(299))
        assertFalse(update.isFor(300))
        assertFalse(update.isFor(301))
    }

    @Test
    fun theRowInSettingsAlwaysOffersSomething() {
        assertEquals("Check for updates", AppUpdate.offer(null, false))
        assertEquals("Update to version 300", AppUpdate.offer(300, false))
        assertEquals("Getting version 300…", AppUpdate.offer(300, true))
    }

    @Test
    fun itsFetchedFromTheBrainsOwnAddress() {
        val update = AppUpdate.from(message)!!
        assertEquals("http://100.101.107.5:8700/app/natro.apk", update.url("ws://100.101.107.5:8700"))
        assertEquals("http://host:8700/app/natro.apk", update.url("ws://host:8700/"))
        assertEquals("https://host/app/natro.apk", update.url("wss://host"))
    }

    @Test
    fun itComesPieceByPieceToItsVeryEnd() {
        val small = AppUpdate(300, 2_500_000, "abc", "/app/natro.apk")
        assertEquals("bytes=0-1048575", small.range(0))
        assertEquals("bytes=1048576-2097151", small.range(1_048_576))
        assertEquals("bytes=2097152-2499999", small.range(2_097_152))
        // Picks up where it stopped, whatever that is.
        assertEquals("bytes=5000-1053575", small.range(5000))
    }

    @Test
    fun whatDoesntSayItAllIsNothing() {
        for (missing in listOf("version", "size", "sha256", "path")) {
            val partial = JSONObject(message.toString()).also { it.remove(missing) }
            assertNull(missing, AppUpdate.from(partial))
        }
        assertNull(AppUpdate.from(JSONObject(message.toString()).put("path", "http://elsewhere.example/x.apk")))
    }
}
