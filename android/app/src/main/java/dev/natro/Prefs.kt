package dev.natro

import android.content.Context
import android.content.SharedPreferences
import androidx.core.content.edit
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/** Where the owner parked the bubble: on the left or right edge, its center at y (a fraction of the screen's height). */
data class Parked(val right: Boolean, val y: Float)

/** Natro's settings on the phone, remembered across restarts and watched by the screens and services. */
object Prefs {
    private lateinit var store: SharedPreferences

    private val _power = MutableStateFlow(true)
    private val _wakeWord = MutableStateFlow(false)
    private val _speakReplies = MutableStateFlow(true)
    private val _showReplies = MutableStateFlow(true)
    private val _keepListening = MutableStateFlow(true)
    private val _parked = MutableStateFlow<Parked?>(null)
    private val _messagesToPc = MutableStateFlow(true)
    private val _worksOn = MutableStateFlow(WorksOn.PHONE)
    private val _places = MutableStateFlow(true)

    /** Natro on at all. Off: no connection, no listening, no bubble, no notification. */
    val power: StateFlow<Boolean> = _power
    /** Listening for "OK Natro". */
    val wakeWord: StateFlow<Boolean> = _wakeWord
    val speakReplies: StateFlow<Boolean> = _speakReplies
    /** Her replies open as text from the bubble (the reply card). */
    val showReplies: StateFlow<Boolean> = _showReplies
    /** A few seconds after a reply to something he said, he can go on by just talking (Natro.listenForMore). */
    val keepListening: StateFlow<Boolean> = _keepListening
    /** The bubble's place on an edge, or null: then it shows only while Natro is busy. */
    val parked: StateFlow<Parked?> = _parked
    /** Messages that come to the phone show on the PC, to answer there (Messages). */
    val messagesToPc: StateFlow<Boolean> = _messagesToPc
    /** Where Natro works for what's asked here: "phone" or "pc" (WorksOn). */
    val worksOn: StateFlow<String> = _worksOn
    /** The phone checks where it is against his places (Places). */
    val places: StateFlow<Boolean> = _places

    fun init(context: Context) {
        if (::store.isInitialized) return
        store = context.getSharedPreferences("natro", Context.MODE_PRIVATE)
        _power.value = store.getBoolean(POWER, true)
        _wakeWord.value = store.getBoolean(WAKE_WORD, false)
        _speakReplies.value = store.getBoolean(SPEAK, true)
        _showReplies.value = store.getBoolean(SHOW, true)
        _keepListening.value = store.getBoolean(KEEP_LISTENING, true)
        _messagesToPc.value = store.getBoolean(MESSAGES_TO_PC, true)
        _worksOn.value = WorksOn.named(store.getString(WORKS_ON, null)) ?: WorksOn.PHONE
        _places.value = store.getBoolean(PLACES, true)
        _parked.value = if (store.contains(PARKED_Y)) Parked(store.getBoolean(PARKED_RIGHT, true), store.getFloat(PARKED_Y, 0.5f)) else null
    }

    fun setPower(on: Boolean) {
        _power.value = on
        store.edit { putBoolean(POWER, on) }
    }

    fun setWakeWord(on: Boolean) {
        _wakeWord.value = on
        store.edit { putBoolean(WAKE_WORD, on) }
    }

    fun setSpeakReplies(on: Boolean) {
        _speakReplies.value = on
        store.edit { putBoolean(SPEAK, on) }
    }

    fun setShowReplies(on: Boolean) {
        _showReplies.value = on
        store.edit { putBoolean(SHOW, on) }
    }

    fun setKeepListening(on: Boolean) {
        _keepListening.value = on
        store.edit { putBoolean(KEEP_LISTENING, on) }
    }

    fun setMessagesToPc(on: Boolean) {
        _messagesToPc.value = on
        store.edit { putBoolean(MESSAGES_TO_PC, on) }
    }

    fun setWorksOn(device: String) {
        _worksOn.value = device
        store.edit { putString(WORKS_ON, device) }
    }

    fun setPlaces(on: Boolean) {
        _places.value = on
        store.edit { putBoolean(PLACES, on) }
    }

    fun setParked(place: Parked?) {
        _parked.value = place
        store.edit {
            if (place == null) remove(PARKED_Y).remove(PARKED_RIGHT)
            else putFloat(PARKED_Y, place.y).putBoolean(PARKED_RIGHT, place.right)
        }
    }

    private const val POWER = "power"
    private const val WAKE_WORD = "listen-for-wake-word"  // the old app's key, so the setting carries over
    private const val SPEAK = "speak-replies"
    private const val SHOW = "show-replies"
    private const val KEEP_LISTENING = "keep-listening"
    private const val PARKED_Y = "bubble-y"
    private const val PARKED_RIGHT = "bubble-right"
    private const val MESSAGES_TO_PC = "messages-to-pc"
    private const val WORKS_ON = "works-on"
    private const val PLACES = "places"
}
