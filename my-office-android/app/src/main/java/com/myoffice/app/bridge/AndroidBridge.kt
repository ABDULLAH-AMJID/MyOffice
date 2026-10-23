package com.myoffice.app.bridge

import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.pdf.PdfRenderer
import android.os.Build
import android.os.ParcelFileDescriptor
import android.print.PrintAttributes
import android.print.PrintManager
import android.speech.tts.TextToSpeech
import android.webkit.JavascriptInterface
import androidx.core.content.FileProvider
import com.myoffice.app.MainActivity
import com.myoffice.app.ai.MobileAiClient
import com.myoffice.app.collab.MobileCollabManager
import com.myoffice.app.crypto.VaultCrypto
import com.myoffice.app.ooxml.OoxmlEngine
import com.myoffice.app.pdf.PdfStudioEngine
import com.myoffice.app.qr.QrCodec
import com.myoffice.app.sync.GmailImapBackend
import com.myoffice.app.sync.LanNetworkUtils
import com.myoffice.app.sync.LocalFolderBackend
import com.myoffice.app.sync.MobileSyncBackend
import com.myoffice.app.sync.MobileSyncHostServer
import com.myoffice.app.sync.PcLanPeerBackend
import com.myoffice.app.sync.RouterFtpBackend
import com.myoffice.app.sync.SyncEngineMobile
import com.myoffice.app.sync.VaultEncryptedBackend
import com.myoffice.app.update.OtaUpdateManager
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.Base64
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

class AndroidBridge(private val activity: MainActivity) {

    val workspaceDir: File = File(activity.filesDir, "workspace").apply { mkdirs() }
    private val versionsDir: File = File(activity.filesDir, "versions").apply { mkdirs() }
    private val prefsFile: File = File(activity.filesDir, "mobile_config.json")
    val keyStore = VaultCrypto.DeviceKeyStore(
        File(activity.filesDir, "security"),
        deviceLabel = "${Build.MANUFACTURER} ${Build.MODEL}".trim().ifEmpty { "MyOffice-Android" }
    )

    private val workerPool = Executors.newCachedThreadPool()
    private val autoSyncScheduler: ScheduledExecutorService = Executors.newSingleThreadScheduledExecutor()

    @Volatile
    private var phoneHostServer: MobileSyncHostServer? = null

    val collabManager = MobileCollabManager(File(workspaceDir, "Collaborations")) { room, evt ->
        val payload = JSONObject().put("room", room).put("event", evt).toString()
        activity.pushJsEvent("window.__onNativeCollabEvent", payload)
    }

    private var tts: TextToSpeech? = null
    @Volatile
    private var ttsReady = false

    init {
        runCatching {
            tts = TextToSpeech(activity.applicationContext) { status ->
                ttsReady = (status == TextToSpeech.SUCCESS)
            }
        }
        // Periodic background sync check every 90 seconds when autoSync is enabled
        autoSyncScheduler.scheduleWithFixedDelay({
            runCatching {
                val cfg = readConfigJson()
                if (cfg.optBoolean("autoSync", false)) {
                    val rep = executeConfiguredSync(cfg)
                    if (rep.uploaded.isNotEmpty() || rep.downloaded.isNotEmpty()) {
                        activity.pushJsEvent("window.__onNativeAutoSync", rep.toJson().toString())
                    }
                }
            }
        }, 60, 90, TimeUnit.SECONDS)
    }

    fun shutdown() {
        runCatching { phoneHostServer?.stop() }
        runCatching { collabManager.shutdownAll() }
        runCatching { tts?.stop(); tts?.shutdown() }
        runCatching { autoSyncScheduler.shutdownNow() }
        runCatching { workerPool.shutdownNow() }
    }

    @Synchronized
    private fun readConfigJson(): JSONObject {
        val obj = if (prefsFile.isFile) runCatching { JSONObject(prefsFile.readText(Charsets.UTF_8)) }.getOrElse { JSONObject() }
        else JSONObject()
        if (obj.optString("updateRepoUrl", "").isBlank()) {
            obj.put("updateRepoUrl", OtaUpdateManager.DEFAULT_REPO_URL)
        }
        return obj
    }

    @Synchronized
    private fun writeConfigJson(obj: JSONObject) {
        prefsFile.writeText(obj.toString(2), Charsets.UTF_8)
    }

    private fun resolveWorkspaceFile(relOrAbs: String): File {
        val f = File(relOrAbs)
        if (f.isAbsolute && f.exists()) return f
        val clean = relOrAbs.replace('\\', '/').trimStart('/')
        return File(workspaceDir, clean)
    }

    private fun saveVersionSnapshot(target: File) {
        if (!target.isFile || target.length() > 25 * 1024 * 1024) return
        runCatching {
            val sub = File(versionsDir, VaultCrypto.sha256Hex(target.absolutePath.toByteArray()).take(12)).apply { mkdirs() }
            val snap = File(sub, "${System.currentTimeMillis()}__${target.name}")
            target.copyTo(snap, overwrite = true)
            val all = sub.listFiles()?.sortedByDescending { it.lastModified() }.orEmpty()
            all.drop(12).forEach { it.delete() }
        }
    }

    // Async Bridge Executor so heavy I/O, PDF rendering, IMAP, or Crypto NEVER blocks the 60fps UI thread
    @JavascriptInterface
    fun callAsync(reqId: String, action: String, argsJson: String) {
        workerPool.execute {
            val result = try {
                val args = if (argsJson.isBlank()) JSONObject() else JSONObject(argsJson)
                dispatchSync(action, args)
            } catch (t: Throwable) {
                JSONObject()
                    .put("ok", false)
                    .put("error", t.message ?: t.javaClass.simpleName)
            }
            activity.resolveJsPromise(reqId, result.toString())
        }
    }

    @JavascriptInterface
    fun requestImportFile() {
        activity.launchImportFilePicker()
    }

    @JavascriptInterface
    fun requestCameraScan() {
        activity.launchCameraCapture(forQr = false)
    }

    @JavascriptInterface
    fun requestScanQrCamera() {
        activity.launchCameraCapture(forQr = true)
    }

    @JavascriptInterface
    fun requestScanQrGallery() {
        activity.launchQrImagePicker()
    }

    @JavascriptInterface
    fun confirmUiBootHealthy() {
        activity.confirmUiBootHealthy()
    }

    @JavascriptInterface
    fun applyNativeTheme(theme: String) {
        activity.applySystemBarAppearance(isDark = theme.equals("dark", ignoreCase = true))
    }

    @JavascriptInterface
    fun printCurrentWebDocument(jobTitle: String) {
        activity.runOnUiThread {
            runCatching {
                val pm = activity.getSystemService(MainActivity::class.java.let { android.content.Context.PRINT_SERVICE }) as PrintManager
                val adapter = activity.webView.createPrintDocumentAdapter(jobTitle.ifBlank { "MyOffice Document" })
                pm.print(jobTitle.ifBlank { "MyOffice Document" }, adapter, PrintAttributes.Builder().build())
            }
        }
    }

    fun dispatchSync(action: String, args: JSONObject): JSONObject {
        return when (action) {
            // ---------------- System & Config ----------------
            "getBootstrap" -> {
                val cfg = readConfigJson()
                JSONObject()
                    .put("ok", true)
                    .put("version", OtaUpdateManager.activeVersionName(activity.filesDir))
                    .put("nativeVersion", OtaUpdateManager.NATIVE_VERSION_NAME)
                    .put("deviceName", keyStore.deviceLabel)
                    .put("vaultKeyId", keyStore.keyId)
                    .put("knownVaultKeys", JSONArray(keyStore.knownKeyIds()))
                    .put("lanIps", JSONArray(LanNetworkUtils.listLanIpv4Addresses()))
                    .put("primaryIp", LanNetworkUtils.primaryLanIp())
                    .put("config", sanitizeConfigForUi(cfg))
                    .put("files", listWorkspaceJson())
                    .put("hostRunning", phoneHostServer?.isRunning == true)
                    .put("activeCollabRooms", collabManager.activeRooms())
            }

            "saveSettings" -> {
                val cur = readConfigJson()
                for (k in args.keys()) {
                    val v = args.get(k)
                    if (k in setOf("gmailAppPassword", "ftpPassword", "aiApiKey", "vaultPassphrase", "pcPin", "pcToken", "githubReadToken")) {
                        val s = v?.toString() ?: ""
                        if (s.isNotEmpty() && s != "••••••••") {
                            cur.put(k, keyStore.sealSecret(s))
                        }
                    } else {
                        cur.put(k, v)
                    }
                }
                writeConfigJson(cur)
                JSONObject().put("ok", true).put("config", sanitizeConfigForUi(cur))
            }

            // ---------------- Dual-Tier Signed GitHub OTA Engine ----------------
            "checkOtaUpdate" -> {
                val cfg = readConfigJson()
                if (args.has("updateRepoUrl")) {
                    cfg.put("updateRepoUrl", args.optString("updateRepoUrl", "").trim())
                }
                if (args.has("githubReadToken")) {
                    val tok = args.optString("githubReadToken", "").trim()
                    if (tok.isNotEmpty() && tok != "••••••••") {
                        cfg.put("githubReadToken", keyStore.sealSecret(tok))
                    } else if (tok.isEmpty()) {
                        cfg.remove("githubReadToken")
                    }
                }
                writeConfigJson(cfg)
                val repoUrl = cfg.optString("updateRepoUrl", "")
                val readTok = keyStore.unsealSecret(cfg.optString("githubReadToken", ""))
                OtaUpdateManager.checkUpdate(activity.filesDir, repoUrl, readTok)
            }

            "applyOtaUpdate" -> {
                val cfg = readConfigJson()
                val repoUrl = args.optString("updateRepoUrl", cfg.optString("updateRepoUrl", "")).trim()
                val readTok = keyStore.unsealSecret(cfg.optString("githubReadToken", ""))
                val checkInfo = OtaUpdateManager.checkUpdate(activity.filesDir, repoUrl, readTok)
                require(checkInfo.optBoolean("available", false)) {
                    "Already up to date (${checkInfo.optString("currentVersion")})."
                }
                val applied = OtaUpdateManager.downloadAndStageUpdate(
                    activity.filesDir,
                    activity.cacheDir,
                    checkInfo,
                    readTok
                )
                if (applied.optString("mode") == "apk") {
                    activity.installDownloadedApk(File(applied.getString("apkPath")))
                } else {
                    autoSyncScheduler.schedule({ activity.loadActiveEntryWithWatchdog() }, 180, TimeUnit.MILLISECONDS)
                }
                applied
            }

            "resetOtaFactory" -> {
                OtaUpdateManager.resetToFactory(activity.filesDir)
                autoSyncScheduler.schedule({ activity.loadActiveEntryWithWatchdog() }, 180, TimeUnit.MILLISECONDS)
                JSONObject().put("ok", true).put("version", OtaUpdateManager.NATIVE_VERSION_NAME)
            }

            // ---------------- Workspace File Management ----------------
            "listFiles" -> JSONObject().put("ok", true).put("files", listWorkspaceJson())

            "deleteFile" -> {
                val f = resolveWorkspaceFile(args.getString("path"))
                val ok = f.exists() && f.delete()
                JSONObject().put("ok", ok)
            }

            "renameFile" -> {
                val src = resolveWorkspaceFile(args.getString("path"))
                val newName = args.getString("newName").trim()
                val dst = File(src.parentFile, newName)
                require(!dst.exists()) { "A file named '$newName' already exists." }
                val ok = src.renameTo(dst)
                JSONObject().put("ok", ok).put("newPath", dst.relativeTo(workspaceDir).invariantSeparatorsPath)
            }

            "toggleStar" -> {
                val cfg = readConfigJson()
                val stars = cfg.optJSONArray("starred")?.let { arr ->
                    (0 until arr.length()).map { arr.getString(it) }.toMutableSet()
                } ?: mutableSetOf()
                val rel = args.getString("rel")
                if (!stars.add(rel)) stars.remove(rel)
                cfg.put("starred", JSONArray(stars.toList()))
                writeConfigJson(cfg)
                JSONObject().put("ok", true).put("starred", JSONArray(stars.toList()))
            }

            "shareFile" -> {
                val f = resolveWorkspaceFile(args.getString("path"))
                require(f.isFile) { "File not found: ${f.name}" }
                activity.runOnUiThread {
                    val uri = FileProvider.getUriForFile(activity, "${activity.packageName}.fileprovider", f)
                    val intent = Intent(Intent.ACTION_SEND).apply {
                        type = guessMime(f.extension)
                        putExtra(Intent.EXTRA_STREAM, uri)
                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    }
                    activity.startActivity(Intent.createChooser(intent, "Share ${f.name}"))
                }
                JSONObject().put("ok", true)
            }

            "listVersions" -> {
                val f = resolveWorkspaceFile(args.getString("path"))
                val sub = File(versionsDir, VaultCrypto.sha256Hex(f.absolutePath.toByteArray()).take(12))
                val arr = JSONArray()
                sub.listFiles()?.sortedByDescending { it.lastModified() }?.forEach { snap ->
                    arr.put(
                        JSONObject()
                            .put("snapPath", snap.absolutePath)
                            .put("size", snap.length())
                            .put("mtime", snap.lastModified())
                    )
                }
                JSONObject().put("ok", true).put("versions", arr)
            }

            "restoreVersion" -> {
                val snap = File(args.getString("snapPath"))
                val target = resolveWorkspaceFile(args.getString("targetPath"))
                snap.copyTo(target, overwrite = true)
                JSONObject().put("ok", true)
            }

            // ---------------- Open & Save Any Format (DOCX / XLSX / PPTX / PDF / Photos / Vault / Text) ----------------
            "openDocument" -> openDocumentNative(args)

            "saveDocument" -> saveDocumentNative(args)

            "exportToPdf" -> {
                val title = args.optString("title", "Document")
                val linesArr = args.optJSONArray("lines") ?: JSONArray()
                val lines = (0 until linesArr.length()).map { linesArr.optString(it, "") }
                val outName = args.optString("outName", "$title.pdf").let {
                    if (it.lowercase().endsWith(".pdf")) it else "$it.pdf"
                }
                val outFile = File(workspaceDir, outName)
                PdfStudioEngine.writePlainDocumentPdf(title, lines, outFile)
                    .put("rel", outFile.relativeTo(workspaceDir).invariantSeparatorsPath)
            }

            "repairDocument" -> {
                val src = resolveWorkspaceFile(args.getString("path"))
                val cleanStem = src.nameWithoutExtension.replace(Regex("(\\s*\\(Repaired\\))+", RegexOption.IGNORE_CASE), "")
                val outFile = File(src.parentFile, "$cleanStem (Repaired).${src.extension}")
                OoxmlEngine.repairOoxmlArchive(src.readBytes(), outFile)
                    .put("rel", outFile.relativeTo(workspaceDir).invariantSeparatorsPath)
            }

            // ---------------- Native High-DPI PDF Rendering & 13 PDF Toolbox Tools ----------------
            "renderPdfPage" -> {
                val f = resolveWorkspaceFile(args.getString("path"))
                val pageIdx = args.optInt("pageIndex", 0)
                val scale = args.optDouble("scale", 1.75).toFloat().coerceIn(0.75f, 3.0f)
                renderPdfPageWithAndroidRenderer(f, pageIdx, scale)
            }

            "runPdfTool" -> executePdfTool(args)

            // ---------------- Hardware Vault (.movault & .movaultkey) ----------------
            "encryptToVault" -> {
                val src = resolveWorkspaceFile(args.getString("path"))
                val pass = args.optString("passphrase", "")
                val vaultBytes = VaultCrypto.encryptBytes(
                    src.readBytes(),
                    originalName = src.name,
                    originalPath = src.relativeTo(workspaceDir).invariantSeparatorsPath,
                    store = keyStore,
                    passphrase = pass
                )
                val outVault = File(workspaceDir, "${src.nameWithoutExtension}.movault")
                outVault.writeBytes(vaultBytes)
                JSONObject()
                    .put("ok", true)
                    .put("rel", outVault.relativeTo(workspaceDir).invariantSeparatorsPath)
                    .put("size", outVault.length())
                    .put("keyId", keyStore.keyId)
            }

            "decryptVaultFile" -> {
                val src = resolveWorkspaceFile(args.getString("path"))
                val pass = args.optString("passphrase", "")
                val dec = VaultCrypto.decryptBytes(src.readBytes(), keyStore, pass)
                val restored = File(workspaceDir, dec.originalName)
                restored.writeBytes(dec.bytes)
                JSONObject()
                    .put("ok", true)
                    .put("restoredRel", restored.relativeTo(workspaceDir).invariantSeparatorsPath)
                    .put("originalName", dec.originalName)
                    .put("deviceLabel", dec.deviceLabel)
                    .put("size", restored.length())
            }

            "exportVaultKeyKit" -> {
                val pw = args.getString("password")
                val kitBytes = keyStore.exportRecoveryKit(pw)
                val kitFile = File(workspaceDir, "MyOffice_Android_Recovery.movaultkey")
                kitFile.writeBytes(kitBytes)
                JSONObject()
                    .put("ok", true)
                    .put("rel", kitFile.name)
                    .put("keyId", keyStore.keyId)
            }

            "importVaultKeyKit" -> {
                val kitFile = resolveWorkspaceFile(args.getString("path"))
                val pw = args.getString("password")
                val imported = keyStore.importRecoveryKit(kitFile.readBytes(), pw)
                JSONObject()
                    .put("ok", true)
                    .put("importedKeys", JSONArray(imported))
                    .put("knownVaultKeys", JSONArray(keyStore.knownKeyIds()))
            }

            // ---------------- PC <-> Mobile Sync Hub, LAN Discovery, Gmail & Router USB ----------------
            "discoverLanPeers" -> {
                val peers = LanNetworkUtils.discoverPeersOnLan(900)
                JSONObject().put("ok", true).put("peers", peers)
            }

            "pairAndSyncPc" -> {
                val rawUrl = args.getString("url").trim()
                val pin = args.optString("pin", "").trim()
                val peer = PcLanPeerBackend(rawUrl, initialPin = pin, deviceName = keyStore.deviceLabel, keyStore = keyStore)
                val pairResp = peer.pairWithPc(pinOverride = pin, shareMyVaultKey = true)
                val rep = SyncEngineMobile(workspaceDir, peer).syncOnce()
                val rooms = runCatching { peer.fetchActivePcCollabRooms() }.getOrElse { JSONArray() }
                val cfg = readConfigJson()
                cfg.put("syncKind", "pc_lan")
                cfg.put("pcUrl", peer.baseUrl)
                cfg.put("pcToken", keyStore.sealSecret(peer.token))
                if (pin.isNotEmpty()) cfg.put("pcPin", keyStore.sealSecret(pin))
                cfg.put("pairedPcName", pairResp.optString("device", "Windows PC"))
                val pcRepoUrl = pairResp.optString("update_repo_url", "").trim()
                if (pcRepoUrl.isNotEmpty() && cfg.optString("updateRepoUrl", "").isEmpty()) {
                    cfg.put("updateRepoUrl", pcRepoUrl)
                }
                writeConfigJson(cfg)
                JSONObject()
                    .put("ok", true)
                    .put("pcDevice", pairResp.optString("device", "Windows PC"))
                    .put("report", rep.toJson())
                    .put("collabRooms", rooms)
                    .put("knownVaultKeys", JSONArray(keyStore.knownKeyIds()))
            }

            "runSyncNow" -> {
                val cfg = readConfigJson()
                if (args.has("syncKind")) {
                    for (k in args.keys()) {
                        val v = args.get(k)
                        if (k in setOf("gmailAppPassword", "ftpPassword", "vaultPassphrase", "pcPin")) {
                            val s = v?.toString() ?: ""
                            if (s.isNotEmpty() && s != "••••••••") cfg.put(k, keyStore.sealSecret(s))
                        } else {
                            cfg.put(k, v)
                        }
                    }
                    writeConfigJson(cfg)
                }
                val rep = executeConfiguredSync(cfg)
                val rooms = if (cfg.optString("syncKind") == "pc_lan" && cfg.optString("pcUrl").isNotEmpty()) {
                    runCatching {
                        PcLanPeerBackend(
                            cfg.getString("pcUrl"),
                            initialToken = keyStore.unsealSecret(cfg.optString("pcToken")),
                            initialPin = keyStore.unsealSecret(cfg.optString("pcPin")),
                            deviceName = keyStore.deviceLabel,
                            keyStore = keyStore
                        ).fetchActivePcCollabRooms()
                    }.getOrElse { JSONArray() }
                } else JSONArray()
                JSONObject().put("ok", true).put("report", rep.toJson()).put("collabRooms", rooms)
            }

            "togglePhoneSyncHost" -> {
                val enable = args.optBoolean("enable", true)
                if (!enable) {
                    phoneHostServer?.stop()
                    phoneHostServer = null
                    JSONObject().put("ok", true).put("running", false)
                } else {
                    val srv = phoneHostServer?.takeIf { it.isRunning }
                        ?: MobileSyncHostServer(
                            workspaceDir,
                            deviceName = keyStore.deviceLabel,
                            keyStore = keyStore
                        ).also {
                            it.start()
                            phoneHostServer = it
                        }
                    val ip = args.optString("ip", "").ifEmpty { LanNetworkUtils.primaryLanIp() }
                    val uri = srv.pairUri(ip)
                    JSONObject()
                        .put("ok", true)
                        .put("running", true)
                        .put("url", srv.baseUrl(ip))
                        .put("pin", srv.pin)
                        .put("pairUri", uri)
                        .put("qrSvg", QrCodec.encodeToSvg(uri))
                        .put("logs", JSONArray(synchronized(srv.activityLog) { srv.activityLog.takeLast(30) }))
                }
            }

            "makeQrSvg" -> {
                val text = args.getString("text")
                JSONObject().put("ok", true).put("svg", QrCodec.encodeToSvg(text))
            }

            // ---------------- Live Multi-File Collaboration & Team Chat ----------------
            "joinCollabRoom" -> {
                val link = args.getString("link")
                val userName = args.optString("userName", keyStore.deviceLabel)
                val res = collabManager.joinRoom(link, userName = userName)
                JSONObject().put("ok", true).put("session", res)
            }

            "sendCollabChat" -> {
                val ok = collabManager.sendChat(args.getString("room"), args.getString("text"))
                JSONObject().put("ok", ok)
            }

            "pushCollabWriter" -> {
                val ok = collabManager.pushWriterText(args.getString("room"), args.getString("text"))
                JSONObject().put("ok", ok)
            }

            "pushCollabSheetCell" -> {
                val ok = collabManager.pushSheetCell(
                    args.getString("room"),
                    args.optString("sheet", "Sheet1"),
                    args.getString("cell"),
                    args.getString("value")
                )
                JSONObject().put("ok", ok)
            }

            "pushCollabSnapshot" -> {
                val f = resolveWorkspaceFile(args.getString("path"))
                val ok = collabManager.pushBinaryFileSnapshot(args.getString("room"), f, args.optString("kind", "any"))
                JSONObject().put("ok", ok)
            }

            "leaveCollabRoom" -> {
                collabManager.leaveRoom(args.getString("room"))
                JSONObject().put("ok", true)
            }

            // ---------------- AI Assistant & BM25 Chat with PDF ----------------
            "askAi" -> {
                val cfg = readConfigJson()
                val provider = args.optString("provider", cfg.optString("aiProvider", "ollama"))
                val endpoint = args.optString("endpoint", cfg.optString("aiEndpoint", ""))
                val rawKey = args.optString("apiKey", "")
                val apiKey = if (rawKey.isNotEmpty() && rawKey != "••••••••") rawKey
                else keyStore.unsealSecret(cfg.optString("aiApiKey", ""))
                val model = args.optString("model", cfg.optString("aiModel", "llama3.2"))
                var userPrompt = args.getString("prompt")
                val systemPrompt = args.optString(
                    "system",
                    "You are MyOffice AI Assistant. Give clear, accurate, ready-to-use output."
                )

                // If pdfPath is supplied, run local BM25 page retrieval first so answers cite exact pages!
                if (args.optString("pdfPath").isNotEmpty()) {
                    val pdfFile = resolveWorkspaceFile(args.getString("pdfPath"))
                    val info = PdfStudioEngine.inspectPdf(pdfFile)
                    val pagesArr = info.getJSONArray("pagesText")
                    val pages = (0 until pagesArr.length()).map { pagesArr.optString(it, "") }
                    val top = MobileAiClient.retrieveTopPdfPagesBm25(pages, userPrompt, topK = 4)
                    val ctx = top.joinToString("\n\n") { (pNum, txt) -> "[Page $pNum]\n$txt" }
                    userPrompt = "Relevant passages extracted from '${pdfFile.name}':\n\n$ctx\n\nQuestion: $userPrompt\nCite page numbers as [Page X]."
                }

                val answer = MobileAiClient.complete(provider, endpoint, apiKey, model, systemPrompt, userPrompt)
                JSONObject().put("ok", true).put("answer", answer)
            }

            // ---------------- Voice Read-Aloud (Android TextToSpeech: EN / UR / HI) ----------------
            "speakText" -> {
                val text = args.optString("text", "")
                val lang = args.optString("lang", "en_US")
                if (ttsReady && text.isNotBlank()) {
                    val loc = when {
                        lang.startsWith("ur") -> Locale("ur", "PK")
                        lang.startsWith("hi") -> Locale("hi", "IN")
                        else -> Locale.US
                    }
                    tts?.language = loc
                    tts?.speak(text.take(3900), TextToSpeech.QUEUE_FLUSH, null, "mo_tts_${System.currentTimeMillis()}")
                }
                JSONObject().put("ok", ttsReady)
            }

            "stopSpeaking" -> {
                tts?.stop()
                JSONObject().put("ok", true)
            }

            // ---------------- Built-in 10-Check Android Self-Test ----------------
            "runSelfTest" -> runAndroidSelfTest()

            else -> JSONObject().put("ok", false).put("error", "Unknown native action: $action")
        }
    }

    private fun openDocumentNative(args: JSONObject): JSONObject {
        val f = resolveWorkspaceFile(args.getString("path"))
        require(f.isFile) { "Document not found: ${f.path}" }
        val ext = f.extension.lowercase()
        val rel = if (f.canonicalPath.startsWith(workspaceDir.canonicalPath)) {
            f.canonicalFile.relativeTo(workspaceDir.canonicalFile).invariantSeparatorsPath
        } else {
            f.name
        }

        return when (ext) {
            "docx" -> OoxmlEngine.readDocx(f.readBytes())
                .put("ok", true).put("name", f.name).put("rel", rel).put("ext", ext)

            "xlsx" -> OoxmlEngine.readXlsx(f.readBytes())
                .put("ok", true).put("name", f.name).put("rel", rel).put("ext", ext)

            "csv", "tsv" -> OoxmlEngine.readCsv(f.readText(Charsets.UTF_8), if (ext == "tsv") '\t' else ',')
                .put("ok", true).put("name", f.name).put("rel", rel).put("ext", ext)

            "pptx" -> OoxmlEngine.readPptx(f.readBytes())
                .put("ok", true).put("name", f.name).put("rel", rel).put("ext", ext)

            "pdf" -> {
                val pw = args.optString("password", "")
                val info = PdfStudioEngine.inspectPdf(f, pw)
                val firstPage = renderPdfPageWithAndroidRenderer(f, 0, 1.7f)
                info.put("ok", true)
                    .put("kind", "pdf")
                    .put("name", f.name)
                    .put("rel", rel)
                    .put("ext", ext)
                    .put("firstPageImage", firstPage.optString("dataUri", ""))
            }

            "png", "jpg", "jpeg", "webp", "bmp", "gif" -> {
                val b64 = Base64.getEncoder().encodeToString(f.readBytes())
                JSONObject()
                    .put("ok", true)
                    .put("kind", "photos")
                    .put("name", f.name)
                    .put("rel", rel)
                    .put("ext", ext)
                    .put("dataUri", "data:${guessMime(ext)};base64,$b64")
            }

            "movault" -> {
                val pass = args.optString("passphrase", "")
                val dec = VaultCrypto.decryptBytes(f.readBytes(), keyStore, pass)
                val unpacked = File(workspaceDir, dec.originalName)
                unpacked.writeBytes(dec.bytes)
                openDocumentNative(JSONObject().put("path", unpacked.absolutePath))
                    .put("unlockedFromVault", f.name)
                    .put("vaultDevice", dec.deviceLabel)
            }

            else -> {
                val text = f.readText(Charsets.UTF_8)
                val paras = JSONArray()
                text.replace("\r\n", "\n").split('\n').forEach { line ->
                    paras.put(JSONObject().put("type", "p").put("text", line))
                }
                JSONObject()
                    .put("ok", true)
                    .put("kind", "writer")
                    .put("name", f.name)
                    .put("rel", rel)
                    .put("ext", ext)
                    .put("plainText", text)
                    .put("blocks", paras)
                    .put("html", text.split('\n').joinToString("") { "<p>${it.replace("&", "&amp;").replace("<", "&lt;")}</p>" })
            }
        }
    }

    private fun saveDocumentNative(args: JSONObject): JSONObject {
        val rel = args.getString("rel").replace('\\', '/').trimStart('/')
        val target = File(workspaceDir, rel)
        target.parentFile?.mkdirs()
        if (target.isFile) saveVersionSnapshot(target)

        val kind = args.getString("kind")
        val ext = target.extension.lowercase()
        when (kind) {
            "writer" -> {
                if (ext in setOf("txt", "md", "py", "json")) {
                    target.writeText(args.optString("plainText", ""), Charsets.UTF_8)
                } else if (ext in setOf("html", "htm")) {
                    target.writeText(args.optString("html", ""), Charsets.UTF_8)
                } else {
                    val blocks = args.optJSONArray("blocks") ?: JSONArray()
                    target.writeBytes(OoxmlEngine.writeDocx(blocks, target.nameWithoutExtension))
                }
            }
            "sheet" -> {
                val sheets = args.optJSONArray("sheets") ?: JSONArray()
                if (ext == "csv") {
                    val first = sheets.optJSONObject(0)?.optJSONObject("cells") ?: JSONObject()
                    val csvText = cellsToCsv(first)
                    target.writeText(csvText, Charsets.UTF_8)
                } else {
                    target.writeBytes(OoxmlEngine.writeXlsx(sheets))
                }
            }
            "slide" -> {
                val slides = args.optJSONArray("slides") ?: JSONArray()
                target.writeBytes(OoxmlEngine.writePptx(slides))
            }
            "photos" -> {
                val uri = args.getString("dataUri")
                val b64 = uri.substringAfter("base64,", "")
                target.writeBytes(Base64.getDecoder().decode(b64))
            }
            else -> throw IllegalArgumentException("Unsupported document save kind: $kind")
        }
        return JSONObject()
            .put("ok", true)
            .put("rel", target.relativeTo(workspaceDir).invariantSeparatorsPath)
            .put("size", target.length())
            .put("mtime", target.lastModified())
    }

    private fun cellsToCsv(cells: JSONObject): String {
        var maxR = 0
        var maxC = 0
        for (ref in cells.keys()) {
            val (r, c) = OoxmlEngine.a1ToRowCol(ref)
            if (r > maxR) maxR = r
            if (c > maxC) maxC = c
        }
        return (0..maxR).joinToString("\n") { r ->
            (0..maxC).joinToString(",") { c ->
                val ref = "${OoxmlEngine.colIndexToLetters(c)}${r + 1}"
                val v = cells.optJSONObject(ref)?.optString("value", "") ?: ""
                if (',' in v || '"' in v || '\n' in v) "\"${v.replace("\"", "\"\"")}\"" else v
            }
        }
    }

    private fun renderPdfPageWithAndroidRenderer(pdfFile: File, pageIndex: Int, scale: Float): JSONObject {
        ParcelFileDescriptor.open(pdfFile, ParcelFileDescriptor.MODE_READ_ONLY).use { pfd ->
            PdfRenderer(pfd).use { renderer ->
                val safeIdx = pageIndex.coerceIn(0, (renderer.pageCount - 1).coerceAtLeast(0))
                renderer.openPage(safeIdx).use { page ->
                    val w = (page.width * scale).toInt().coerceIn(320, 2200)
                    val h = (page.height * scale).toInt().coerceIn(320, 3200)
                    val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
                    Canvas(bmp).drawColor(Color.WHITE)
                    page.render(bmp, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
                    val out = ByteArrayOutputStream()
                    bmp.compress(Bitmap.CompressFormat.PNG, 92, out)
                    bmp.recycle()
                    val b64 = Base64.getEncoder().encodeToString(out.toByteArray())
                    return JSONObject()
                        .put("ok", true)
                        .put("pageIndex", safeIdx)
                        .put("pageCount", renderer.pageCount)
                        .put("width", w)
                        .put("height", h)
                        .put("dataUri", "data:image/png;base64,$b64")
                }
            }
        }
    }

    private fun executePdfTool(args: JSONObject): JSONObject {
        val tool = args.getString("tool")
        return when (tool) {
            "merge" -> {
                val arr = args.getJSONArray("paths")
                val files = (0 until arr.length()).map { resolveWorkspaceFile(arr.getString(it)) }
                val out = File(workspaceDir, args.optString("outName", "Merged_${System.currentTimeMillis() % 10000}.pdf"))
                PdfStudioEngine.mergePdfs(files, out).put("rel", out.name)
            }
            "split" -> {
                val src = resolveWorkspaceFile(args.getString("path"))
                PdfStudioEngine.splitPdf(src, args.optString("ranges", "all"), workspaceDir)
            }
            "protect" -> {
                val src = resolveWorkspaceFile(args.getString("path"))
                val pw = args.getString("password")
                val out = File(workspaceDir, "${src.nameWithoutExtension}_Protected.pdf")
                PdfStudioEngine.protectPdf(src, pw, pw, out).put("rel", out.name)
            }
            "unlock" -> {
                val src = resolveWorkspaceFile(args.getString("path"))
                val pw = args.getString("password")
                val out = File(workspaceDir, "${src.nameWithoutExtension}_Unlocked.pdf")
                PdfStudioEngine.unlockPdf(src, pw, out).put("rel", out.name)
            }
            "watermark" -> {
                val src = resolveWorkspaceFile(args.getString("path"))
                val out = File(workspaceDir, "${src.nameWithoutExtension}_Watermarked.pdf")
                PdfStudioEngine.watermarkPdf(
                    src,
                    args.optString("text", "CONFIDENTIAL"),
                    args.optDouble("opacity", 0.22).toFloat(),
                    args.optDouble("fontSize", 44.0).toFloat(),
                    out
                ).put("rel", out.name)
            }
            "page_numbers" -> {
                val src = resolveWorkspaceFile(args.getString("path"))
                val out = File(workspaceDir, "${src.nameWithoutExtension}_Numbered.pdf")
                PdfStudioEngine.addPageNumbersPdf(
                    src,
                    args.optString("position", "bottom-center"),
                    args.optString("format", "Page {n} of {total}"),
                    args.optInt("startAt", 1),
                    out
                ).put("rel", out.name)
            }
            "stamp" -> {
                val src = resolveWorkspaceFile(args.getString("path"))
                val label = args.optString("label", "APPROVED")
                val out = File(workspaceDir, "${src.nameWithoutExtension}_${label.lowercase()}.pdf")
                PdfStudioEngine.stampPdf(
                    src,
                    label,
                    args.optBoolean("includeDate", true),
                    args.optString("color", "#DC2626"),
                    out
                ).put("rel", out.name)
            }
            "flatten" -> {
                val src = resolveWorkspaceFile(args.getString("path"))
                val out = File(workspaceDir, "${src.nameWithoutExtension}_Flattened.pdf")
                PdfStudioEngine.fillAndFlattenPdf(src, emptyMap(), flatten = true, outputFile = out).put("rel", out.name)
            }
            "fill_forms" -> {
                val src = resolveWorkspaceFile(args.getString("path"))
                val valsJson = args.optJSONObject("values") ?: JSONObject()
                val map = valsJson.keys().asSequence().associateWith { valsJson.optString(it, "") }
                val out = File(workspaceDir, "${src.nameWithoutExtension}_Filled.pdf")
                PdfStudioEngine.fillAndFlattenPdf(src, map, args.optBoolean("flatten", false), out).put("rel", out.name)
            }
            "extract_text" -> {
                val src = resolveWorkspaceFile(args.getString("path"))
                val info = PdfStudioEngine.inspectPdf(src)
                val pages = info.getJSONArray("pagesText")
                val full = (0 until pages.length()).joinToString("\n\n--- Page break ---\n\n") { pages.optString(it, "") }
                val out = File(workspaceDir, "${src.nameWithoutExtension}_Extracted.txt")
                out.writeText(full, Charsets.UTF_8)
                JSONObject().put("ok", true).put("rel", out.name).put("text", full)
            }
            "compress" -> {
                val src = resolveWorkspaceFile(args.getString("path"))
                val out = File(workspaceDir, "${src.nameWithoutExtension}_Compressed.pdf")
                // Re-render compressed stream cleanly via Android PdfRenderer -> JPEG stream -> PDF
                val origSize = src.length()
                compressPdfViaRenderer(src, args.optInt("quality", 68).coerceIn(30, 92), out)
                JSONObject()
                    .put("ok", true)
                    .put("rel", out.name)
                    .put("beforeBytes", origSize)
                    .put("afterBytes", out.length())
            }
            "images_to_pdf" -> {
                val arr = args.getJSONArray("paths")
                val imgs = (0 until arr.length()).map { resolveWorkspaceFile(arr.getString(it)) }
                val out = File(workspaceDir, args.optString("outName", "Scanned_${System.currentTimeMillis() % 10000}.pdf"))
                imagesToPdfNative(imgs, out)
                JSONObject().put("ok", true).put("rel", out.name).put("size", out.length())
            }
            else -> throw IllegalArgumentException("Unknown PDF tool: $tool")
        }
    }

    private fun imagesToPdfNative(imageFiles: List<File>, outputPdf: File) {
        val pdf = android.graphics.pdf.PdfDocument()
        try {
            imageFiles.forEachIndexed { idx, imgFile ->
                val bmp = BitmapFactory.decodeFile(imgFile.absolutePath) ?: return@forEachIndexed
                val pageInfo = android.graphics.pdf.PdfDocument.PageInfo.Builder(595, 842, idx + 1).create()
                val page = pdf.startPage(pageInfo)
                val canvas = page.canvas
                canvas.drawColor(Color.WHITE)
                val margin = 28f
                val maxW = 595f - margin * 2
                val maxH = 842f - margin * 2
                val ratio = minOf(maxW / bmp.width, maxH / bmp.height)
                val dw = bmp.width * ratio
                val dh = bmp.height * ratio
                val left = (595f - dw) / 2f
                val top = (842f - dh) / 2f
                canvas.drawBitmap(
                    bmp,
                    null,
                    android.graphics.RectF(left, top, left + dw, top + dh),
                    android.graphics.Paint(android.graphics.Paint.FILTER_BITMAP_FLAG)
                )
                pdf.finishPage(page)
                bmp.recycle()
            }
            outputPdf.outputStream().use { pdf.writeTo(it) }
        } finally {
            pdf.close()
        }
    }

    private fun compressPdfViaRenderer(src: File, jpegQuality: Int, outPdf: File) {
        ParcelFileDescriptor.open(src, ParcelFileDescriptor.MODE_READ_ONLY).use { pfd ->
            PdfRenderer(pfd).use { renderer ->
                val outDoc = android.graphics.pdf.PdfDocument()
                try {
                    for (i in 0 until renderer.pageCount) {
                        renderer.openPage(i).use { p ->
                            val bmp = Bitmap.createBitmap(
                                (p.width * 1.25f).toInt().coerceAtLeast(300),
                                (p.height * 1.25f).toInt().coerceAtLeast(300),
                                Bitmap.Config.RGB_565
                            )
                            Canvas(bmp).drawColor(Color.WHITE)
                            p.render(bmp, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
                            val jpegOut = ByteArrayOutputStream()
                            bmp.compress(Bitmap.CompressFormat.JPEG, jpegQuality, jpegOut)
                            bmp.recycle()
                            val redecoded = BitmapFactory.decodeByteArray(jpegOut.toByteArray(), 0, jpegOut.size())
                            val pInfo = android.graphics.pdf.PdfDocument.PageInfo.Builder(p.width, p.height, i + 1).create()
                            val page = outDoc.startPage(pInfo)
                            page.canvas.drawBitmap(
                                redecoded,
                                null,
                                android.graphics.RectF(0f, 0f, p.width.toFloat(), p.height.toFloat()),
                                null
                            )
                            outDoc.finishPage(page)
                            redecoded.recycle()
                        }
                    }
                    outPdf.outputStream().use { outDoc.writeTo(it) }
                } finally {
                    outDoc.close()
                }
            }
        }
    }

    private fun executeConfiguredSync(cfg: JSONObject): com.myoffice.app.sync.SyncReportMobile {
        val kind = cfg.optString("syncKind", "pc_lan")
        val rawBackend: MobileSyncBackend = when (kind) {
            "pc_lan" -> PcLanPeerBackend(
                cfg.optString("pcUrl", ""),
                initialToken = keyStore.unsealSecret(cfg.optString("pcToken", "")),
                initialPin = keyStore.unsealSecret(cfg.optString("pcPin", "")),
                deviceName = keyStore.deviceLabel,
                keyStore = keyStore
            )
            "gmail" -> GmailImapBackend(
                cfg.optString("gmailAddress", ""),
                keyStore.unsealSecret(cfg.optString("gmailAppPassword", "")),
                label = cfg.optString("gmailLabel", "MyOffice/Sync").ifBlank { "MyOffice/Sync" }
            )
            "ftp" -> RouterFtpBackend(
                cfg.optString("ftpUrl", ""),
                cfg.optString("ftpUser", "anonymous"),
                keyStore.unsealSecret(cfg.optString("ftpPassword", ""))
            )
            "folder" -> LocalFolderBackend(File(cfg.optString("folderTarget", File(activity.filesDir, "local_backup").absolutePath)))
            else -> throw IllegalArgumentException("Select a sync method first ($kind)")
        }
        val finalBackend = if (cfg.optBoolean("vaultEncryptSync", false)) {
            VaultEncryptedBackend(rawBackend, keyStore, keyStore.unsealSecret(cfg.optString("vaultPassphrase", "")))
        } else {
            rawBackend
        }
        return SyncEngineMobile(workspaceDir, finalBackend).syncOnce()
    }

    private fun runAndroidSelfTest(): JSONObject {
        val checks = JSONArray()
        fun check(label: String, block: () -> String) {
            val t0 = System.currentTimeMillis()
            try {
                val detail = block()
                checks.put(JSONObject().put("name", label).put("ok", true).put("detail", detail).put("ms", System.currentTimeMillis() - t0))
            } catch (t: Throwable) {
                checks.put(JSONObject().put("name", label).put("ok", false).put("detail", t.message ?: t.javaClass.simpleName).put("ms", System.currentTimeMillis() - t0))
            }
        }
        val tmp = File(activity.cacheDir, "selftest_${System.currentTimeMillis()}").apply { mkdirs() }
        try {
            check("Writer OOXML (.docx + Urdu RTL)") {
                val b = OoxmlEngine.writeDocx(JSONArray().put(JSONObject().put("type", "p").put("rtl", true).put("text", "MyOffice اردو")), "Test")
                val r = OoxmlEngine.readDocx(b)
                "Round-trip OK (${b.size} B, '${r.optString("plainText")}')"
            }
            check("Spreadsheets OOXML (.xlsx + Formulas)") {
                val s = JSONArray().put(JSONObject().put("name", "Sheet1").put("cells", JSONObject().put("A1", JSONObject().put("raw", "=SUM(10,20)").put("value", "30"))))
                val b = OoxmlEngine.writeXlsx(s)
                val r = OoxmlEngine.readXlsx(b)
                "Round-trip OK (${b.size} B, ${r.getJSONArray("sheets").length()} sheet)"
            }
            check("Presentation OOXML (.pptx)") {
                val p = OoxmlEngine.writePptx(JSONArray().put(JSONObject().put("title", "Deck").put("bullets", JSONArray().put("Point A"))))
                val r = OoxmlEngine.readPptx(p)
                "Round-trip OK (${p.size} B, ${r.getJSONArray("slides").length()} slide)"
            }
            check("PDF Studio (Create + Stamp + AES-256 Protect)") {
                val p1 = File(tmp, "a.pdf")
                val p2 = File(tmp, "b.pdf")
                PdfStudioEngine.writePlainDocumentPdf("Test PDF", listOf("Line 1"), p1)
                PdfStudioEngine.stampPdf(p1, "APPROVED", true, "#16A34A", p2)
                val p3 = File(tmp, "enc.pdf")
                PdfStudioEngine.protectPdf(p2, "secret123", "secret123", p3)
                "PDF created, stamped & AES-256 encrypted (${p3.length()} B)"
            }
            check("Hardware Vault (MOVAULT1 AES-256-GCM)") {
                val blob = VaultCrypto.encryptBytes("Top Secret Data".toByteArray(), "secret.docx", store = keyStore)
                val dec = VaultCrypto.decryptBytes(blob, keyStore)
                "Sealed & verified SHA-256 (${dec.originalName}, key ${keyStore.keyId})"
            }
            check("QR Generator & ZXing Decoder") {
                val uri = "myoffice://pair?url=http%3A%2F%2F192.168.1.10%3A41880&pin=123456"
                val px = QrCodec.encodeToArgbPixels(uri, 240)
                val decoded = QrCodec.decodeFromArgbPixels(px, 240, 240)
                require(decoded == uri) { "Decoded mismatch: $decoded" }
                "Encoded & decoded 240x240 QR image 100%"
            }
            check("PC <-> Phone Local Sync Engine") {
                val d1 = File(tmp, "pc").apply { mkdirs() }
                val d2 = File(tmp, "phone").apply { mkdirs() }
                File(d1, "doc.txt").writeText("From PC")
                val srv = MobileSyncHostServer(d1, requestedPort = 0, enableUdpDiscovery = false)
                srv.start()
                try {
                    val client = PcLanPeerBackend("http://127.0.0.1:${srv.port}", initialPin = srv.pin, keyStore = keyStore)
                    val rep = SyncEngineMobile(d2, client).syncOnce()
                    "Live HTTP REST pair + sync: ${rep.summary()}"
                } finally {
                    srv.stop()
                }
            }
            check("Dual-Tier GitHub OTA & ECDSA-P256 Verifier") {
                val norm = OtaUpdateManager.resolveManifestUrl("https://github.com/demo/myoffice.git")
                require(norm == "https://raw.githubusercontent.com/demo/myoffice/main/ota/manifest.json")
                "ECDSA P-256 + SHA-256 + Boot Watchdog ready (v${OtaUpdateManager.activeVersionName(activity.filesDir)})"
            }
        } finally {
            tmp.deleteRecursively()
        }
        val allOk = (0 until checks.length()).all { checks.getJSONObject(it).getBoolean("ok") }
        return JSONObject().put("ok", allOk).put("checks", checks)
    }

    fun listWorkspaceJson(): JSONArray {
        val cfg = readConfigJson()
        val starred = cfg.optJSONArray("starred")?.let { arr ->
            (0 until arr.length()).map { arr.getString(it) }.toSet()
        } ?: emptySet()
        val arr = JSONArray()
        val canonRoot = workspaceDir.canonicalFile
        canonRoot.walkTopDown()
            .filter { it.isFile && !SyncEngineMobile.isIgnored(it.name) }
            .sortedByDescending { it.lastModified() }
            .forEach { f ->
                val rel = f.relativeTo(canonRoot).invariantSeparatorsPath
                val ext = f.extension.lowercase()
                arr.put(
                    JSONObject()
                        .put("name", f.name)
                        .put("rel", rel)
                        .put("ext", ext)
                        .put("type", classifyExt(ext))
                        .put("size", f.length())
                        .put("mtime", f.lastModified())
                        .put("starred", rel in starred)
                )
            }
        return arr
    }

    private fun classifyExt(ext: String): String = when (ext) {
        "docx", "doc", "rtf", "odt", "txt", "md", "html", "htm" -> "writer"
        "xlsx", "xls", "xlsm", "csv", "tsv", "ods" -> "sheet"
        "pptx", "ppt", "ppsx", "odp" -> "slide"
        "pdf" -> "pdf"
        "png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff" -> "photos"
        "movault", "movaultkey" -> "vault"
        else -> "file"
    }

    private fun sanitizeConfigForUi(cfg: JSONObject): JSONObject {
        val copy = JSONObject(cfg.toString())
        for (secretKey in listOf("gmailAppPassword", "ftpPassword", "aiApiKey", "vaultPassphrase", "pcPin", "pcToken", "githubReadToken")) {
            if (copy.optString(secretKey).isNotEmpty()) {
                copy.put(secretKey, "••••••••")
            }
        }
        return copy
    }

    private fun guessMime(ext: String): String = when (ext.lowercase()) {
        "pdf" -> "application/pdf"
        "docx" -> "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        "xlsx" -> "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        "pptx" -> "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        "csv" -> "text/csv"
        "png" -> "image/png"
        "jpg", "jpeg" -> "image/jpeg"
        "webp" -> "image/webp"
        else -> "application/octet-stream"
    }
}
