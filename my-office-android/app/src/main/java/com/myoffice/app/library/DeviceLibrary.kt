package com.myoffice.app.library

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.provider.Settings
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Finds the documents that already live on the phone, so nobody has to import a
 * file through the system picker before they can open it.
 *
 * Two sources, in order of trust:
 *  1. **All-files access** (`MANAGE_EXTERNAL_STORAGE`, API 30+): a bounded walk of
 *     internal storage and any SD card, skipping `Android/data`, `Android/obb`,
 *     thumbnails and hidden folders.
 *  2. **MediaStore** (always tried): whatever the system has already indexed.  On
 *     Android 13+ this is mostly media, which still proves useful before access is
 *     granted instead of showing an empty screen.
 *
 * Results are cached in `filesDir/device-index.json` (metadata only) and reused for
 * [CACHE_TTL_MS], so opening the app does not re-walk the disk every time.
 */
object DeviceLibrary {

    private const val CACHE_FILE = "device-index.json"
    private const val CACHE_TTL_MS = 10 * 60 * 1000L
    private const val MAX_ENTRIES = 20_000
    private const val MAX_DEPTH = 12

    // ---------------------------------------------------------------- access

    /** True when MyOffice may read the whole shared storage. */
    fun hasAllFilesAccess(context: Context): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            Environment.isExternalStorageManager()
        } else {
            @Suppress("DEPRECATION")
            context.checkSelfPermission(android.Manifest.permission.READ_EXTERNAL_STORAGE) ==
                android.content.pm.PackageManager.PERMISSION_GRANTED
        }
    }

    /**
     * Settings screen where the user grants "All files access" (API 30+), else null.
     * The caller launches it; some OEM builds do not publish the per-app screen, so the
     * app-wide list is offered instead.
     */
    fun allFilesAccessIntent(context: Context): Intent? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return null
        val direct = Intent(
            Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
            Uri.parse("package:${context.packageName}")
        ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return if (direct.resolveActivity(context.packageManager) != null) {
            direct
        } else {
            Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
    }

    // ---------------------------------------------------------------- scanning

    fun roots(context: Context): List<File> {
        val out = LinkedHashSet<File>()
        @Suppress("DEPRECATION")
        val primary = Environment.getExternalStorageDirectory()
        if (primary != null && primary.isDirectory) out += primary
        // /storage/XXXX-XXXX/Android/data/<pkg>/files → walk up to the card root
        runCatching {
            context.getExternalFilesDirs(null).filterNotNull().forEach { dir ->
                var p: File? = dir
                repeat(4) { p = p?.parentFile }
                if (p != null && p.isDirectory) out += p
            }
        }
        return out.toList()
    }

    /** Walk the device and return everything MyOffice can open. Never throws. */
    fun scan(context: Context, workspaceDir: File? = null): List<DeviceFile> {
        val found = LinkedHashMap<String, DeviceFile>()
        if (hasAllFilesAccess(context)) {
            val workspaceRoot = runCatching { workspaceDir?.canonicalPath }.getOrNull()
            for (root in roots(context)) {
                walk(root, root, "", 0, found, workspaceRoot)
            }
        }
        runCatching { scanMediaStore(context, found) }
        return found.values.toList()
    }

    private fun walk(
        dir: File,
        root: File,
        relPrefix: String,
        depth: Int,
        out: MutableMap<String, DeviceFile>,
        workspaceRoot: String?,
    ) {
        if (depth > MAX_DEPTH || out.size >= MAX_ENTRIES) return
        val children = runCatching { dir.listFiles() }.getOrNull() ?: return
        for (f in children) {
            if (out.size >= MAX_ENTRIES) return
            val name = f.name
            if (name.startsWith(".")) continue
            val rel = if (relPrefix.isEmpty()) name else "$relPrefix/$name"
            if (f.isDirectory) {
                if (LibraryPaths.isSkippedDir(rel)) continue
                walk(f, root, rel, depth + 1, out, workspaceRoot)
            } else if (f.isFile) {
                if (!DocumentKinds.isSupported(name)) continue
                val canonical = runCatching { f.canonicalPath }.getOrDefault(f.absolutePath)
                if (workspaceRoot != null && canonical.startsWith(workspaceRoot)) continue
                val ext = name.substringAfterLast('.', "").lowercase()
                out[canonical] = DeviceFile(
                    path = canonical,
                    name = name,
                    ext = ext,
                    kind = DocumentKinds.kindOf(ext),
                    size = f.length(),
                    modified = f.lastModified(),
                    folder = folderLabel(canonical, root),
                )
            }
        }
    }

    private fun folderLabel(path: String, root: File): String {
        val rootPath = runCatching { root.canonicalPath }.getOrDefault(root.absolutePath)
        val rel = path.removePrefix(rootPath).trim('/')
        val parent = rel.substringBeforeLast('/', "")
        return parent.ifBlank { root.name }
    }

    /** Whatever the system already indexed — works without all-files access. */
    private fun scanMediaStore(context: Context, out: MutableMap<String, DeviceFile>) {
        val projections = arrayOf(
            MediaStore.Files.FileColumns.DATA,
            MediaStore.Files.FileColumns.DISPLAY_NAME,
            MediaStore.Files.FileColumns.SIZE,
            MediaStore.Files.FileColumns.DATE_MODIFIED,
        )
        val uri = MediaStore.Files.getContentUri("external")
        runCatching {
            context.contentResolver.query(uri, projections, null, null, null)?.use { c ->
                val iData = c.getColumnIndex(MediaStore.Files.FileColumns.DATA)
                val iName = c.getColumnIndex(MediaStore.Files.FileColumns.DISPLAY_NAME)
                val iSize = c.getColumnIndex(MediaStore.Files.FileColumns.SIZE)
                val iDate = c.getColumnIndex(MediaStore.Files.FileColumns.DATE_MODIFIED)
                while (c.moveToNext() && out.size < MAX_ENTRIES) {
                    val data = if (iData >= 0) c.getString(iData) else null ?: continue
                    val name = (if (iName >= 0) c.getString(iName) else null) ?: File(data).name
                    if (!DocumentKinds.isSupported(name)) continue
                    if (data.contains("/Android/data/") || data.contains("/Android/obb/")) continue
                    val ext = name.substringAfterLast('.', "").lowercase()
                    out[data] = DeviceFile(
                        path = data,
                        name = name,
                        ext = ext,
                        kind = DocumentKinds.kindOf(ext),
                        size = if (iSize >= 0) c.getLong(iSize) else 0L,
                        modified = (if (iDate >= 0) c.getLong(iDate) else 0L) * 1000L,
                        folder = File(data).parentFile?.name ?: "",
                    )
                }
            }
        }
    }

    // ---------------------------------------------------------------- cache

    private fun cacheFile(context: Context) = File(context.filesDir, CACHE_FILE)

    fun loadCache(context: Context): Pair<List<DeviceFile>, Long>? {
        val f = cacheFile(context)
        if (!f.isFile) return null
        return runCatching {
            val obj = JSONObject(f.readText())
            val scannedAt = obj.optLong("scannedAt", 0L)
            val arr = obj.optJSONArray("files") ?: JSONArray()
            val list = ArrayList<DeviceFile>(arr.length())
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                list += DeviceFile(
                    path = o.getString("path"),
                    name = o.getString("name"),
                    ext = o.optString("ext"),
                    kind = o.optString("kind"),
                    size = o.optLong("size"),
                    modified = o.optLong("modified"),
                    folder = o.optString("folder"),
                )
            }
            list to scannedAt
        }.getOrNull()
    }

    fun saveCache(context: Context, files: List<DeviceFile>, scannedAt: Long) {
        runCatching {
            val arr = JSONArray()
            files.forEach { d ->
                arr.put(
                    JSONObject()
                        .put("path", d.path).put("name", d.name).put("ext", d.ext)
                        .put("kind", d.kind).put("size", d.size).put("modified", d.modified)
                        .put("folder", d.folder)
                )
            }
            JSONObject().put("scannedAt", scannedAt).put("files", arr)
                .toString().let { cacheFile(context).writeText(it) }
        }
    }

    /**
     * Cached scan.  [refresh] forces a re-walk; otherwise the cache is served when it
     * is younger than [CACHE_TTL_MS].  Returns (files, scannedAt, fromCache).
     */
    fun listCached(context: Context, refresh: Boolean, workspaceDir: File? = null): Triple<List<DeviceFile>, Long, Boolean> {
        val cached = loadCache(context)
        if (!refresh && cached != null) {
            val age = System.currentTimeMillis() - cached.second
            if (age in 0..CACHE_TTL_MS) return Triple(cached.first, cached.second, true)
        }
        val files = scan(context, workspaceDir)
        val now = System.currentTimeMillis()
        saveCache(context, files, now)
        return Triple(files, now, false)
    }

    fun toJson(files: List<DeviceFile>): JSONArray {
        val arr = JSONArray()
        files.forEach { d ->
            arr.put(
                JSONObject()
                    .put("path", d.path).put("name", d.name).put("ext", d.ext)
                    .put("kind", d.kind).put("size", d.size).put("modified", d.modified)
                    .put("folder", d.folder).put("source", "device")
            )
        }
        return arr
    }
}
