package dev.natro

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PlaceCheckTest {
    private val gym = Place("pl1", "Gym", 41.5400, 45.0000, 150.0)
    private val places = listOf(gym)

    // Points north of the gym, by about these meters.
    private fun north(meters: Double) = gym.lat + meters / 111_195.0

    private var clock = 1000L

    private fun PlaceCheck.at(meters: Double, accuracy: Float? = 20f, on: List<Place> = places) =
        fix(on, north(meters), gym.lng, accuracy, ++clock)

    /** A check that already knows he's outside (or inside). */
    private fun knowing(meters: Double) = PlaceCheck().also {
        it.at(meters)
        it.at(meters)
    }

    private val away = 1000.0
    private val there = 50.0

    @Test
    fun metersBetweenTwoPoints() {
        assertEquals(1000.0, PlaceCheck.meters(gym.lat, gym.lng, north(1000.0), gym.lng), 1.0)
        assertEquals(0.0, PlaceCheck.meters(gym.lat, gym.lng, gym.lat, gym.lng), 0.001)
    }

    @Test
    fun theFirstFixesOfAPlaceSayNothing() {
        val check = PlaceCheck()
        assertEquals(emptyList<PlaceEvent>(), check.at(there))  // he may be standing in it as he saves it
        assertEquals(emptyList<PlaceEvent>(), check.at(there))
        assertEquals(emptyList<PlaceEvent>(), check.at(there))
    }

    @Test
    fun arrivedTakesTwoFixesInARow() {
        val check = knowing(away)
        assertEquals(emptyList<PlaceEvent>(), check.at(there))
        val events = check.at(there)
        assertEquals(listOf(PlaceEvent("pl1", "arrived", clock)), events)
        assertEquals(emptyList<PlaceEvent>(), check.at(there))  // said once
    }

    @Test
    fun leftTakesTwoFixesInARowPastTheMargin() {
        val check = knowing(there)
        assertEquals(emptyList<PlaceEvent>(), check.at(away))
        val events = check.at(away)
        assertEquals(listOf(PlaceEvent("pl1", "left", clock)), events)
        assertEquals(emptyList<PlaceEvent>(), check.at(away))
    }

    @Test
    fun oneFixAwayAndBackIsNothing() {
        val check = knowing(there)
        assertEquals(emptyList<PlaceEvent>(), check.at(away))
        assertEquals(emptyList<PlaceEvent>(), check.at(there))
        assertEquals(emptyList<PlaceEvent>(), check.at(away))  // starts again
        assertEquals("left", check.at(away).single().event)
    }

    @Test
    fun wobbleAtTheEdgeDoesntFlap() {
        val check = knowing(there)
        // Just past the radius (150 m), within the margin, again and again: he hasn't left.
        repeat(5) { assertEquals(emptyList<PlaceEvent>(), check.at(170.0)) }
        repeat(2) { assertEquals(emptyList<PlaceEvent>(), check.at(140.0)) }
        repeat(5) { assertEquals(emptyList<PlaceEvent>(), check.at(190.0)) }
        assertEquals(emptyList<PlaceEvent>(), check.at(201.0))  // past the margin, once
        assertEquals("left", check.at(201.0).single().event)
    }

    @Test
    fun theEdgeBreaksARow() {
        val check = knowing(away)
        assertEquals(emptyList<PlaceEvent>(), check.at(there))
        assertEquals(emptyList<PlaceEvent>(), check.at(170.0))
        assertEquals(emptyList<PlaceEvent>(), check.at(there))
        assertEquals("arrived", check.at(there).single().event)
    }

    @Test
    fun aPoorFixDoesntCount() {
        val check = knowing(away)
        repeat(3) { assertEquals(emptyList<PlaceEvent>(), check.at(there, accuracy = 400f)) }
        assertEquals(emptyList<PlaceEvent>(), check.at(there))
        assertEquals(emptyList<PlaceEvent>(), check.at(there, accuracy = 400f))  // nor breaks a row
        assertEquals("arrived", check.at(there).single().event)
    }

    @Test
    fun aFixWithNoAccuracyCounts() {
        val check = knowing(away)
        check.at(there, accuracy = null)
        assertEquals("arrived", check.at(there, accuracy = null).single().event)
    }

    @Test
    fun theSameFixGivenAgainIsNotASecondOne() {
        val check = knowing(away)
        val inside = north(there)
        assertEquals(emptyList<PlaceEvent>(), check.fix(places, inside, gym.lng, 20f, ++clock))
        assertEquals(emptyList<PlaceEvent>(), check.fix(places, inside, gym.lng, 20f, clock))
        assertEquals(emptyList<PlaceEvent>(), check.fix(places, inside, gym.lng, 20f, clock - 500))
        assertEquals("arrived", check.at(there).single().event)
    }

    @Test
    fun aRadiusIsAtLeastTheLeast() {
        val small = listOf(gym.copy(radius = 20.0))
        val check = PlaceCheck()
        check.at(away, on = small)
        check.at(away, on = small)
        check.at(90.0, on = small)
        assertEquals("arrived", check.at(90.0, on = small).single().event)
    }

    @Test
    fun eachPlaceOnItsOwn() {
        val work = Place("pl2", "Work", north(away), gym.lng, 150.0)
        val both = listOf(gym, work)
        val check = PlaceCheck()
        check.at(there, on = both)
        check.at(there, on = both)  // known: at the gym, not at work
        check.at(away, on = both)
        val events = check.at(away, on = both)
        assertEquals(setOf("pl1" to "left", "pl2" to "arrived"), events.map { it.id to it.event }.toSet())
    }

    @Test
    fun beingThereAcrossARestartIsntArriving() {
        val check = knowing(there)
        val restored = PlaceCheck().also { it.restore(JSONObject(check.saved().toString())) }
        assertEquals(emptyList<PlaceEvent>(), restored.at(there))
        assertEquals(emptyList<PlaceEvent>(), restored.at(there))
        restored.at(away)
        assertEquals("left", restored.at(away).single().event)
    }

    @Test
    fun aPlaceForgottenIsNewAgain() {
        val check = knowing(away)
        check.forget("pl1")
        check.at(there)
        assertEquals(emptyList<PlaceEvent>(), check.at(there))  // nothing's known of it: nothing's said
    }

    @Test
    fun placesAsTheBrainSendsThem() {
        val sent = JSONArray("""[{"id": "pl1", "name": "Gym", "lat": 41.54, "lng": 45.0, "radius": 200},
            {"id": "", "name": "No id", "lat": 1, "lng": 2, "radius": 200}, {"id": "pl3", "name": "No point"}, 5]""")
        assertEquals(listOf(Place("pl1", "Gym", 41.54, 45.0, 200.0)), PlaceCheck.placesFrom(sent))
        assertEquals(emptyList<Place>(), PlaceCheck.placesFrom(null))
        assertEquals(places, PlaceCheck.placesFrom(PlaceCheck.placesJson(places)))
    }

    private val once = PlaceCard("ct1", "cd1", "Carrefour", "arrived", false)
    private val always = once.copy(id = "ct2", every = true)

    @Test
    fun aCardOnceIsDueOnce() {
        val times = CardTimes()
        assertTrue(times.due(once, 1000))
        assertEquals(false, times.due(once, 1000 + 3 * 3600 * 1000))
    }

    @Test
    fun aCardEveryTimeIsDueAtMostOnceAnHour() {
        val times = CardTimes()
        assertTrue(times.due(always, 1000))
        assertEquals(false, times.due(always, 1000 + 30 * 60 * 1000))
        assertTrue(times.due(always, 1000 + 61 * 60 * 1000))
    }

    @Test
    fun whatsNoLongerSetIsForgottenAndTheRestSurviveARestart() {
        val times = CardTimes()
        times.due(once, 1000)
        times.due(always, 1000)
        times.keep(setOf("ct2"))
        val restored = CardTimes().also { it.restore(JSONObject(times.saved().toString())) }
        assertTrue(restored.due(once, 2000))  // set anew: due again
        assertEquals(false, restored.due(always, 2000))
    }

    @Test
    fun placesCarryTheirCards() {
        val sent = JSONArray("""[{"id": "pl1", "name": "Shop", "lat": 41.5, "lng": 45.0, "radius": 150, "cards": [
            {"id": "ct1", "card": "cd1", "name": "Carrefour", "on": "arrived", "every": false},
            {"id": "", "card": "cd2"}, {"id": "ct3"}, 5]}]""")
        val place = PlaceCheck.placesFrom(sent).single()
        assertEquals(listOf(once), place.cards)
        assertEquals(listOf(place), PlaceCheck.placesFrom(PlaceCheck.placesJson(listOf(place))))
    }

    @Test
    fun anEventsMessage() {
        val message = PlaceEvent("pl1", "left", 1761000000000).message()
        assertTrue(message.getString("type") == "place_event" && message.getString("id") == "pl1")
        assertEquals("left", message.getString("event"))
        assertEquals(1761000000000, message.getLong("at"))
    }
}
