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
        onProgress: ((indexed: Int, total: Int, pending: Int) -> Unit)? = null,
    ): Int {
        if (!indexing) indexing = true
        var processed = 0
        try {
            val index = LibraryIndex(context)
            while (processed < maxDocs) {
                val batch = index.nextPending(BATCH)
                if (batch.isEmpty()) break
                for (doc in batch) {
                    if (processed >= maxDocs) break
                    val text = runCatching { DocumentText.extract(File(doc.path)) }.getOrElse { "" }
                    if (text.isBlank()) index.markSkipped(doc.path, doc.modified)
                    else index.storeText(doc.path, doc.modified, text)
                    processed++
                    if (processed % 5 == 0) {
                        val counts = index.counts()
                        onProgress?.invoke(counts.indexed.toInt(), counts.total.toInt(), counts.pending.toInt())
                    }
                }
            }
            val counts = index.counts()
            onProgress?.invoke(counts.indexed.toInt(), counts.total.toInt(), counts.pending.toInt())
        } catch (t: Throwable) {
            // indexing is best-effort; a single unreadable volume must not crash the app
        } finally {
            indexing = false
        }
        return processed
    }

    /** Fire-and-forget pass on the library thread (used after a rescan or at startup). */
    fun kickIndexing(
        context: Context,
        onProgress: ((indexed: Int, total: Int, pending: Int) -> Unit)? = null,
    ) {
        if (indexing) return
        worker.execute { indexPending(context, onProgress = onProgress) }
    }

    fun resetTextIndex(context: Context) = LibraryIndex(context).resetTextIndex()
}
