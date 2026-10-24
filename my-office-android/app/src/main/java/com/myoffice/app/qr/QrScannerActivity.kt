package com.myoffice.app.qr

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Size
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import android.widget.ImageButton
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.FocusMeteringAction
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import com.myoffice.app.R
import org.json.JSONObject
import java.io.File
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import kotlin.math.max
import kotlin.math.min

/**
 * In-app QR code scanner.
 *
 * The old implementation simply launched the *system camera* (`ACTION_IMAGE_CAPTURE`) and tried to read a
 * QR code from the resulting JPEG, which is why every "Scan QR" button in the app opened a normal photo
 * camera and usually produced nothing.  This screen streams the camera through CameraX, decodes the live
 * frames with ZXing (Y plane only - no RGB conversion, so it keeps ~8-10 frames per second on mid-range
 * phones) and returns the payload to the WebView.
 *
 * Features: viewfinder with scan window, torch, pinch-to-zoom, tap-to-focus, instant result feedback,
 * and "pick a picture instead" for QR codes that arrive as images.
 */
class QrScannerActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_RESULT_TEXT = "qr_result_text"
        private const val FRAME_INTERVAL_MS = 85L
    }

    private lateinit var previewView: PreviewView
    private lateinit var overlay: ScanFrameOverlay
    private lateinit var hintText: TextView
    private lateinit var torchButton: ImageButton

    private val analyzerExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    private var cameraProvider: ProcessCameraProvider? = null
    private var camera: Camera? = null
    private var torchOn = false
    private var lang = "en"

    @Volatile
    private var delivered = false
    private var lastAnalyseAt = 0L

    private val permissionLauncher = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) {
            startCamera()
        } else {
            showCameraUnavailable(permissionDenied = true)
        }
    }

    private val galleryLauncher = registerForActivityResult(ActivityResultContracts.GetContent()) { uri: Uri? ->
        if (uri != null) readPicture(uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        window.statusBarColor = Color.TRANSPARENT
        window.navigationBarColor = Color.TRANSPARENT
        setContentView(R.layout.activity_qr_scanner)

        previewView = findViewById(R.id.qrPreview)
        overlay = findViewById(R.id.qrOverlay)
        hintText = findViewById(R.id.qrHint)
        torchButton = findViewById(R.id.qrTorch)

        applyLanguage()
        applyWindowInsets()

        findViewById<View>(R.id.qrClose).setOnClickListener { finishWithResult(null) }
        findViewById<View>(R.id.qrGallery).setOnClickListener { galleryLauncher.launch("image/*") }
        torchButton.setOnClickListener { toggleTorch() }
        setupGestures()

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            startCamera()
        } else {
            permissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    /**
     * Android 15 forces edge-to-edge for targetSdk 35, so the close / torch buttons would otherwise sit
     * under the status bar on phones with a cut-out (e.g. Infinix X6885). Pad with the real insets.
     */
    private fun applyWindowInsets() {
        val density = resources.displayMetrics.density
        val baseTop = (12 * density).toInt()
        val baseBottom = (30 * density).toInt()
        val topBar = findViewById<View>(R.id.qrTopBar)
        val bottomBar = findViewById<View>(R.id.qrBottomBar)

        ViewCompat.setOnApplyWindowInsetsListener(topBar) { view, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout()
            )
            view.updatePadding(top = baseTop + bars.top, left = view.paddingLeft + 0, right = view.paddingRight)
            insets
        }
        ViewCompat.setOnApplyWindowInsetsListener(bottomBar) { view, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout()
            )
            view.updatePadding(bottom = baseBottom + bars.bottom)
            insets
        }
        ViewCompat.requestApplyInsets(topBar)
    }

    // ------------------------------------------------------------------ language

    /** The WebView stores the UI language in `mobile_config.json`; reuse it for the native screen. */
    private fun applyLanguage() {
        lang = runCatching {
            val file = File(filesDir, "mobile_config.json")
            if (file.isFile) JSONObject(file.readText(Charsets.UTF_8)).optString("lang", "en") else "en"
        }.getOrDefault("en")

        val (title, hint) = when {
            lang.startsWith("ur") -> "کیو آر کوڈ اسکین کریں" to "کوڈ کو دائرے کے اندر رکھیں، خود بخود اسکین ہو جائے گا"
            lang.startsWith("hi") -> "क्यूआर कोड स्कैन करें" to "कोड को फ़्रेम के अंदर रखें, अपने आप स्कैन होगा"
            else -> "Scan QR code" to "Place the code inside the frame - it is read automatically"
        }
        (findViewById<TextView>(R.id.qrTitle)).text = title
        hintText.text = hint
    }

    /** Runtime status lines, in the language chosen in the app (en / ur / hi). */
    private fun msg(key: String): String {
        val ur = lang.startsWith("ur")
        val hi = lang.startsWith("hi")
        return when (key) {
            "found" -> when {
                ur -> "کیو آر کوڈ پڑھ لیا گیا"
                hi -> "क्यूआर कोड पढ़ लिया गया"
                else -> "QR code read"
            }
            "reading" -> when {
                ur -> "تصویر پڑھی جا رہی ہے…"
                hi -> "तस्वीर पढ़ी जा रही है…"
                else -> "Reading picture…"
            }
            "noQr" -> when {
                ur -> "اس تصویر میں کوئی کیو آر کوڈ نہیں ملا — دوسری تصویر چنیں یا کیمرے سے اسکین کریں"
                hi -> "इस तस्वीर में कोई क्यूआर कोड नहीं मिला — दूसरी तस्वीर चुनें या कैमरे से स्कैन करें"
                else -> "No QR code in that picture — try another one, or scan live"
            }
            "denied" -> when {
                ur -> "کیمرے کی اجازت بند ہے۔ سیٹنگز سے اجازت دیں، یا کیو آر کوڈ کی تصویر چنیں۔"
                hi -> "कैमरा अनुमति बंद है। सेटिंग्स से अनुमति दें, या क्यूआर कोड की तस्वीर चुनें।"
                else -> "Camera permission is off. Allow it in Settings, or pick a picture of the QR code."
            }
            "noCam" -> when {
                ur -> "اس ڈیوائس پر کیمرہ دستیاب نہیں — کیو آر کوڈ کی تصویر چنیں"
                hi -> "इस डिवाइस पर कैमरा उपलब्ध नहीं — क्यूआर कोड की तस्वीर चुनें"
                else -> "No camera available on this device — pick a picture of the QR code instead."
            }
            else -> key
        }
    }

    // ------------------------------------------------------------------ camera

    private fun startCamera() {
        if (!packageManager.hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY)) {
            showCameraUnavailable(permissionDenied = false)
            return
        }
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            val provider = runCatching { future.get() }.getOrNull()
            if (provider == null) {
                showCameraUnavailable(permissionDenied = false)
                return@addListener
            }
            cameraProvider = provider
            bindUseCases(provider)
        }, ContextCompat.getMainExecutor(this))
    }

    private fun bindUseCases(provider: ProcessCameraProvider) {
        val preview = Preview.Builder()
            .build()
            .also { it.setSurfaceProvider(previewView.surfaceProvider) }

        val resolution = ResolutionSelector.Builder()
            .setResolutionStrategy(
                ResolutionStrategy(
                    Size(1280, 720),
                    ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER
                )
            )
            .build()

        val analysis = ImageAnalysis.Builder()
            .setResolutionSelector(resolution)
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
            .build()
        analysis.setAnalyzer(analyzerExecutor) { proxy -> analyseFrame(proxy) }

        val selector = runCatching {
            if (provider.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA)) {
                CameraSelector.DEFAULT_BACK_CAMERA
            } else {
                CameraSelector.DEFAULT_FRONT_CAMERA
            }
        }.getOrDefault(CameraSelector.DEFAULT_BACK_CAMERA)

        val bound = runCatching {
            provider.unbindAll()
            provider.bindToLifecycle(this, selector, preview, analysis)
        }.getOrNull()

        if (bound == null) {
            showCameraUnavailable(permissionDenied = false)
            return
        }
        camera = bound
        previewView.scaleType = PreviewView.ScaleType.FILL_CENTER
        hintText.alpha = 1f
    }

    /**
     * Runs on the analyser thread: reads the luminance plane straight out of the YUV frame, rotates it to
     * the display orientation and lets ZXing look for a QR code. Throttled so a busy phone never queues
     * frames faster than it can decode them.
     */
    private fun analyseFrame(image: ImageProxy) {
        try {
            if (delivered) return
            val now = SystemClock.elapsedRealtime()
            if (now - lastAnalyseAt < FRAME_INTERVAL_MS) return
            lastAnalyseAt = now

            val luma = extractLuma(image) ?: return
            val text = QrImageDecoder.decodeLuma(luma.data, luma.w, luma.h, image.imageInfo.rotationDegrees)
            if (text.isNullOrBlank()) return

            delivered = true
            runOnUiThread { onQrFound(text) }
        } catch (_: Throwable) {
            // a broken frame must never take the preview down
        } finally {
            image.close()
        }
    }

    private class Luma(val data: ByteArray, val w: Int, val h: Int)

    private fun extractLuma(image: ImageProxy): Luma? {
        val plane = image.planes.firstOrNull() ?: return null
        val buffer = plane.buffer
        val width = image.width
        val height = image.height
        if (width <= 0 || height <= 0) return null
        val rowStride = plane.rowStride
        val pixelStride = max(1, plane.pixelStride)
        val total = buffer.remaining()
        val data = ByteArray(width * height)

        if (pixelStride == 1 && rowStride == width) {
            if (total < width * height) return null
            buffer.rewind()
            buffer.get(data, 0, width * height)
            return Luma(data, width, height)
        }

        val rowBuffer = ByteArray(rowStride)
        buffer.rewind()
        for (y in 0 until height) {
            val rowStart = y * rowStride
            if (rowStart >= total) break
            val toRead = min(rowStride, total - rowStart)
            buffer.position(rowStart)
            buffer.get(rowBuffer, 0, toRead)
            var dst = y * width
            var x = 0
            while (x < width) {
                data[dst++] = rowBuffer[x * pixelStride]
                x++
            }
        }
        return Luma(data, width, height)
    }

    // ------------------------------------------------------------------ interaction

    @SuppressLint("ClickableViewAccessibility")
    private fun setupGestures() {
        val scaleDetector = ScaleGestureDetector(this, object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
            override fun onScale(detector: ScaleGestureDetector): Boolean {
                val cam = camera ?: return false
                val state = cam.cameraInfo.zoomState.value ?: return false
                val ratio = (state.zoomRatio * detector.scaleFactor)
                    .coerceIn(state.minZoomRatio, state.maxZoomRatio)
                cam.cameraControl.setZoomRatio(ratio)
                return true
            }
        })
        val tapDetector = GestureDetector(this, object : GestureDetector.SimpleOnGestureListener() {
            override fun onSingleTapUp(event: MotionEvent): Boolean {
                val cam = camera ?: return false
                runCatching {
                    val point = previewView.meteringPointFactory.createPoint(event.x, event.y)
                    cam.cameraControl.startFocusAndMetering(
                        FocusMeteringAction.Builder(point, FocusMeteringAction.FLAG_AF).build()
                    )
                }
                return true
            }
        })
        previewView.setOnTouchListener { _, event ->
            scaleDetector.onTouchEvent(event)
            tapDetector.onTouchEvent(event)
            true
        }
    }

    private fun toggleTorch() {
        val cam = camera ?: return
        val control = cam.cameraControl
        val hasFlash = cam.cameraInfo.hasFlashUnit()
        if (!hasFlash) {
            torchButton.isEnabled = false
            torchButton.alpha = 0.35f
            return
        }
        torchOn = !torchOn
        control.enableTorch(torchOn)
        torchButton.setImageResource(if (torchOn) R.drawable.ic_qr_torch_on else R.drawable.ic_qr_torch_off)
    }

    private fun onQrFound(text: String) {
        overlay.state = ScanFrameOverlay.State.FOUND
        hintText.text = msg("found")
        vibrate()
        previewView.postDelayed({ finishWithResult(text) }, 200)
    }

    private fun vibrate() {
        runCatching {
            val vibrator = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                (getSystemService(VIBRATOR_MANAGER_SERVICE) as VibratorManager).defaultVibrator
            } else {
                @Suppress("DEPRECATION")
                getSystemService(VIBRATOR_SERVICE) as Vibrator
            }
            if (vibrator.hasVibrator()) {
                vibrator.vibrate(VibrationEffect.createOneShot(45, VibrationEffect.DEFAULT_AMPLITUDE))
            }
        }
    }

    // ------------------------------------------------------------------ gallery fallback

    private fun readPicture(uri: Uri) {
        hintText.text = msg("reading")
        Thread {
            val text = QrGallery.decode(contentResolver, uri)
            runOnUiThread {
                if (text.isNullOrBlank()) {
                    hintText.text = msg("noQr")
                } else {
                    overlay.state = ScanFrameOverlay.State.FOUND
                    finishWithResult(text)
                }
            }
        }.start()
    }

    // ------------------------------------------------------------------ helpers

    private fun showCameraUnavailable(permissionDenied: Boolean) {
        overlay.visibility = View.GONE
        hintText.text = msg(if (permissionDenied) "denied" else "noCam")
        torchButton.isEnabled = false
        torchButton.alpha = 0.35f
    }

    private fun finishWithResult(text: String?) {
        val data = Intent()
        if (text != null) data.putExtra(EXTRA_RESULT_TEXT, text)
        setResult(if (text != null) RESULT_OK else RESULT_CANCELED, data)
        finish()
    }

    override fun onDestroy() {
        runCatching { cameraProvider?.unbindAll() }
        runCatching { analyzerExecutor.shutdownNow() }
        super.onDestroy()
    }
}
