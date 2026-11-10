package dev.natro

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.location.LocationListener
import android.location.LocationManager
import android.location.LocationRequest
import androidx.core.content.edit
import kotlinx.coroutines.flow.MutableStateFlow
import org.json.JSONArray
import org.json.JSONObject

/**
 * His places on the phone: the brain sends them, and the phone checks its own location against them (PlaceCheck) and
 * sends only that it got to one or left it. Where he is otherwise never leaves the phone. NatroService runs this while
 * there are places. One that happens offline is kept, and sent when the phone connects, with its time. The messages are
 * in shared/PROTOCOL.md (place_rules, place_event).
 */
object Places {
    /** The places now, as the brain last sent them (on connecting, and whenever they change). */
    val rules = MutableStateFlow<List<Place>>(emptyList())

    private lateinit var store: SharedPreferences
    private val check = PlaceCheck()
    private val waiting = ArrayDeque<PlaceEvent>()
    private val cardTimes = CardTimes()
    private var listener: LocationListener? = null
    private var app: Context? = null

    fun init(context: Context) {
        if (::store.isInitialized) return
        app = context.applicationContext
        store = context.getSharedPreferences("places", Context.MODE_PRIVATE)
        runCatching { cardTimes.restore(JSONObject(store.getString(CARD_TIMES, "{}") ?: "{}")) }
        rules.value = runCatching { PlaceCheck.placesFrom(JSONArray(store.getString(RULES, "[]"))) }.getOrDefault(emptyList())
        runCatching { check.restore(JSONObject(store.getString(STATE, "{}") ?: "{}")) }
        runCatching {
            val kept = JSONArray(store.getString(WAITING, "[]"))
            for (i in 0 until kept.length()) {
                val event = kept.getJSONObject(i)
                waiting.addLast(PlaceEvent(event.getString("id"), event.getString("event"), event.getLong("at")))
            }
        }
    }

    /** The brain's place_rules. Also how the phone hears it's connected, so what waited goes now. */
    @Synchronized
    fun setRules(array: JSONArray?) {
        val places = PlaceCheck.placesFrom(array)
        val now = places.associateBy { it.id }
        for (old in rules.value) {
            val place = now[old.id]
            if (place == null || place.lat != old.lat || place.lng != old.lng || place.radius != old.radius) check.forget(old.id)
        }
        rules.value = places
        store.edit { putString(RULES, PlaceCheck.placesJson(places).toString()) }
        save()
        flush()
        val cards = places.flatMap { it.cards }
        cardTimes.keep(cards.map { it.id }.toSet())
        Cards.keepOnly(cards.map { it.card }.toSet())
    }

    @Synchronized
    private fun fixed(lat: Double, lng: Double, accuracy: Float?, at: Long) {
        val events = check.fix(rules.value, lat, lng, accuracy, at)
        waiting += events
        while (waiting.size > MOST_WAITING) waiting.removeFirst()
        // A card is shown here and now, with or without a connection.
        for (event in events) {
            val cards = rules.value.firstOrNull { it.id == event.id }?.cards.orEmpty()
            for (card in cards.filter { it.on == event.event && cardTimes.due(it, event.at) }) {
                app?.let { Cards.arrived(it, card) }
            }
        }
        store.edit { putString(CARD_TIMES, cardTimes.saved().toString()) }
        save()
        flush()
    }

    private fun flush() {
        while (waiting.isNotEmpty() && Natro.sendMessage(waiting.first().message())) waiting.removeFirst()
        store.edit { putString(WAITING, JSONArray(waiting.map { it.message() }).toString()) }
    }

    private fun save() {
        store.edit { putString(STATE, check.saved().toString()) }
    }

    /** Whether Natro may have the location from the background, as well as with the app open. */
    fun alwaysAllowed(context: Context) =
        context.checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION) == PackageManager.PERMISSION_GRANTED

    /** Check the location, or stop, as wanted now (NatroService). */
    @SuppressLint("MissingPermission")  // NatroService wants this only with it allowed
    fun sync(context: Context, wanted: Boolean) {
        if (wanted == (listener != null)) return
        val manager = context.getSystemService(LocationManager::class.java)
        if (!wanted) {
            listener?.let { manager.removeUpdates(it) }
            listener = null
            return
        }
        val heard = LocationListener { fix ->
            fixed(fix.latitude, fix.longitude, if (fix.hasAccuracy()) fix.accuracy else null, fix.time)
        }
        val provider = if (manager.hasProvider(LocationManager.FUSED_PROVIDER)) LocationManager.FUSED_PROVIDER
            else LocationManager.NETWORK_PROVIDER
        // Android takes a distance on top of the time, not instead of it, and a phone that stays where it got to would
        // then never have its second fix: so by the time alone.
        val request = LocationRequest.Builder(FIX_MILLIS).setQuality(LocationRequest.QUALITY_BALANCED_POWER_ACCURACY)
            .setMinUpdateIntervalMillis(FIX_MILLIS / 4).build()
        try {
            manager.requestLocationUpdates(provider, request, context.mainExecutor, heard)
            listener = heard
        } catch (e: RuntimeException) {  // not allowed after all, or no such provider: the next refresh tries again
            listener = null
        }
    }

    private const val FIX_MILLIS = 2 * 60 * 1000L
    private const val MOST_WAITING = 50
    private const val RULES = "rules"
    private const val STATE = "state"
    private const val WAITING = "waiting"
    private const val CARD_TIMES = "card-times"
}
