package com.myoffice.app.library

/**
 * Filtering and ordering for the on-device document library.
 *
 * This file is deliberately free of Android imports so the behaviour that users
 * actually notice — which files a chip shows, and in what order — is covered by
 * plain JUnit tests instead of needing a device.
 */

/** One document found anywhere on the phone. */
data class DeviceFile(
    val path: String,
    val name: String,
    val ext: String,
    val kind: String,          // writer | sheet | slide | pdf | photos | vault | file
    val size: Long,
    val modified: Long,
    val folder: String,        // human-readable parent folder, e.g. "Download/Receipts"
    val lastUsed: Long = 0L,   // epoch millis of the last time it was opened in MyOffice
)

object LibrarySorting {

    /** Sort keys accepted from the UI; anything else falls back to [NEWEST]. */
    const val NEWEST = "newest"
    const val OLDEST = "oldest"
    const val NAME_ASC = "name_asc"
    const val NAME_DESC = "name_desc"
    const val LARGEST = "largest"
    const val SMALLEST = "smallest"
    const val TYPE = "type"
    const val LAST_USED = "last_used"

    val ALL = listOf(NEWEST, OLDEST, NAME_ASC, NAME_DESC, LARGEST, SMALLEST, TYPE, LAST_USED)

    fun normalise(key: String?): String {
        val k = (key ?: "").trim().lowercase()
        return if (k in ALL) k else NEWEST
    }

    /**
     * Order [files] by [key].  [lastUsed] maps a path to the epoch millis of its
     * last opening; files that were never opened sort after the ones that were.
     */
    fun sort(files: List<DeviceFile>, key: String?, lastUsed: Map<String, Long> = emptyMap()): List<DeviceFile> {
        return when (normalise(key)) {
            OLDEST -> files.sortedWith(compareBy<DeviceFile> { it.modified }.thenBy { it.name.lowercase() })
            NAME_ASC -> files.sortedWith(compareBy(String.CASE_INSENSITIVE_ORDER) { it.name })
            NAME_DESC -> files.sortedWith(compareByDescending(String.CASE_INSENSITIVE_ORDER) { it.name })
            LARGEST -> files.sortedWith(compareByDescending<DeviceFile> { it.size }.thenBy { it.name.lowercase() })
            SMALLEST -> files.sortedWith(compareBy<DeviceFile> { it.size }.thenBy { it.name.lowercase() })
            TYPE -> files.sortedWith(
                compareBy<DeviceFile> { kindRank(it.kind) }
                    .thenByDescending { it.modified }
                    .thenBy { it.name.lowercase() }
            )
            LAST_USED -> files.sortedWith(
                compareByDescending<DeviceFile> { lastUsed[it.path] ?: 0L }
                    .thenByDescending { it.modified }
                    .thenBy { it.name.lowercase() }
            )
            else -> files.sortedWith(compareByDescending<DeviceFile> { it.modified }.thenBy { it.name.lowercase() })
        }
    }

    /** Filter chips use the same vocabulary the workspace list already uses. */
    fun matches(file: DeviceFile, filter: String?): Boolean {
        val f = (filter ?: "all").lowercase()
        return when (f) {
            "all", "" -> true
            "recent" -> true                       // recency is the caller's business (it slices)
            "pdf", "writer", "sheet", "slide", "photos", "vault" -> file.kind == f
            "file" -> file.kind == "file"
            else -> true
        }
    }

    fun apply(files: List<DeviceFile>, filter: String?, query: String?): List<DeviceFile> {
        val q = (query ?: "").trim().lowercase()
        return files.asSequence()
            .filter { matches(it, filter) }
            .filter { q.isEmpty() || it.name.lowercase().contains(q) || it.folder.lowercase().contains(q) }
            .toList()
    }

    private fun kindRank(kind: String): Int = when (kind) {
        "pdf" -> 0
        "writer" -> 1
        "sheet" -> 2
        "slide" -> 3
        "photos" -> 4
        "vault" -> 5
        else -> 6
    }
}

/**
 * Folders the library walks past. `Android/data` and `Android/obb` hold other apps'
 * private trees and are unreadable anyway, but `Android/media` must stay reachable —
 * that is where WhatsApp keeps the documents people actually open.
 */
object LibraryPaths {

    private val SKIP_LEAVES = setOf(
        "lost.dir", ".thumbnails", ".trashes", ".trash", "cache", "temp", "tmp", "\$recycle.bin"
    )

    fun isSkippedDir(relPath: String): Boolean {
        val p = relPath.replace('\\', '/').trim('/').lowercase()
        if (p.isEmpty()) return false
        if (p == "android/data" || p.startsWith("android/data/")) return true
        if (p == "android/obb" || p.startsWith("android/obb/")) return true
        return p.substringAfterLast('/') in SKIP_LEAVES
    }
}

/** Extension → the same document families the editors use. */
object DocumentKinds {

    val WRITER = setOf("docx", "doc", "rtf", "odt", "txt", "md", "html", "htm")
    val SHEET = setOf("xlsx", "xls", "xlsm", "csv", "tsv", "ods")
    val SLIDE = setOf("pptx", "ppt", "ppsx", "pptm", "odp")
    val PDF = setOf("pdf")
    val PHOTOS = setOf("png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff", "heic", "heif")
    val VAULT = setOf("movault", "movaultkey")

    /** Everything the library shows.  Anything else on the phone is ignored. */
    val ALL: Set<String> = WRITER + SHEET + SLIDE + PDF + PHOTOS + VAULT

    fun kindOf(ext: String): String {
        val e = ext.lowercase()
        return when {
            e in WRITER -> "writer"
            e in SHEET -> "sheet"
            e in SLIDE -> "slide"
            e in PDF -> "pdf"
            e in PHOTOS -> "photos"
            e in VAULT -> "vault"
            else -> "file"
        }
    }

    fun isSupported(name: String): Boolean {
        val e = name.substringAfterLast('.', "").lowercase()
        return e in ALL
    }
}
