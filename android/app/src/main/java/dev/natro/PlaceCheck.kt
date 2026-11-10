package dev.natro

import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt

/** One of his places, as the brain sends them (server/natro_agent/places.py: place_rules). */
data class Place(val id: String, val name: String, val lat: Double, val lng: Double, val radius: Double,
                 val cards: List<PlaceCard> = emptyList())

/** A card this place shows (server/natro_agent/cards.py). id: its being set here; card: the card itself. */
data class PlaceCard(val id: String, val card: String, val name: String, val on: String, val every: Boolean)

/**
 * Which of the cards at a place are due when the phone gets there or leaves: once, unless it's every time, and then at
 * most once an hour. The phone keeps this itself, as the shop may have no signal for the brain to say so.
 */
class CardTimes {
    private val shown = mutableMapOf<String, Long>()

    fun due(card: PlaceCard, at: Long): Boolean {
        val last = shown[card.id]
        if (last != null && (!card.every || at - last < AGAIN_MILLIS)) return false
        shown[card.id] = at
        return true
    }

    /** What's no longer set is forgotten. */
    fun keep(ids: Set<String>) {
        shown.keys.retainAll(ids)
    }

    fun saved() = JSONObject(shown as Map<*, *>)

    fun restore(saved: JSONObject) {
        for (id in saved.keys()) shown[id] = saved.optLong(id)
    }

    private companion object {
        const val AGAIN_MILLIS = 60 * 60 * 1000L
    }
}

/** The phone got to a place ("arrived") or left it ("left"), at this time. */
data class PlaceEvent(val id: String, val event: String, val at: Long) {
    fun message(): JSONObject = JSONObject().put("type", "place_event").put("id", id).put("event", event).put("at", at)
}

/**
 * Whether the phone has got to each of his places or left it, from its location fixes. Arrived: inside the radius for
 * two fixes in a row. Left: 50 m past it for two fixes, so GPS wobble at the edge doesn't flap. A fix less accurate than
 * the radius doesn't count. The first time a place is known (he may be standing in it as he saves it) nothing is said.
 */
class PlaceCheck {
    private enum class Side { Inside, Outside }

    private class State(var side: Side? = null, var leaning: Side? = null, var times: Int = 0)

    private val states = mutableMapOf<String, State>()
    private var lastAt = 0L

    /** What this fix changed. at: its time, in ms (the same fix given again isn't a second one). */
    fun fix(places: List<Place>, lat: Double, lng: Double, accuracy: Float?, at: Long): List<PlaceEvent> {
        if (at <= lastAt) return emptyList()
        lastAt = at
        return places.mapNotNull { check(it, lat, lng, accuracy, at) }
    }

    private fun check(place: Place, lat: Double, lng: Double, accuracy: Float?, at: Long): PlaceEvent? {
        val radius = max(place.radius, LEAST_RADIUS)
        if (accuracy != null && accuracy > radius) return null
        val away = meters(place.lat, place.lng, lat, lng)
        val side = when {
            away <= radius -> Side.Inside
            away > radius + LEAVE_MARGIN -> Side.Outside
            else -> null
        }
        val state = states.getOrPut(place.id) { State() }
        if (side == null || side == state.side) {
            state.leaning = null
            state.times = 0
            return null
        }
        state.times = if (state.leaning == side) state.times + 1 else 1
        state.leaning = side
        if (state.times < FIXES_IN_A_ROW) return null
        val before = state.side
        state.side = side
        state.leaning = null
        state.times = 0
        if (before == null) return null
        return PlaceEvent(place.id, if (side == Side.Inside) "arrived" else "left", at)
    }

    /** A place that's gone, or has moved: nothing is known of it any more. */
    fun forget(id: String) {
        states.remove(id)
    }

    /** What's known, to keep across restarts, so being somewhere already isn't arriving there again. */
    fun saved(): JSONObject {
        val sides = JSONObject()
        for ((id, state) in states) state.side?.let { sides.put(id, it.name) }
        return JSONObject().put("sides", sides).put("at", lastAt)
    }

    fun restore(saved: JSONObject) {
        lastAt = saved.optLong("at")
        val sides = saved.optJSONObject("sides") ?: return
        for (id in sides.keys()) {
            val side = Side.entries.firstOrNull { it.name == sides.optString(id) } ?: continue
            states[id] = State(side)
        }
    }

    companion object {
        const val LEAST_RADIUS = 100.0
        const val LEAVE_MARGIN = 50.0
        const val FIXES_IN_A_ROW = 2

        fun placesFrom(array: JSONArray?): List<Place> = List(array?.length() ?: 0) { i ->
            val place = array!!.optJSONObject(i) ?: return@List null
            val lat = place.optDouble("lat")
            val lng = place.optDouble("lng")
            val id = place.optString("id")
            if (id.isBlank() || lat.isNaN() || lng.isNaN()) null
            else Place(id, place.optString("name"), lat, lng, place.optDouble("radius", LEAST_RADIUS),
                cardsFrom(place.optJSONArray("cards")))
        }.filterNotNull()

        private fun cardsFrom(array: JSONArray?): List<PlaceCard> = List(array?.length() ?: 0) { i ->
            val card = array!!.optJSONObject(i) ?: return@List null
            if (card.optString("id").isBlank() || card.optString("card").isBlank()) null
            else PlaceCard(card.optString("id"), card.optString("card"), card.optString("name"), card.optString("on"),
                card.optBoolean("every"))
        }.filterNotNull()

        fun placesJson(places: List<Place>): JSONArray = JSONArray(places.map { place ->
            JSONObject().put("id", place.id).put("name", place.name).put("lat", place.lat).put("lng", place.lng)
                .put("radius", place.radius).put("cards", JSONArray(place.cards.map { card ->
                    JSONObject().put("id", card.id).put("card", card.card).put("name", card.name).put("on", card.on)
                        .put("every", card.every)
                }))
        })

        /** Meters between two points on Earth. */
        fun meters(lat1: Double, lng1: Double, lat2: Double, lng2: Double): Double {
            val a = sin(Math.toRadians(lat2 - lat1) / 2).pow(2) +
                cos(Math.toRadians(lat1)) * cos(Math.toRadians(lat2)) * sin(Math.toRadians(lng2 - lng1) / 2).pow(2)
            return 2 * EARTH_METERS * asin(sqrt(a))
        }

        private const val EARTH_METERS = 6_371_000.0
    }
}
