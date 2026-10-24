package com.myoffice.app.qr

import android.content.ContentResolver
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import kotlin.math.max

/**
 * Loads a picture the user picked (gallery, Files, WhatsApp, screenshot) and hands it to
 * [QrImageDecoder].  The bitmap is decoded with a `inSampleSize` that keeps the long edge around
 * 2048 px, which is plenty for a QR code and keeps a 12 MP photo from allocating ~48 MB of pixels
 * on a mid-range phone.
 */
object QrGallery {

    private const val MAX_LOAD_EDGE = 2048

    fun decode(resolver: ContentResolver, uri: Uri): String? {
        val bitmap = loadBitmap(resolver, uri) ?: return null
        return try {
            val width = bitmap.width
            val height = bitmap.height
            if (width <= 0 || height <= 0) return null
            val pixels = IntArray(width * height)
            bitmap.getPixels(pixels, 0, width, 0, 0, width, height)
            QrImageDecoder.decode(pixels, width, height)
        } catch (_: Throwable) {
            null
        } finally {
            bitmap.recycle()
        }
    }

    private fun loadBitmap(resolver: ContentResolver, uri: Uri): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        runCatching {
            resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
        }
        var sample = 1
        val longEdge = max(bounds.outWidth, bounds.outHeight)
        while (longEdge > 0 && longEdge / (sample * 2) >= MAX_LOAD_EDGE) sample *= 2
        val options = BitmapFactory.Options().apply {
            inSampleSize = sample
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        return runCatching {
            resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, options) }
        }.getOrNull()
    }
}
