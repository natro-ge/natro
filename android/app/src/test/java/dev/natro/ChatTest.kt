package dev.natro

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

    @Test
    fun turnsLeftWorkingWereInterrupted() {
        var turns = Chat.asked(emptyList(), "r1", Turn.From.Typed, "hi", time = 1)
        turns = Chat.replied(turns, "r1", "Hi!", "gemini", null, null, null)
        turns = Chat.asked(turns, "r2", Turn.From.Spoken, "", time = 2)
        assertEquals(listOf(Turn.State.Done, Turn.State.Failed), Chat.interrupted(turns).map { it.state })
    }
}
