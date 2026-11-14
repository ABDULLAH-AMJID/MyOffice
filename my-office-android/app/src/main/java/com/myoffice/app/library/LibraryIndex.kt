package com.myoffice.app.library

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import java.io.File

/**
 * SQLite index behind the Home screen.
 *
 * Two jobs:
 *  1. **List** — the scanned files live here, so `listDeviceFiles` reads a table instead of
 *     re-walking storage every time the app starts.
 *  2. **Find** — document text goes into an FTS4 table, which is what makes "search inside my
 *     files" possible at all.
 *
 * Text extraction never happens on the UI thread; [nextPending] hands out work and the
 * caller writes the result back with [storeText].
 */
class LibraryIndex(context: Context) : SQLiteOpenHelper(context, DB_NAME, null, DB_VERSION) {

    companion object {
        private const val DB_NAME = "library-index.db"
        private const val DB_VERSION = 2

        const val TEXT_PENDING = 0
        const val TEXT_DONE = 1
        const val TEXT_SKIPPED = 2
        const val TEXT_NEEDS_OCR = 3
    }

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE files(
                path TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                ext TEXT NOT NULL,
                kind TEXT NOT NULL,
                size INTEGER NOT NULL,
                modified INTEGER NOT NULL,
                folder TEXT NOT NULL,
                generation INTEGER NOT NULL DEFAULT 0,
                text_state INTEGER NOT NULL DEFAULT 0,
                text_modified INTEGER NOT NULL DEFAULT 0,
                last_used INTEGER NOT NULL DEFAULT 0
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX idx_files_name ON files(name)")
        db.execSQL("CREATE INDEX idx_files_pending ON files(text_state, modified)")
        db.execSQL("CREATE INDEX idx_files_recent ON files(last_used DESC)")
        createFts(db)
    }

    private fun createFts(db: SQLiteDatabase) {
        // unicode61 handles Urdu/Arabic and diacritics properly; very old SQLite builds fall back.
        val created = runCatching {
            db.execSQL("CREATE VIRTUAL TABLE doc_fts USING fts4(body, path, tokenize=unicode61)")
        }.isSuccess
        if (!created) {
            runCatching { db.execSQL("CREATE VIRTUAL TABLE doc_fts USING fts4(body, path)") }
        }
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        // The index is a cache: rebuilding is always cheaper than migrating it.
        db.execSQL("DROP TABLE IF EXISTS doc_fts")
        db.execSQL("DROP TABLE IF EXISTS files")
        onCreate(db)
    }

    // ------------------------------------------------------------------ listing

    /** Replace the file table with a fresh scan; rows that disappeared are dropped. */
    fun replaceAll(files: List<DeviceFile>, generation: Long) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            for (file in files) {
                val previous = previousVersion(db, file.path)
                val changed = previous == null || previous != file.modified
                val values = ContentValues().apply {
                    put("path", file.path)
                    put("name", file.name)
                    put("ext", file.ext)
                    put("kind", file.kind)
                    put("size", file.size)
                    put("modified", file.modified)
                    put("folder", file.folder)
                    put("generation", generation)
                    // "Continue where you left off" must survive every rescan
                    put("last_used", if (previous == null) file.lastUsed else lastUsedOf(db, file.path))
                    if (previous == null) put("text_state", TEXT_PENDING)
                    else if (changed) put("text_state", TEXT_PENDING)
                }
                db.insertWithOnConflict("files", null, values, SQLiteDatabase.CONFLICT_REPLACE)
            }
            db.delete("files", "generation != ?", arrayOf(generation.toString()))
            db.execSQL(
                "DELETE FROM doc_fts WHERE path NOT IN (SELECT path FROM files)"
            )
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    private fun lastUsedOf(db: SQLiteDatabase, path: String): Long {
        db.query("files", arrayOf("last_used"), "path = ?", arrayOf(path), null, null, null).use { c ->
            return if (c.moveToFirst()) c.getLong(0) else 0L
        }
    }

    /** Remember that a document was opened, for the Recent row and the "last used" sort. */
    fun touch(path: String, whenMs: Long = System.currentTimeMillis()): Boolean {
        val values = ContentValues().apply { put("last_used", whenMs) }
        return writableDatabase.update("files", values, "path = ?", arrayOf(path)) > 0
    }

    /** The documents that were opened most recently. */
    fun recent(limit: Int): List<DeviceFile> {
        val out = ArrayList<DeviceFile>()
        readableDatabase.query(
            "files",
            arrayOf("path", "name", "ext", "kind", "size", "modified", "folder", "last_used"),
            "last_used > 0", null, null, null, "last_used DESC", limit.toString()
        ).use { c ->
            while (c.moveToNext()) {
                out += DeviceFile(
                    c.getString(0), c.getString(1), c.getString(2), c.getString(3),
                    c.getLong(4), c.getLong(5), c.getString(6), c.getLong(7),
                )
            }
        }
        return out
    }

    private fun previousVersion(db: SQLiteDatabase, path: String): Long? {
        db.query("files", arrayOf("modified"), "path = ?", arrayOf(path), null, null, null).use { c ->
            return if (c.moveToFirst()) c.getLong(0) else null
        }
    }

    fun allFiles(): List<DeviceFile> {
        val out = ArrayList<DeviceFile>()
        readableDatabase.query(
            "files", arrayOf("path", "name", "ext", "kind", "size", "modified", "folder", "last_used"),
            null, null, null, null, "modified DESC"
        ).use { c ->
            while (c.moveToNext()) {
                out += DeviceFile(
                    path = c.getString(0), name = c.getString(1), ext = c.getString(2),
                    kind = c.getString(3), size = c.getLong(4), modified = c.getLong(5),
                    folder = c.getString(6),
                    lastUsed = c.getLong(7),
                )
            }
        }
        return out
    }

    fun counts(): Counts {
        readableDatabase.rawQuery(
            "SELECT COUNT(*), " +
                "SUM(CASE WHEN text_state = $TEXT_DONE THEN 1 ELSE 0 END), " +
                "SUM(CASE WHEN text_state IN ($TEXT_PENDING, $TEXT_NEEDS_OCR) THEN 1 ELSE 0 END), " +
                "SUM(CASE WHEN text_state = $TEXT_NEEDS_OCR THEN 1 ELSE 0 END) FROM files",
            null
        ).use { c ->
            if (!c.moveToFirst()) return Counts(0, 0, 0, 0)
            return Counts(c.getLong(0), c.getLong(1), c.getLong(2), c.getLong(3))
        }
    }

    data class Counts(val total: Long, val indexed: Long, val pending: Long, val needsOcr: Long = 0)

    // ------------------------------------------------------------------ indexing queue

    data class PendingDoc(val path: String, val ext: String, val modified: Long, val size: Long)

    /**
     * Next documents whose text is missing or stale, newest first. Files that cannot carry
     * text (images, vaults, archives) are skipped once instead of being re-examined forever.
     */
    fun nextPending(limit: Int): List<PendingDoc> {
        val out = ArrayList<PendingDoc>()
        readableDatabase.query(
            "files",
            arrayOf("path", "ext", "modified", "size"),
            "text_state = ? AND size <= ?",
            arrayOf(TEXT_PENDING.toString(), OfficeTextExtractor.MAX_SOURCE_BYTES.toString()),
            null, null, "modified DESC", limit.toString()
        ).use { c ->
            while (c.moveToNext()) {
                out += PendingDoc(c.getString(0), c.getString(1), c.getLong(2), c.getLong(3))
            }
        }
        if (out.isEmpty()) retireUnindexable()
        return out
    }

    /**
     * Retire work that can never succeed — formats with no text layer, and files above the
     * extraction ceiling — so the queue actually empties instead of spinning forever.
     */
    private fun retireUnindexable() {
        // Scans and photos carry text as pixels: send them to the OCR queue rather than dropping them.
        val ocr = OfficeTextExtractor.OCR_CANDIDATES.toList()
        writableDatabase.execSQL(
            "UPDATE files SET text_state = $TEXT_NEEDS_OCR " +
                "WHERE text_state = ? AND ext IN (${ocr.joinToString(",") { "?" }})",
            arrayOf<Any>(TEXT_PENDING.toString(), *ocr.toTypedArray())
        )

        val all = OfficeTextExtractor.SUPPORTED + ocr
        writableDatabase.execSQL(
            "UPDATE files SET text_state = $TEXT_SKIPPED " +
                "WHERE text_state = ? AND ext NOT IN (${all.joinToString(",") { "?" }})",
            arrayOf<Any>(TEXT_PENDING.toString(), *all.toTypedArray())
        )
        val tooBig = arrayOf(TEXT_PENDING.toString(), OfficeTextExtractor.MAX_SOURCE_BYTES.toString())
        writableDatabase.execSQL(
            "UPDATE files SET text_state = $TEXT_SKIPPED WHERE text_state = ? AND size > ?",
            tooBig
        )
    }

    fun storeText(path: String, modified: Long, body: String, needsOcr: Boolean = false) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            if (body.isBlank() && needsOcr) {
                val ocr = ContentValues().apply {
                    put("text_state", TEXT_NEEDS_OCR)
                    put("text_modified", modified)
                }
                db.update("files", ocr, "path = ?", arrayOf(path))
            } else if (body.isBlank()) {
                val skip = ContentValues().apply {
                    put("text_state", TEXT_SKIPPED)
                    put("text_modified", modified)
                }
                db.update("files", skip, "path = ?", arrayOf(path))
            } else {
                db.execSQL("DELETE FROM doc_fts WHERE path = ?", arrayOf(path))
                db.execSQL(
                    "INSERT INTO doc_fts(body, path) VALUES (?, ?)",
                    arrayOf(body, path)
                )
                val done = ContentValues().apply {
                    put("text_state", TEXT_DONE)
                    put("text_modified", modified)
                }
                db.update("files", done, "path = ?", arrayOf(path))
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    /** Scanned PDFs and photos waiting for the OCR pass. */
    fun nextNeedsOcr(limit: Int): List<PendingDoc> {
        val out = ArrayList<PendingDoc>()
        readableDatabase.query(
            "files", arrayOf("path", "ext", "modified", "size"),
            "text_state = ? AND size <= ?",
            arrayOf(TEXT_NEEDS_OCR.toString(), OfficeTextExtractor.MAX_SOURCE_BYTES.toString()),
            null, null, "modified DESC", limit.toString()
        ).use { c ->
            while (c.moveToNext()) {
                out += PendingDoc(c.getString(0), c.getString(1), c.getLong(2), c.getLong(3))
            }
        }
        return out
    }

    fun countsOcr(): Long {
        readableDatabase.rawQuery("SELECT COUNT(*) FROM files WHERE text_state = $TEXT_NEEDS_OCR", null).use { c ->
            return if (c.moveToFirst()) c.getLong(0) else 0L
        }
    }

    fun markSkipped(path: String, modified: Long) {
        writableDatabase.update(
            "files",
            ContentValues().apply {
                put("text_state", TEXT_SKIPPED)
                put("text_modified", modified)
            },
            "path = ?", arrayOf(path)
        )
    }

    // ------------------------------------------------------------------ search

    /** Documents whose body contains every token of [tokens]. */
    fun searchContent(tokens: List<String>, limit: Int): List<ContentHit> {
        val expression = LibrarySearch.toMatchExpression(tokens)
        if (expression.isEmpty()) return emptyList()
        val out = ArrayList<ContentHit>()
        val sql =
            "SELECT f.path, f.name, f.ext, f.kind, f.size, f.modified, f.folder, d.body, f.last_used " +
                "FROM doc_fts d JOIN files f ON f.path = d.path WHERE doc_fts MATCH ? " +
                "ORDER BY f.modified DESC LIMIT ?"
        runCatching {
            readableDatabase.rawQuery(sql, arrayOf(expression, limit.toString())).use { c ->
                while (c.moveToNext()) {
                    out += ContentHit(
                        path = c.getString(0), name = c.getString(1), ext = c.getString(2),
                        kind = c.getString(3), size = c.getLong(4), modified = c.getLong(5),
                        folder = c.getString(6), body = c.getString(7) ?: "", lastUsed = c.getLong(8),
                    )
                }
            }
        }.onFailure {
            // A malformed MATCH expression must never crash search; an empty list is the answer.
            return emptyList()
        }
        return out
    }

    data class ContentHit(
        val path: String, val name: String, val ext: String, val kind: String,
        val size: Long, val modified: Long, val folder: String, val body: String, val lastUsed: Long = 0,
    )

    /** Files whose name or folder contains [query]. */
    fun searchNames(query: String, limit: Int): List<DeviceFile> {
        val out = ArrayList<DeviceFile>()
        val like = "%${query.replace("%", "\\%").replace("_", "\\_")}%"
        readableDatabase.query(
            "files", arrayOf("path", "name", "ext", "kind", "size", "modified", "folder", "last_used"),
            "name LIKE ? ESCAPE '\\' OR folder LIKE ? ESCAPE '\\'",
            arrayOf(like, like), null, null, "modified DESC", limit.toString()
        ).use { c ->
            while (c.moveToNext()) {
                out += DeviceFile(
                    c.getString(0), c.getString(1), c.getString(2), c.getString(3),
                    c.getLong(4), c.getLong(5), c.getString(6), c.getLong(7),
                )
            }
        }
        return out
    }

    /** Wipe the text index but keep the file list, so a rebuild can be triggered on demand. */
    fun resetTextIndex() {
        val db = writableDatabase
        db.beginTransaction()
        try {
            db.execSQL("DELETE FROM doc_fts")
            db.execSQL("UPDATE files SET text_state = $TEXT_PENDING, text_modified = 0")
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    fun fileCountForRoot(root: File): Int {
        readableDatabase.rawQuery(
            "SELECT COUNT(*) FROM files WHERE path LIKE ?",
            arrayOf("${root.absolutePath}%")
        ).use { c -> return if (c.moveToFirst()) c.getInt(0) else 0 }
    }
}
