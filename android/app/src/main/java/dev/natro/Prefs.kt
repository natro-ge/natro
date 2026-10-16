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
    private val _parked = MutableStateFlow<Parked?>(null)

    /** Natro on at all. Off: no connection, no listening, no bubble, no notification. */
    val power: StateFlow<Boolean> = _power
    /** Listening for "OK Natro". */
    val wakeWord: StateFlow<Boolean> = _wakeWord
    val speakReplies: StateFlow<Boolean> = _speakReplies
    /** Her replies open as text from the bubble (the reply card). */
    val showReplies: StateFlow<Boolean> = _showReplies
    /** The bubble's place on an edge, or null: then it shows only while Natro is busy. */
    val parked: StateFlow<Parked?> = _parked

    fun init(context: Context) {
        if (::store.isInitialized) return
        store = context.getSharedPreferences("natro", Context.MODE_PRIVATE)
        _power.value = store.getBoolean(POWER, true)
        _wakeWord.value = store.getBoolean(WAKE_WORD, false)
        _speakReplies.value = store.getBoolean(SPEAK, true)
        _showReplies.value = store.getBoolean(SHOW, true)
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
    private const val PARKED_Y = "bubble-y"
    private const val PARKED_RIGHT = "bubble-right"
}
