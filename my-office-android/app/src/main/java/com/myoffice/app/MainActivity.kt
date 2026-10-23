package com.myoffice.app

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.OpenableColumns
import android.provider.Settings
import android.view.ViewGroup
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.myoffice.app.bridge.AndroidBridge
import com.myoffice.app.qr.QrCodec
import com.myoffice.app.update.OtaUpdateManager
import org.json.JSONObject
import java.io.File

class MainActivity : AppCompatActivity() {

    lateinit var rootContainer: FrameLayout
        private set
    lateinit var webView: WebView
        private set
    private lateinit var bridge: AndroidBridge

    @Volatile
    private var uiReady = false
    private val pendingDeepEvents = mutableListOf<Pair<String, String>>()
    private var pendingWebFileCallback: ValueCallback<Array<Uri>>? = null
    private var pendingCameraFile: File? = null
    private var pendingCameraForQr: Boolean = false
    private var pendingApkToInstall: File? = null
    private val mainHandler = Handler(Looper.getMainLooper())
    private val bootWatchdogRunnable = Runnable {
        val fallbackUrl = OtaUpdateManager.rollbackToSafeSlot(filesDir, "boot_watchdog_timeout")
        webView.clearCache(true)
        webView.loadUrl(fallbackUrl)
    }

    companion object {
        private val LIGHT_CHROME_BG = Color.parseColor("#FFFFFF")
        private val DARK_CHROME_BG = Color.parseColor("#161615")
    }

    private val installApkPermissionLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        val apk = pendingApkToInstall
        if (apk != null && apk.isFile && packageManager.canRequestPackageInstalls()) {
            pendingApkToInstall = null
            launchSystemApkInstallerNow(apk)
        }
    }

    private val importFileLauncher = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            importExternalUriToWorkspace(uri, autoOpen = true)
        }
    }

    private val webFileChooserLauncher = registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        val cb = pendingWebFileCallback
        pendingWebFileCallback = null
        cb?.onReceiveValue(if (uri != null) arrayOf(uri) else null)
    }

    private val qrImagePickerLauncher = registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) {
            decodeQrFromUri(uri)
        }
    }

    private val cameraTakePictureLauncher = registerForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        val photo = pendingCameraFile
        if (ok && photo != null && photo.isFile) {
            if (pendingCameraForQr) {
                val bmp = BitmapFactory.decodeFile(photo.absolutePath)
                photo.delete()
                if (bmp != null) {
                    decodeQrFromBitmap(bmp)
                    bmp.recycle()
                }
            } else {
                val dest = File(bridge.workspaceDir, "Scan_${System.currentTimeMillis() % 100000}.jpg")
                photo.copyTo(dest, overwrite = true)
                photo.delete()
                pushJsEvent(
                    "window.__onNativeFileImported",
                    JSONObject().put("rel", dest.name).put("autoOpen", true).toString()
                )
            }
        }
    }

    private val cameraPermissionLauncher = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startCameraIntentNow(pendingCameraForQr)
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Explicit edge-to-edge control across Android 8.0 .. Android 15+ (targetSdk 35)
        WindowCompat.setDecorFitsSystemWindows(window, false)

        bridge = AndroidBridge(this)

        webView = WebView(this).apply {
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
            isVerticalScrollBarEnabled = false
            overScrollMode = WebView.OVER_SCROLL_NEVER
            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                allowFileAccess = true
                allowContentAccess = true
                allowFileAccessFromFileURLs = true
                allowUniversalAccessFromFileURLs = true
                mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
                mediaPlaybackRequiresUserGesture = false
                setSupportZoom(false)
                builtInZoomControls = false
                displayZoomControls = false
                cacheMode = WebSettings.LOAD_NO_CACHE
            }
            addJavascriptInterface(bridge, "AndroidNative")
            webViewClient = object : WebViewClient() {
                override fun onPageFinished(view: WebView?, url: String?) {
                    super.onPageFinished(view, url)
                    uiReady = true
                    flushPendingEvents()
                }

                override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                    val u = request?.url ?: return false
                    if (u.scheme == "myoffice") {
                        handleMyOfficeDeepUri(u.toString())
                        return true
                    }
                    return false
                }
            }
            webChromeClient = object : WebChromeClient() {
                override fun onShowFileChooser(
                    webView: WebView?,
                    filePathCallback: ValueCallback<Array<Uri>>?,
                    fileChooserParams: FileChooserParams?
                ): Boolean {
                    pendingWebFileCallback?.onReceiveValue(null)
                    pendingWebFileCallback = filePathCallback
                    webFileChooserLauncher.launch("*/*")
                    return true
                }
            }
        }

        rootContainer = FrameLayout(this).apply {
            setBackgroundColor(LIGHT_CHROME_BG)
            addView(webView)
        }
        setContentView(rootContainer)

        // Consume status bar, navigation bar, camera punch-hole cutout, AND soft keyboard (IME) insets natively
        ViewCompat.setOnApplyWindowInsetsListener(rootContainer) { v, windowInsets ->
            val barsAndIme = windowInsets.getInsets(
                WindowInsetsCompat.Type.systemBars() or
                    WindowInsetsCompat.Type.displayCutout() or
                    WindowInsetsCompat.Type.ime()
            )
            v.setPadding(barsAndIme.left, barsAndIme.top, barsAndIme.right, barsAndIme.bottom)
            WindowInsetsCompat.CONSUMED
        }
        ViewCompat.requestApplyInsets(rootContainer)
        applySystemBarAppearance(isDark = false)

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                webView.evaluateJavascript("window.__handleAndroidBack && window.__handleAndroidBack()") { res ->
                    if (res != "true") {
                        isEnabled = false
                        onBackPressedDispatcher.onBackPressed()
                        isEnabled = true
                    }
                }
            }
        })

        loadActiveEntryWithWatchdog()
        intent?.let { handleIncomingIntent(it) }
    }

    fun loadActiveEntryWithWatchdog() {
        runOnUiThread {
            mainHandler.removeCallbacks(bootWatchdogRunnable)
            val (entryUrl, armedWatchdog) = OtaUpdateManager.resolveStartupEntryUrl(filesDir)
            if (armedWatchdog) {
                mainHandler.postDelayed(bootWatchdogRunnable, 4_500L)
            }
            webView.clearCache(true)
            webView.loadUrl(entryUrl)
        }
    }

    fun confirmUiBootHealthy() {
        mainHandler.removeCallbacks(bootWatchdogRunnable)
        OtaUpdateManager.markBootHealthy(filesDir)
    }

    fun installDownloadedApk(apkFile: File) {
        runOnUiThread {
            if (!packageManager.canRequestPackageInstalls()) {
                pendingApkToInstall = apkFile
                val settingsIntent = Intent(
                    Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:$packageName")
                )
                installApkPermissionLauncher.launch(settingsIntent)
            } else {
                launchSystemApkInstallerNow(apkFile)
            }
        }
    }

    private fun launchSystemApkInstallerNow(apkFile: File) {
        val contentUri = FileProvider.getUriForFile(this, "${packageName}.fileprovider", apkFile)
        val installIntent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(contentUri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        startActivity(installIntent)
    }

    fun applySystemBarAppearance(isDark: Boolean) {
        runOnUiThread {
            val bg = if (isDark) DARK_CHROME_BG else LIGHT_CHROME_BG
            rootContainer.setBackgroundColor(bg)
            webView.setBackgroundColor(bg)
            if (Build.VERSION.SDK_INT < 35) {
                @Suppress("DEPRECATION")
                window.statusBarColor = bg
                @Suppress("DEPRECATION")
                window.navigationBarColor = bg
            }
            WindowInsetsControllerCompat(window, window.decorView).apply {
                isAppearanceLightStatusBars = !isDark
                isAppearanceLightNavigationBars = !isDark
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIncomingIntent(intent)
    }

    private fun handleIncomingIntent(intent: Intent) {
        val dataUri = intent.data
        if (dataUri != null) {
            if (dataUri.scheme.equals("myoffice", ignoreCase = true)) {
                handleMyOfficeDeepUri(dataUri.toString())
                return
            }
            importExternalUriToWorkspace(dataUri, autoOpen = true)
            return
        }
        if (intent.action == Intent.ACTION_SEND) {
            val stream = if (Build.VERSION.SDK_INT >= 33) {
                intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
            } else {
                @Suppress("DEPRECATION")
                intent.getParcelableExtra(Intent.EXTRA_STREAM)
            }
            if (stream != null) {
                importExternalUriToWorkspace(stream, autoOpen = true)
            }
        }
    }

    private fun handleMyOfficeDeepUri(uriStr: String) {
        pushJsEvent("window.__onNativeDeepLink", JSONObject().put("uri", uriStr).toString())
    }

    private fun importExternalUriToWorkspace(uri: Uri, autoOpen: Boolean) {
        Thread {
            runCatching {
                val displayName = queryDisplayName(uri) ?: "Imported_${System.currentTimeMillis() % 10000}.bin"
                val safeName = displayName.replace(Regex("[^A-Za-z0-9._()\\- ]"), "_")
                val dest = File(bridge.workspaceDir, safeName)
                contentResolver.openInputStream(uri)?.use { input ->
                    dest.outputStream().use { out -> input.copyTo(out) }
                }
                pushJsEvent(
                    "window.__onNativeFileImported",
                    JSONObject().put("rel", dest.name).put("autoOpen", autoOpen).toString()
                )
            }
        }.start()
    }

    private fun decodeQrFromUri(uri: Uri) {
        Thread {
            val bmp = runCatching {
                contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it) }
            }.getOrNull()
            if (bmp != null) {
                decodeQrFromBitmap(bmp)
                bmp.recycle()
            } else {
                pushJsEvent("window.__onNativeQrScanned", JSONObject().put("ok", false).toString())
            }
        }.start()
    }

    private fun decodeQrFromBitmap(bmp: Bitmap) {
        val w = bmp.width
        val h = bmp.height
        val pixels = IntArray(w * h)
        bmp.getPixels(pixels, 0, w, 0, 0, w, h)
        val text = QrCodec.decodeFromArgbPixels(pixels, w, h)
        val payload = if (text != null) {
            JSONObject().put("ok", true).put("text", text)
        } else {
            JSONObject().put("ok", false)
        }
        pushJsEvent("window.__onNativeQrScanned", payload.toString())
    }

    private fun queryDisplayName(uri: Uri): String? {
        contentResolver.query(uri, null, null, null, null)?.use { c ->
            val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (idx >= 0 && c.moveToFirst()) return c.getString(idx)
        }
        return uri.lastPathSegment?.substringAfterLast('/')
    }

    fun launchImportFilePicker() {
        runOnUiThread { importFileLauncher.launch(arrayOf("*/*")) }
    }

    fun launchQrImagePicker() {
        runOnUiThread { qrImagePickerLauncher.launch("image/*") }
    }

    fun launchCameraCapture(forQr: Boolean) {
        runOnUiThread {
            pendingCameraForQr = forQr
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
                startCameraIntentNow(forQr)
            } else {
                cameraPermissionLauncher.launch(Manifest.permission.CAMERA)
            }
        }
    }

    private fun startCameraIntentNow(forQr: Boolean) {
        val cacheScans = File(cacheDir, "scans").apply { mkdirs() }
        val tmp = File(cacheScans, if (forQr) "qr_capture.jpg" else "doc_capture.jpg")
        pendingCameraFile = tmp
        val uri = FileProvider.getUriForFile(this, "${packageName}.fileprovider", tmp)
        cameraTakePictureLauncher.launch(uri)
    }

    fun resolveJsPromise(reqId: String, jsonPayload: String) {
        val qReq = JSONObject.quote(reqId)
        val qJson = JSONObject.quote(jsonPayload)
        runOnUiThread {
            webView.evaluateJavascript("window.__resolveNativeCall && window.__resolveNativeCall($qReq, $qJson);", null)
        }
    }

    fun pushJsEvent(fnExpr: String, jsonPayload: String) {
        synchronized(pendingDeepEvents) {
            if (!uiReady) {
                pendingDeepEvents.add(fnExpr to jsonPayload)
                return
            }
        }
        val qJson = JSONObject.quote(jsonPayload)
        runOnUiThread {
            webView.evaluateJavascript("$fnExpr && $fnExpr($qJson);", null)
        }
    }

    private fun flushPendingEvents() {
        val copy: List<Pair<String, String>>
        synchronized(pendingDeepEvents) {
            copy = pendingDeepEvents.toList()
            pendingDeepEvents.clear()
        }
        copy.forEach { (fn, payload) -> pushJsEvent(fn, payload) }
    }

    override fun onDestroy() {
        bridge.shutdown()
        super.onDestroy()
    }
}
