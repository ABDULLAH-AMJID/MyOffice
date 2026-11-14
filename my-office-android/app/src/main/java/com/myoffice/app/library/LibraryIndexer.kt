package com.myoffice.app.library

import android.content.Context
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Owns the background work behind the library: scanning storage and filling the text index.
 *
 * One low-priority thread does both. The UI never waits for it — the file list is served from
 * SQLite straight away and the index catches up while the user reads.
 */
object LibraryIndexer {

    private const val PREFS = "myoffice-library"
    private const val KEY_SCANNED_AT = "scanned_at"
    private const val TTL_MS = 10 * 60 * 1000L
    private const val BATCH = 24

    private val worker = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "myoffice-library").apply { priority = Thread.MIN_PRIORITY }
    }

    private val scanQueued = AtomicBoolean(false)

    @Volatile
    private var indexing = false

    fun isIndexing(): Boolean = indexing

    /** What the chip shows while the index is being built. */
    data class Progress(
        val indexed: Int,   // documents whose text is searchable
        val total: Int,     // documents in the library
        val pending: Int,   // still queued (text or OCR)
        val skipped: Int,   // nothing to read (images without OCR engine, vaults, oversized)
        val failed: Int,    // tried twice, gave up
    ) {
        val processed: Int get() = (total - pending).coerceIn(0, total)
    }

    private fun progressOf(index: LibraryIndex): Progress {
        val c = index.counts()
        return Progress(
            indexed = c.indexed.toInt(),
            total = c.total.toInt(),
            pending = c.pending.toInt(),
            skipped = c.skipped.toInt(),
            failed = c.failed.toInt(),
        )
    }

    fun scannedAt(context: Context): Long =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getLong(KEY_SCANNED_AT, 0L)

    private fun markScanned(context: Context, at: Long) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putLong(KEY_SCANNED_AT, at).apply()
    }

    /** Called by the bridge after it scanned synchronously for the first paint. */
    fun markScannedNow(context: Context) = markScanned(context, System.currentTimeMillis())

    fun index(context: Context): LibraryIndex = LibraryIndex(context.applicationContext)

    /**
     * Run a scan in the background when the cached one is older than [TTL_MS] (or when
     * [force]). [onDone] receives the file list from the index once the scan settles.
     */
    fun ensureScanned(
        context: Context,
        workspaceDir: File?,
        force: Boolean,
        onDone: (List<DeviceFile>) -> Unit,
    ) {
        val stale = System.currentTimeMillis() - scannedAt(context) > TTL_MS
        if (!force && !stale) {
            onDone(index(context).allFiles())
            return
        }
        if (!scanQueued.compareAndSet(false, true)) {
            // a scan is already on its way; hand back what we have and let it finish
            onDone(index(context).allFiles())
            return
        }
        worker.execute {
            try {
                val files = DeviceLibrary.scan(context, workspaceDir)
                val generation = System.currentTimeMillis()
                LibraryIndex(context).replaceAll(files, generation)
                markScanned(context, generation)
                onDone(files)
            } catch (t: Throwable) {
                onDone(runCatching { LibraryIndex(context).allFiles() }.getOrDefault(emptyList()))
            } finally {
                scanQueued.set(false)
            }
        }
    }

    /**
     * Extract text for as many pending documents as fit in [maxDocs]. Returns how many were
     * processed. Safe to call repeatedly; it is a no-op while another pass is running.
     */
    fun indexPending(
        context: Context,
        maxDocs: Int = 400,
        onProgress: ((Progress) -> Unit)? = null,
    ): Int {
        if (!indexing) indexing = true
        var processed = 0
        try {
            val index = LibraryIndex(context)
            val attempted = HashSet<String>()
            while (processed < maxDocs) {
                val batch = index.nextPending(BATCH).filter { attempted.add(it.path) }
                if (batch.isEmpty()) break
                for (doc in batch) {
                    if (processed >= maxDocs) break
                    try {
                        val extraction = DocumentText.extractDetailed(File(doc.path))
                        if (extraction.text.isBlank() && !extraction.needsOcr) {
                            index.markSkipped(doc.path, doc.modified)
                        } else {
                            index.storeText(doc.path, doc.modified, extraction.text, extraction.needsOcr)
                        }
                    } catch (t: Throwable) {
                        // A single unreadable file used to abort the whole run, which is why the
                        // counter always froze on the same document. Record and move on.
                        index.recordFailure(doc.path, doc.modified, t.message ?: t.javaClass.simpleName)
                    }
                    processed++
                    if (processed % 5 == 0) onProgress?.invoke(progressOf(index))
                }
            }
            onProgress?.invoke(progressOf(index))
        } catch (t: Throwable) {
            // indexing is best-effort; a single unreadable volume must not crash the app
        } finally {
            indexing = false
        }
        return processed
    }

    /** Anything that can turn a page image into words. Implemented by the OCR engine. */
    interface OcrEngine {
        /** Text for [file], or "" when nothing could be read. */
        fun recognise(file: File): String
    }

    /**
     * OCR the documents that carry text only as pixels (scans, photographed receipts).
     * Deliberately slower than the text pass — it decodes and reads images — so it runs
     * with a smaller batch and yields back to the text index between documents.
     */
    fun indexOcr(
        context: Context,
        engine: OcrEngine,
        maxDocs: Int = 12,
        budgetMs: Long = 90_000L,
        onProgress: ((Progress) -> Unit)? = null,
    ): Int {
        if (indexing) return 0
        indexing = true
        var processed = 0
        val deadline = System.currentTimeMillis() + budgetMs
        try {
            val index = LibraryIndex(context)
            val batch = index.nextNeedsOcr(maxDocs)
            for (doc in batch) {
                if (System.currentTimeMillis() > deadline) break   // next pass picks up here
                try {
                    val text = engine.recognise(File(doc.path))
                    if (text.isBlank()) index.markSkipped(doc.path, doc.modified)
                    else index.storeText(doc.path, doc.modified, text, needsOcr = false)
                } catch (t: Throwable) {
                    index.recordFailure(doc.path, doc.modified, t.message ?: t.javaClass.simpleName)
                }
                processed++
                // one update per document: a single scan can take seconds, silence looks like a hang
                onProgress?.invoke(progressOf(index))
            }
        } catch (_: Throwable) {
            // OCR is best-effort; the text index stays usable either way
        } finally {
            indexing = false
        }
        return processed
    }

    /** Fire-and-forget pass on the library thread (used after a rescan or at startup). */
    fun kickIndexing(
        context: Context,
        onProgress: ((Progress) -> Unit)? = null,
        ocr: OcrEngine? = null,
        budgetMs: Long = 120_000L,
    ) {
        if (indexing) return
        worker.execute {
            val deadline = System.currentTimeMillis() + budgetMs
            // Text first, repeatedly: a library of thousands must not need thousands of app starts.
            while (System.currentTimeMillis() < deadline) {
                val done = indexPending(context, maxDocs = 200, onProgress = onProgress)
                val stillPending = LibraryIndex(context).counts().pending
                if (done == 0 || stillPending == 0L) break
            }
            // Then the scans, one bounded pass at a time.
            if (ocr != null) {
                while (System.currentTimeMillis() < deadline) {
                    val left = LibraryIndex(context).countsOcr()
                    if (left == 0L) break
                    val done = indexOcr(context, ocr, onProgress = onProgress)
                    if (done == 0) break
                }
            }
            onProgress?.invoke(progressOf(LibraryIndex(context)))
        }
    }

    fun resetTextIndex(context: Context) = LibraryIndex(context).resetTextIndex()
}
