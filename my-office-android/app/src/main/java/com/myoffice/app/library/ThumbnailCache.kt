package com.myoffice.app.library

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Base64
import android.util.LruCache
import com.tom_roush.pdfbox.pdmodel.PDDocument
import com.tom_roush.pdfbox.rendering.PDFRenderer
import java.io.ByteArrayOutputStream
import java.io.File
import java.security.MessageDigest

/**
 * Small previews for the Home screen: the first page of a PDF, the picture itself for photos.
 *
 * Two layers, because a list re-renders constantly while scrolling:
 *  - an in-memory LRU for the bitmaps that were just drawn,
 *  - JPEG files in `cacheDir/thumbs`, keyed by path + size + modified time, so a rotated
 *    screen or a restarted app does not re-render anything.
 *
 * Apple/HEIC decoding is left to the platform; if it cannot decode, the row simply keeps
 * its file-type badge — a missing thumbnail must never break the list.
 */
object ThumbnailCache {

    private const val MAX_DISK_BYTES = 48L * 1024 * 1024
    private const val MAX_DISK_FILES = 1200
    private const val JPEG_QUALITY = 72

    private val memory = object : LruCache<String, Bitmap>(6 * 1024 * 1024) {
        override fun sizeOf(key: String, value: Bitmap): Int = value.byteCount
    }

    private fun keyOf(file: File, px: Int): String {
        val digest = MessageDigest.getInstance("SHA-256")
            .digest("${file.absolutePath}|${file.length()}|${file.lastModified()}|$px".toByteArray())
        return digest.joinToString("") { "%02x".format(it) }.take(24)
    }

    private fun dir(context: Context) = File(context.cacheDir, "thumbs").apply { mkdirs() }

    /** Data URI for the WebView, or null when this file has no preview worth drawing. */
    fun dataUri(context: Context, file: File, px: Int = 240): String? {
        if (!file.isFile) return null
        if (file.extension.lowercase() !in PREVIEWABLE) return null
        val bytes = jpegBytes(context, file, px) ?: return null
        return "data:image/jpeg;base64," + Base64.encodeToString(bytes, Base64.NO_WRAP)
    }

    fun jpegBytes(context: Context, file: File, px: Int = 240): ByteArray? {
        val key = keyOf(file, px)
        val cached = File(dir(context), "$key.jpg")
        if (cached.isFile && cached.length() > 0) {
            cached.setLastModified(System.currentTimeMillis())   // LRU marker
            return runCatching { cached.readBytes() }.getOrNull()
        }
        val bitmap = runCatching { render(file, px) }.getOrNull() ?: return null
        val out = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)
        if (bitmap !== memory.get(key)) bitmap.recycle()
        val bytes = out.toByteArray()
        runCatching { cached.writeBytes(bytes) }
        prune(context)
        return bytes
    }

    private fun render(file: File, px: Int): Bitmap? {
        return when (file.extension.lowercase()) {
            "pdf" -> renderPdf(file, px)
            else -> renderImage(file, px)
        }
    }

    private fun renderImage(file: File, px: Int): Bitmap? {
        // pass 1: measure only, so a 12 MP photo never lands in memory at full size
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.absolutePath, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null

        var sample = 1
        while (bounds.outWidth / sample > px * 2 && bounds.outHeight / sample > px * 2) sample *= 2
        val opts = BitmapFactory.Options().apply {
            inSampleSize = sample
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        val decoded = BitmapFactory.decodeFile(file.absolutePath, opts) ?: return null
        val scaled = scaleToFit(decoded, px)
        if (scaled !== decoded) decoded.recycle()
        return scaled
    }

    private fun renderPdf(file: File, px: Int): Bitmap? {
        PDDocument.load(file).use { doc ->
            if (doc.numberOfPages <= 0) return null
            val renderer = PDFRenderer(doc)
            val page = renderer.renderImage(0)      // scale 1 = 72 dpi
            val scaled = scaleToFit(page, px)
            if (scaled !== page) page.recycle()
            return scaled
        }
    }

    private fun scaleToFit(source: Bitmap, px: Int): Bitmap {
        val longest = maxOf(source.width, source.height)
        if (longest <= px) return source
        val ratio = px.toFloat() / longest
        val w = (source.width * ratio).toInt().coerceAtLeast(1)
        val h = (source.height * ratio).toInt().coerceAtLeast(1)
        return Bitmap.createScaledBitmap(source, w, h, true)
    }

    /** Keep the cache folder bounded; oldest files go first. */
    private fun prune(context: Context) {
        val folder = dir(context)
        val files = folder.listFiles() ?: return
        if (files.size <= MAX_DISK_FILES && files.sumOf { it.length() } <= MAX_DISK_BYTES) return
        files.sortedBy { it.lastModified() }
            .dropLast(MAX_DISK_FILES / 2)
            .forEach { runCatching { it.delete() } }
    }

    fun clear(context: Context) {
        dir(context).listFiles()?.forEach { runCatching { it.delete() } }
        memory.evictAll()
    }

    private val PREVIEWABLE = setOf("pdf", "png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff", "heic", "heif")
}
