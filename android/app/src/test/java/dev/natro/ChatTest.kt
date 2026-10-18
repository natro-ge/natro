package dev.natro

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatTest {
    private val tool = ToolRun("phone_open_app", "{\"app\":\"spotify\"}", ok = true, declined = false, result = "Opened Spotify.")

    @Test
    fun aSpokenRequestFillsInAsItIsHeardAndAnswered() {
        var turns = Chat.asked(emptyList(), "r1", Turn.From.Spoken, "", time = 1)
        turns = Chat.english(turns, "r1", "Open ")
        turns = Chat.english(turns, "r1", "Spotify.")
        turns = Chat.heard(turns, "r1", "სპოტიფაი გახსენი", "Open Spotify.")
        turns = Chat.step(turns, "r1", "Opening Spotify")
        turns = Chat.step(turns, "r1", "Opening Spotify")  // said twice, kept once
        turns = Chat.toolRan(turns, "r1", tool, time = 2)
        turns = Chat.replyText(turns, "r1", "Opened")
        val turn = Chat.replied(turns, "r1", "Opened Spotify.", "gemini", 0.001, 2.1, tools = null).single()
        assertEquals("Open Spotify.", turn.asked)
        assertEquals("სპოტიფაი გახსენი", turn.heard)
        assertEquals(listOf("Opening Spotify"), turn.steps)
        assertEquals("Opened Spotify.", turn.reply)
        assertEquals(Turn.State.Done, turn.state)
        assertEquals("", turn.step)
        assertEquals(listOf(tool), turn.tools)  // the reply named none: the phone's own list stays
        assertTrue(turn.hasDetails)
    }

    @Test
    fun theRepliesToolListReplacesThePhones() {
        val brain = listOf(tool, ToolRun("spotify_play", "{}", ok = true, declined = false, result = "Playing."))
        var turns = Chat.asked(emptyList(), "r1", Turn.From.Typed, "play jazz", time = 1)
        turns = Chat.toolRan(turns, "r1", tool, time = 2)
        assertEquals(brain, Chat.replied(turns, "r1", "Playing.", "claude", null, null, brain).single().tools)
    }

    @Test
    fun aCancelledTurnStaysAsItIs() {
        var turns = Chat.asked(emptyList(), "r1", Turn.From.Typed, "call mom", time = 1)
        turns = Chat.cancelled(turns, "r1")
        val cancelled = turns.single()
        turns = Chat.replyText(turns, "r1", "Calling")
        turns = Chat.replied(turns, "r1", "Calling Mom.", "claude", 0.01, 3.0, null)
        turns = Chat.failed(turns, "r1", "Lost the connection to Natro.")
        assertEquals(cancelled, turns.single())
        assertEquals(Turn.State.Cancelled, cancelled.state)
    }

    @Test
    fun onlyAWorkingTurnFails() {
        var turns = Chat.asked(emptyList(), "r1", Turn.From.Typed, "hi", time = 1)
        turns = Chat.replied(turns, "r1", "Hi!", "gemini", null, null, null)
        assertEquals(Turn.State.Done, Chat.failed(turns, "r1", "Lost the connection to Natro.").single().state)
    }

    @Test
    fun questionsAndTheirAnswers() {
        var turns = Chat.asked(emptyList(), "r1", Turn.From.Typed, "call mom", time = 1)
        turns = Chat.asking(turns, "r1", "Call Mom?", "+995 555 12 34 56")
        turns = Chat.answered(turns, "r1", "Yes")
        turns = Chat.answered(turns, "r1", "No")  // nothing open any more
        assertEquals(listOf(Asked("Call Mom?", "+995 555 12 34 56", "Yes")), turns.single().questions)
    }

    @Test
    fun thePcsUsesOfThePhoneGroupWhileTheyreClose() {
        var turns = Chat.asked(emptyList(), "r1", Turn.From.Typed, "hi", time = 0)
        turns = Chat.toolRan(turns, null, tool, time = 1_000)
        turns = Chat.toolRan(turns, null, tool, time = 30_000)
        turns = Chat.toolRan(turns, null, tool, time = 500_000)
        assertEquals(listOf(Turn.From.Typed, Turn.From.Pc, Turn.From.Pc), turns.map { it.from })
        assertEquals(listOf(0, 2, 1), turns.map { it.tools.size })
    }

    @Test
    fun theLatestTurnWithAnIdChanges() {
        var turns = Chat.asked(emptyList(), "r1", Turn.From.Typed, "first", time = 1)
        turns = Chat.replied(turns, "r1", "One.", "gemini", null, null, null)
        turns = Chat.asked(turns, "r1", Turn.From.Typed, "second", time = 2)
        turns = Chat.replyText(turns, "r1", "Two.")
        assertEquals(listOf("One.", "Two."), turns.map { it.reply })
    }

    @Test
    fun theChatKeepsTheLatestTurns() {
        val turns = (1..Chat.MAX_TURNS + 5).fold(emptyList<Turn>()) { all, i ->
            Chat.asked(all, "r$i", Turn.From.Typed, "$i", time = i.toLong())
        }
        assertEquals(Chat.MAX_TURNS, turns.size)
        assertEquals("r6", turns.first().id)
    }

    private fun entry(device: String, request: String, reply: String = "", done: Boolean = false) = JSONObject()
        .put("id", "$device:$request").put("device", device).put("label", if (device == "pc") "PC" else device)
        .put("request", request).put("time", 5L).put("asked", "what's on my screen").put("heard", "").put("spoken", false)
        .put("reply", reply).put("step", "").put("done", done).put("problem", "").put("pictures", JSONArray())
        .put("question", JSONObject.NULL).put("queued", JSONObject.NULL).put("sent", false)

    @Test
    fun thePcsRequestsShowAsTheBrainHasThemAndKeepTheirStepsAndPhoneTools() {
        var turns = Chat.entry(emptyList(), entry("pc", "r1").put("step", "Taking a screenshot"))
        turns = Chat.toolRan(turns, null, tool, time = 6, request = "pc:r1")
        turns = Chat.entry(turns, entry("pc", "r1", "Here it is.", done = true).put("pictures", JSONArray(listOf("pic2"))))
        val turn = turns.single()
        assertEquals(Turn.From.Other, turn.from)
        assertEquals("PC", turn.device)
        assertEquals("" , turn.queued)  // JSON null is no device, not "null"
        assertEquals(listOf("Taking a screenshot"), turn.steps)
        assertEquals(listOf(tool), turn.tools)
        assertEquals(listOf("pic2"), turn.pictures)
        assertEquals(Turn.State.Done, turn.state)
    }

    @Test
    fun thePhonesOwnRequestsTakeOnlyTheirPicturesFromTheBrain() {
        var turns = Chat.asked(emptyList(), "r1", Turn.From.Typed, "show me my PC screen", time = 1)
        turns = Chat.replyText(turns, "r1", "Here")
        turns = Chat.entry(turns, entry("phone", "r1", "Here it is.", done = true).put("pictures", JSONArray(listOf("pic1"))))
        val turn = turns.single()
        assertEquals("Here", turn.reply)
        assertEquals(Turn.State.Working, turn.state)
        assertEquals(listOf("pic1"), turn.pictures)
    }

    @Test
    fun questionsAboutThePcsRequestsAreAnsweredHere() {
        val asking = entry("pc", "r2").put("question", JSONObject().put("id", "c1").put("text", "Force close Steam?").put("answer", ""))
        var turns = Chat.entry(emptyList(), asking)
        assertEquals(Asked("Force close Steam?", "", "", "c1", "pc"), turns.single().questions.single())
        turns = Chat.answeredFor(turns, "pc", "c1", "Yes")
        turns = Chat.answeredFor(turns, "pc", "c1", "No")  // answered already
        assertEquals("Yes", turns.single().questions.single().answer)
    }

    @Test
    fun aNewChatAndCatchingUp() {
        var turns = Chat.asked(emptyList(), "r1", Turn.From.Typed, "hi", time = 1)
        turns = Chat.replied(turns, "r1", "Hi!", "gemini", null, null, null)
        turns = Chat.asked(turns, "r2", Turn.From.Typed, "and now", time = 2)
        turns = Chat.entry(turns, entry("pc", "r3", "Done.", done = true))
        // Catching up: the PC's turns as the brain has them now (r3 went with a new chat there), the phone's kept.
        assertEquals(listOf("r1", "r2", "pc:r4"), Chat.all(turns, JSONArray(listOf(entry("pc", "r4")))).map { it.id })
        assertEquals(listOf("r2"), Chat.cleared(turns).map { it.id })
    }

    @Test
    fun aReminderIsHerWordsAlone() {
        val reminder = entry("natro", "rm1-17", "Time to call Nino.", done = true).put("asked", "").put("reminder", true)
        val turn = Chat.entry(emptyList(), reminder).single()
        assertTrue(turn.reminder)
        assertEquals("natro:rm1-17", turn.id)
        assertEquals("Time to call Nino.", turn.reply)
        assertEquals(Turn.State.Done, turn.state)
        assertEquals(false, Chat.entry(emptyList(), entry("pc", "r1")).single().reminder)
        val alert = entry("natro", "al1", "Nino emailed: Dinner.", done = true).put("asked", "").put("alert", true)
        val seen = Chat.entry(emptyList(), alert).single()
        assertTrue(seen.alert)
        assertEquals(false, seen.reminder)
    }

    @Test
    fun aBackgroundJobIsHerWorkWithItsTask() {
        val question = JSONObject().put("id", "c4").put("text", "I'll email Nino the flats.").put("answer", "")
            .put("device", "pc")
        val job = entry("natro", "jb1").put("asked", "").put("job", "Find three flats under \$600.")
            .put("step", "Searching flats").put("question", question)
        val turn = Chat.entry(emptyList(), job).single()
        assertEquals("Find three flats under \$600.", turn.job)
        assertEquals(Turn.State.Working, turn.state)
        assertEquals("", turn.asked)
        // Answered here, it goes to the brain as the device "natro"'s: the brain knows where it was asked.
        assertEquals("natro", turn.questions.single().device)
        assertEquals("", Chat.entry(emptyList(), entry("pc", "r1")).single().job)
    }

    @Test
    fun turnsLeftWorkingWereInterrupted() {
        var turns = Chat.asked(emptyList(), "r1", Turn.From.Typed, "hi", time = 1)
        turns = Chat.replied(turns, "r1", "Hi!", "gemini", null, null, null)
        turns = Chat.asked(turns, "r2", Turn.From.Spoken, "", time = 2)
        assertEquals(listOf(Turn.State.Done, Turn.State.Failed), Chat.interrupted(turns).map { it.state })
    }
}
