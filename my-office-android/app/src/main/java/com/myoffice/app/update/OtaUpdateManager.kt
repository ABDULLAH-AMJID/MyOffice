package com.myoffice.app.update

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.util.Base64
import java.util.zip.ZipFile

/**
 * Dual-Tier Cryptographically Verified Over-The-Air (OTA) Update Engine for Android:
 *  - Tier 1 (`hotpatch`): ~45 KB signed Web/UI bundle swapped atomically into `filesDir/ota/active/ui/`
 *    and reloaded live in < 1 second with a 4.5s Native Boot Watchdog + Automatic Rollback.
 *  - Tier 2 (`apk`): Full signed `MyOffice_Android.apk` verified with SHA-256 and launched via
 *    Android `FileProvider` + `ACTION_VIEW` (`REQUEST_INSTALL_PACKAGES`) when native Kotlin changes.
 */
object OtaUpdateManager {

    const val NATIVE_VERSION_NAME = "1.6.1"
    const val NATIVE_VERSION_CODE = 10601
    const val DEFAULT_REPO_URL = "https://github.com/ABDULLAH-AMJID/MyOffice"

    const val RELEASE_PUBLIC_KEY_B64 =
        "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEv7m2geEry3tzazfcAhVieGNKMVkg" +
            "Q6QQHLfFqgRCaRGTHQxuDm9/MCrTuVZ17qJZeXfuRbIlEorzlqYcjGXB/Q=="

    private const val BUILTIN_ENTRY_URL = "file:///android_asset/ui/index.html"

    fun otaRoot(filesDir: File): File = File(filesDir, "ota").apply { mkdirs() }

    private fun stateFile(filesDir: File): File = File(otaRoot(filesDir), "state.json")

    @Synchronized
    fun readState(filesDir: File): JSONObject {
        val sf = stateFile(filesDir)
        if (!sf.isFile) return JSONObject()
        return runCatching { JSONObject(sf.readText(Charsets.UTF_8)) }.getOrElse { JSONObject() }
    }

    @Synchronized
    fun writeState(filesDir: File, state: JSONObject) {
        val root = otaRoot(filesDir)
        val tmp = File(root, "state.json.tmp")
        tmp.writeText(state.toString(2), Charsets.UTF_8)
        tmp.renameTo(stateFile(filesDir))
    }

    fun activeVersionName(filesDir: File): String {
        val st = readState(filesDir)
        val patched = st.optString("activeVersion", "")
        val activeIdx = File(otaRoot(filesDir), "active/ui/index.html")
        return if (patched.isNotEmpty() && activeIdx.isFile && compareSemver(patched, NATIVE_VERSION_NAME) > 0) {
            patched
        } else {
            NATIVE_VERSION_NAME
        }
    }

    fun activeVersionCode(filesDir: File): Int {
        val st = readState(filesDir)
        val activeIdx = File(otaRoot(filesDir), "active/ui/index.html")
        val patchedCode = st.optInt("activeCode", 0)
        return if (activeIdx.isFile && patchedCode > NATIVE_VERSION_CODE) patchedCode else NATIVE_VERSION_CODE
    }

    /**
     * Resolves which UI entry URL `MainActivity.webView` should load, arming crash-recovery canary if hot-patched.
     */
    @Synchronized
    fun resolveStartupEntryUrl(filesDir: File): Pair<String, Boolean> {
        val root = otaRoot(filesDir)
        val activeIndex = File(root, "active/ui/index.html")
        if (!activeIndex.isFile) return BUILTIN_ENTRY_URL to false

        val st = readState(filesDir)
        val inProgress = st.optBoolean("bootInProgress", false)
        val attempts = st.optInt("bootAttempts", 0)
        if (inProgress && attempts >= 2) {
            rollbackToSafeSlot(filesDir, "boot_watchdog_timeout")
            val fallbackIndex = File(root, "active/ui/index.html")
            return if (fallbackIndex.isFile) {
                "file://${fallbackIndex.absolutePath}" to true
            } else {
                BUILTIN_ENTRY_URL to false
            }
        }

        st.put("bootInProgress", true)
        st.put("bootAttempts", attempts + 1)
        writeState(filesDir, st)
        return "file://${activeIndex.absolutePath}" to true
    }

    @Synchronized
    fun markBootHealthy(filesDir: File) {
        val st = readState(filesDir)
        if (st.length() == 0) return
        st.put("bootInProgress", false)
        st.put("bootAttempts", 0)
        st.put("healthy", true)
        st.put("verifiedAt", System.currentTimeMillis() / 1000)
        writeState(filesDir, st)
    }

    @Synchronized
    fun rollbackToSafeSlot(filesDir: File, reason: String = "manual_rollback"): String {
        val root = otaRoot(filesDir)
        val cur = File(root, "active")
        val prev = File(root, "previous")
        if (cur.exists()) {
            val quarantine = File(root, "quarantine_${System.currentTimeMillis()}")
            cur.renameTo(quarantine)
        }
        var restoredVer = ""
        var restoredCode = 0
        if (prev.exists()) {
            prev.renameTo(cur)
            val metaFile = File(cur, "ota_meta.json")
            if (metaFile.isFile) {
                runCatching {
                    val m = JSONObject(metaFile.readText(Charsets.UTF_8))
                    restoredVer = m.optString("version", "")
                    restoredCode = m.optInt("versionCode", 0)
                }
            }
        }
        writeState(
            filesDir,
            JSONObject()
                .put("activeVersion", restoredVer)
                .put("activeCode", restoredCode)
                .put("bootInProgress", false)
                .put("bootAttempts", 0)
                .put("healthy", true)
                .put("lastRollbackReason", reason)
        )
        val restoredIndex = File(root, "active/ui/index.html")
        return if (restoredIndex.isFile) "file://${restoredIndex.absolutePath}" else BUILTIN_ENTRY_URL
    }

    @Synchronized
    fun resetToFactory(filesDir: File): String {
        val root = otaRoot(filesDir)
        listOf("active", "previous", "staging").forEach { name ->
            File(root, name).deleteRecursively()
        }
        writeState(
            filesDir,
            JSONObject()
                .put("activeVersion", "")
                .put("activeCode", 0)
                .put("bootInProgress", false)
                .put("bootAttempts", 0)
                .put("healthy", true)
        )
        return BUILTIN_ENTRY_URL
    }

    /**
     * Normalizes `owner/repo` or `https://github.com/owner/repo` into raw GitHub manifest URL.
     */
    fun resolveManifestUrl(rawInput: String): String {
        val s = rawInput.trim()
        if (s.isEmpty()) return ""
        if (s.startsWith("file://") || s.endsWith(".json")) return s
        val local = File(s)
        if (local.exists()) {
            val f = if (local.isDirectory) File(local, "ota/manifest.json") else local
            return f.toURI().toString()
        }
        var cleaned = s.trimEnd('/')
        if (cleaned.endsWith(".git")) cleaned = cleaned.removeSuffix(".git")
        if ("github.com/" in cleaned) {
            val after = cleaned.substringAfter("github.com/")
            return if ("/tree/" in after) {
                val slug = after.substringBefore("/tree/").trim('/')
                val branch = after.substringAfter("/tree/").trim('/')
                "https://raw.githubusercontent.com/$slug/$branch/ota/manifest.json"
            } else {
                "https://raw.githubusercontent.com/${after.trim('/')}/main/ota/manifest.json"
            }
        }
        if ("://" !in cleaned && cleaned.count { it == '/' } == 1) {
            return "https://raw.githubusercontent.com/$cleaned/main/ota/manifest.json"
        }
        return s
    }

    fun resolveRelativeAssetUrl(assetUrl: String, baseManifestUrl: String): String {
        if (assetUrl.isBlank()) return ""
        if ("://" in assetUrl) return assetUrl
        return URI(baseManifestUrl).resolve(assetUrl).toString()
    }

    /**
     * Exact byte-for-byte match to Python `json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
     */
    fun canonicalPayloadBytes(manifest: JSONObject): ByteArray {
        val sb = StringBuilder()
        appendCanonicalJson(manifest, sb, skipSignatureRoot = true)
        return sb.toString().toByteArray(Charsets.UTF_8)
    }

    private fun appendCanonicalJson(value: Any?, sb: StringBuilder, skipSignatureRoot: Boolean = false) {
        when (value) {
            null, JSONObject.NULL -> sb.append("null")
            is Boolean -> sb.append(if (value) "true" else "false")
            is Int, is Long, is Short, is Byte -> sb.append(value.toString())
            is Number -> {
                val d = value.toDouble()
                if (d == d.toLong().toDouble()) sb.append(d.toLong().toString())
                else sb.append(value.toString())
            }
            is JSONObject -> {
                sb.append('{')
                val keys = mutableListOf<String>()
                val it = value.keys()
                while (it.hasNext()) {
                    val k = it.next()
                    if (skipSignatureRoot && k == "signature") continue
                    keys.add(k)
                }
                keys.sort()
                keys.forEachIndexed { idx, k ->
                    if (idx > 0) sb.append(',')
                    appendEscapedString(k, sb)
                    sb.append(':')
                    appendCanonicalJson(value.opt(k), sb, skipSignatureRoot = false)
                }
                sb.append('}')
            }
            is JSONArray -> {
                sb.append('[')
                for (i in 0 until value.length()) {
                    if (i > 0) sb.append(',')
                    appendCanonicalJson(value.opt(i), sb, skipSignatureRoot = false)
                }
                sb.append(']')
            }
            else -> appendEscapedString(value.toString(), sb)
        }
    }

    private fun appendEscapedString(s: String, sb: StringBuilder) {
        sb.append('"')
        for (ch in s) {
            when (ch) {
                '"' -> sb.append("\\\"")
                '\\' -> sb.append("\\\\")
                '\b' -> sb.append("\\b")
                '\u000C' -> sb.append("\\f")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                else -> {
                    if (ch.code < 0x20) sb.append(String.format("\\u%04x", ch.code))
                    else sb.append(ch)
                }
            }
        }
        sb.append('"')
    }

    fun verifyManifestSignature(manifest: JSONObject, pubKeyB64: String = RELEASE_PUBLIC_KEY_B64): Boolean {
        val sigB64 = manifest.optString("signature", "").trim()
        if (sigB64.isEmpty()) return false
        return runCatching {
            val keyBytes = Base64.getDecoder().decode(pubKeyB64.trim())
            val pubKey = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(keyBytes))
            val verifier = Signature.getInstance("SHA256withECDSA")
            verifier.initVerify(pubKey)
            verifier.update(canonicalPayloadBytes(manifest))
            verifier.verify(Base64.getDecoder().decode(sigB64))
        }.getOrDefault(false)
    }

    fun checkUpdate(
        filesDir: File,
        repoOrManifestUrl: String,
        readToken: String = "",
        pubKeyB64: String = RELEASE_PUBLIC_KEY_B64,
    ): JSONObject {
        val manifestUrl = resolveManifestUrl(repoOrManifestUrl)
        require(manifestUrl.isNotEmpty()) { "Enter your GitHub repository URL first (e.g. username/myoffice)." }

        val rawText = fetchBytes(withCacheBuster(manifestUrl), readToken).toString(Charsets.UTF_8)
        val manifest = JSONObject(rawText)
        val signed = verifyManifestSignature(manifest, pubKeyB64)
        require(signed) { "Rejected tampered update manifest: ECDSA signature check failed." }

        val curVer = activeVersionName(filesDir)
        val curCode = activeVersionCode(filesDir)

        val androidObj = manifest.optJSONObject("android") ?: JSONObject()
        val latestVer = androidObj.optString("version", manifest.optString("version", NATIVE_VERSION_NAME))
        val latestCode = androidObj.optInt("version_code", manifest.optInt("version_code", NATIVE_VERSION_CODE))
        val minNativeCode = androidObj.optInt("min_native_code", NATIVE_VERSION_CODE)

        val canHotpatch = NATIVE_VERSION_CODE >= minNativeCode && androidObj.optString("bundle_url").isNotEmpty()
        val mode = if (canHotpatch) "hotpatch" else "apk"
        val downloadUrl = if (canHotpatch) {
            resolveRelativeAssetUrl(androidObj.optString("bundle_url"), manifestUrl)
        } else {
            resolveRelativeAssetUrl(androidObj.optString("apk_url"), manifestUrl)
        }
        val sha256 = if (canHotpatch) {
            androidObj.optString("bundle_sha256")
        } else {
            androidObj.optString("apk_sha256")
        }
        val sizeBytes = if (canHotpatch) {
            androidObj.optLong("bundle_size", 0L)
        } else {
            androidObj.optLong("apk_size", 0L)
        }
        val available = latestCode > curCode || compareSemver(latestVer, curVer) > 0

        return JSONObject()
            .put("ok", true)
            .put("available", available)
            .put("mode", mode)
            .put("signed", true)
            .put("currentVersion", curVer)
            .put("currentCode", curCode)
            .put("nativeVersion", NATIVE_VERSION_NAME)
            .put("latestVersion", latestVer)
            .put("latestCode", latestCode)
            .put("notes", manifest.optString("notes", ""))
            .put("date", manifest.optString("date", ""))
            .put("downloadUrl", downloadUrl)
            .put("sha256", sha256)
            .put("size", sizeBytes)
    }

    @Synchronized
    fun downloadAndStageUpdate(
        filesDir: File,
        cacheDir: File,
        checkInfo: JSONObject,
        readToken: String = ""
    ): JSONObject {
        val url = checkInfo.getString("downloadUrl")
        val expectedSha = checkInfo.optString("sha256", "").lowercase()
        val mode = checkInfo.getString("mode")
        val latestVer = checkInfo.getString("latestVersion")
        val latestCode = checkInfo.optInt("latestCode", NATIVE_VERSION_CODE)

        val updatesCache = File(cacheDir, "updates").apply { mkdirs() }
        val targetFile = File(updatesCache, if (mode == "hotpatch") "android-ui.zip" else "MyOffice_Update.apk")
        val bytes = fetchBytes(url, readToken)
        val actualSha = sha256Hex(bytes)
        require(expectedSha.isEmpty() || actualSha == expectedSha) {
            "SHA-256 verification failed (expected $expectedSha, got $actualSha)."
        }
        targetFile.writeBytes(bytes)

        if (mode == "apk") {
            return JSONObject()
                .put("ok", true)
                .put("mode", "apk")
                .put("apkPath", targetFile.absolutePath)
                .put("version", latestVer)
        }

        // Atomic extraction of verified Web/UI bundle into `ota/active/ui/`
        val root = otaRoot(filesDir)
        val staging = File(root, "staging").also { it.deleteRecursively() }
        val stagingUi = File(staging, "ui").apply { mkdirs() }
        val canonicalStaging = stagingUi.canonicalFile

        ZipFile(targetFile).use { zip ->
            val entries = zip.entries()
            while (entries.hasMoreElements()) {
                val entry = entries.nextElement()
                val rel = entry.name.replace('\\', '/')
                require(rel.isNotEmpty() && !rel.startsWith("/") && ".." !in rel.split("/")) {
                    "Blocked unsafe OTA archive entry: ${entry.name}"
                }
                val out = File(stagingUi, rel).canonicalFile
                require(out.path.startsWith(canonicalStaging.path)) {
                    "OTA entry escapes staging root: ${entry.name}"
                }
                if (entry.isDirectory) {
                    out.mkdirs()
                } else {
                    out.parentFile?.mkdirs()
                    zip.getInputStream(entry).use { input ->
                        out.outputStream().use { fout -> input.copyTo(fout) }
                    }
                }
            }
        }
        require(File(stagingUi, "index.html").isFile) { "Invalid UI bundle: missing index.html" }

        File(staging, "ota_meta.json").writeText(
            JSONObject().put("version", latestVer).put("versionCode", latestCode).toString(2),
            Charsets.UTF_8
        )

        val active = File(root, "active")
        val previous = File(root, "previous")
        previous.deleteRecursively()
        if (active.exists()) active.renameTo(previous)
        staging.renameTo(active)

        writeState(
            filesDir,
            JSONObject()
                .put("activeVersion", latestVer)
                .put("activeCode", latestCode)
                .put("bootInProgress", false)
                .put("bootAttempts", 0)
                .put("healthy", true)
                .put("appliedAt", System.currentTimeMillis() / 1000)
        )

        return JSONObject()
            .put("ok", true)
            .put("mode", "hotpatch")
            .put("entryUrl", "file://${File(active, "ui/index.html").absolutePath}")
            .put("version", latestVer)
            .put("versionCode", latestCode)
    }

    private fun withCacheBuster(url: String): String {
        if (url.startsWith("file:")) return url
        val sep = if ('?' in url) '&' else '?'
        return "${url}${sep}ts=${System.currentTimeMillis()}"
    }

    private fun fetchBytes(urlStr: String, readToken: String): ByteArray {
        if (urlStr.startsWith("file:")) {
            return File(URI(urlStr)).readBytes()
        }
        val conn = (URL(urlStr).openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000
            readTimeout = 30_000
            useCaches = false
            setRequestProperty("Cache-Control", "no-cache")
            setRequestProperty("User-Agent", "MyOffice-Android/$NATIVE_VERSION_NAME")
            if (readToken.isNotBlank()) {
                setRequestProperty("Authorization", "Bearer ${readToken.trim()}")
            }
        }
        return try {
            val code = conn.responseCode
            require(code in 200..299) { "HTTP $code while fetching $urlStr" }
            conn.inputStream.use { it.readBytes() }
        } finally {
            conn.disconnect()
        }
    }

    fun sha256Hex(data: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }

    fun compareSemver(a: String, b: String): Int {
        val pa = a.split('.').map { it.filter(Char::isDigit).toIntOrNull() ?: 0 }
        val pb = b.split('.').map { it.filter(Char::isDigit).toIntOrNull() ?: 0 }
        for (i in 0 until maxOf(pa.size, pb.size, 3)) {
            val da = pa.getOrElse(i) { 0 }
            val db = pb.getOrElse(i) { 0 }
            if (da != db) return da.compareTo(db)
        }
        return 0
    }
}
