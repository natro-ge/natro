package dev.natro

import android.content.ContentResolver
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ImageDecoder
import android.net.Uri
import java.io.ByteArrayOutputStream
import kotlin.math.max
import kotlin.math.roundToInt

/** Pictures in the chat: one he picks, made small enough to send quickly, and the chat's, decoded to show. */
object Pictures {
    /** The longest side of a picture he sends. */
    private const val MOST_PIXELS = 2048
    private const val QUALITY = 85

    /** A picture he picked, as JPEG bytes at most MOST_PIXELS a side, the right way up (as the camera held it). */
    fun forChat(resolver: ContentResolver, uri: Uri): ByteArray {
        val picture = ImageDecoder.decodeBitmap(ImageDecoder.createSource(resolver, uri)) { decoder, info, _ ->
            val longest = max(info.size.width, info.size.height)
            if (longest > MOST_PIXELS) {
                val scale = MOST_PIXELS.toFloat() / longest
                decoder.setTargetSize((info.size.width * scale).roundToInt(), (info.size.height * scale).roundToInt())
            }
            decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
        }
        // A transparent picture on white, not black (JPEG has no transparency).
        val flat = Bitmap.createBitmap(picture.width, picture.height, Bitmap.Config.ARGB_8888)
        Canvas(flat).apply {
            drawColor(Color.WHITE)
            drawBitmap(picture, 0f, 0f, null)
        }
        return ByteArrayOutputStream().also { flat.compress(Bitmap.CompressFormat.JPEG, QUALITY, it) }.toByteArray()
    }

    /** The chat's picture, decoded no wider than about `widest` pixels (null if it isn't one). */
    fun decode(bytes: ByteArray, widest: Int): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        var sample = 1
        while (bounds.outWidth / (sample * 2) >= widest) sample *= 2
        return BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
    }
}
