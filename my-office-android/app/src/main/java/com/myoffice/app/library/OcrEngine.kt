package com.myoffice.app.library

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import com.tom_roush.pdfbox.pdmodel.PDDocument
import com.tom_roush.pdfbox.rendering.PDFRenderer
import com.googlecode.tesseract.android.TessBaseAPI
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Turns pages into words: what makes a photographed receipt or a scanned PDF searchable.
 *
 * The engine is Tesseract 5 (via tesseract4android, Apache-2.0) with Leptonica, running on the
 * device. English and Urdu traineddata are bundled in `assets/tessdata`, so recognition works
 * with no network and no Play Services. The engine is initialised lazily and reused: creating a
 * TessBaseAPI costs a few hundred milliseconds, doing it per document would waste them.
 */
object OcrEngineHolder {

    @Volatile
    private var engine: LibraryIndexer.OcrEngine? = null

    @Volatile
    private var unusable = false

    /** The recogniser shipped with this build, or null when this device cannot run it. */
    fun engine(context: Context): LibraryIndexer.OcrEngine? {
        if (unusable) return null
        engine?.let { return it }
        return synchronized(this) {
            engine ?: runCatching { LocalOcrEngine(context.applicationContext) }
                .onFailure { unusable = true }
                .getOrNull()
                ?.also { engine = it }
        }
    }

    fun isAvailable(context: Context): Boolean {
        if (unusable) return false
        return runCatching { OcrEngineHolder.engine(context) != null }.getOrDefault(false)
    }

    /** Free the native handle when the app is done with it. */
    fun release() {
        (engine as? LocalOcrEngine)?.close()
        engine = null
    }
}

/**
 * Renders pages at ~200 dpi, downscales to a size Tesseract is comfortable with, and reads
 * them one at a time. Only the first pages of a document are read: that is enough to make it
 * findable, and it keeps a 300-page scan from running for minutes in the background.
 */
internal class LocalOcrEngine(private val context: Context) : LibraryIndexer.OcrEngine {

    private companion object {
        const val MAX_PAGES = 3
        const val TARGET_LONG_EDGE = 1600
        const val BUDGET_MS = 25_000L
        const val ASSETS_DIR = "tessdata"
        const val LANGUAGES = "eng+urd"
    }

    private val lock = Any()
    private val closed = AtomicBoolean(false)
    private var tess: TessBaseAPI? = null
    private var initialised = false

    private fun api(): TessBaseAPI? {
        synchronized(lock) {
            if (closed.get()) return null
            if (initialised) return tess
            initialised = true
            // The engine unpacks assets/tessdata into the app's files directory on first init.
            val dataPath = File(context.filesDir, "tess").apply { mkdirs() }.absolutePath
            tess = runCatching {
                copyTrainedData(dataPath)
                TessBaseAPI().apply {
                    init(dataPath, LANGUAGES)
                    setPageSegMode(TessBaseAPI.PageSegMode.PSM_AUTO)
                    setVariable("preserve_interword_spaces", "1")
                }
            }.getOrNull()
            return tess
        }
    }

    override fun recognise(file: File): String {
        val api = api() ?: return ""
        val ext = file.extension.lowercase()
        return if (ext == "pdf") recognisePdf(file, api) else recogniseImage(file, api)
    }

    private fun recognisePdf(file: File, api: TessBaseAPI): String {
        if (file.length() > OfficeTextExtractor.MAX_SOURCE_BYTES) return ""
        PDDocument.load(file).use { doc ->
            if (doc.numberOfPages <= 0) return ""
            val renderer = PDFRenderer(doc)
            val out = StringBuilder()
            val deadline = System.currentTimeMillis() + BUDGET_MS
            for (page in 0 until minOf(doc.numberOfPages, MAX_PAGES)) {
                if (System.currentTimeMillis() > deadline) break
                val bitmap = runCatching { renderer.renderImage(page, 2.2f) }.getOrNull() ?: continue
                out.append(readBitmap(bitmap, api)).append('\n')
                bitmap.recycle()
            }
            return TextClean.cap(TextClean.normalise(out.toString()), OfficeTextExtractor.MAX_TEXT_CHARS)
        }
    }

    private fun recogniseImage(file: File, api: TessBaseAPI): String {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.absolutePath, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return ""

        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / sample > TARGET_LONG_EDGE * 2) sample *= 2
        val bitmap = BitmapFactory.decodeFile(
            file.absolutePath,
            BitmapFactory.Options().apply { inSampleSize = sample }
        ) ?: return ""

        val scaled = scaleToFit(bitmap, TARGET_LONG_EDGE)
        if (scaled !== bitmap) bitmap.recycle()
        val text = readBitmap(scaled, api)
        scaled.recycle()
        return TextClean.cap(TextClean.normalise(text), OfficeTextExtractor.MAX_TEXT_CHARS)
    }

    /** One recognition pass. Guarded by [lock]: the native handle is not re-entrant. */
    private fun readBitmap(bitmap: Bitmap, api: TessBaseAPI): String {
        synchronized(lock) {
            if (closed.get()) return ""
            return runCatching {
                api.setImage(bitmap)
                api.getUTF8Text() ?: ""
            }.getOrDefault("")
        }
    }

    /**
     * Tesseract reads `<dataPath>/tessdata/<lang>.traineddata`. Copy the bundled files once;
     * a version bump of the asset re-copies them (the size check is enough for our own files).
     */
    private fun copyTrainedData(dataPath: String) {
        val tessRoot = File(dataPath, "tessdata").apply { mkdirs() }
        val assets = context.assets.list("tessdata").orEmpty()
        for (name in assets) {
            val target = File(tessRoot, name)
            val expected = runCatching { context.assets.open("tessdata/$name").use { it.available() } }.getOrDefault(0)
            if (target.isFile && target.length() == expected.toLong() && expected > 0) continue
            runCatching {
                context.assets.open("tessdata/$name").use { input ->
                    target.outputStream().use { out -> input.copyTo(out) }
                }
            }
        }
    }

    private fun scaleToFit(source: Bitmap, target: Int): Bitmap {
        val longest = maxOf(source.width, source.height)
        if (longest <= target) return source
        val ratio = target.toFloat() / longest
        val w = (source.width * ratio).toInt().coerceAtLeast(1)
        val h = (source.height * ratio).toInt().coerceAtLeast(1)
        return Bitmap.createScaledBitmap(source, w, h, true)
    }

    fun close() {
        synchronized(lock) {
            if (closed.getAndSet(true)) return
            runCatching { tess?.recycle() }
            tess = null
        }
    }
}
