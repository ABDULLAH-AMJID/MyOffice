package com.myoffice.app.qr

import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.MultiFormatReader
import com.google.zxing.PlanarYUVLuminanceSource
import com.google.zxing.RGBLuminanceSource
import com.google.zxing.common.HybridBinarizer
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Robust QR decoding for *still* pictures (gallery picks, screenshots, shared images) and for live camera
 * frames, shared by `QrScannerActivity` and `MainActivity`.
 *
 * A single ZXing pass on a phone photo fails most of the time, for three reasons:
 *  - a 12 MP camera picture is 4000x3000; the QR occupies a small part of the frame and its modules land on
 *    fractional pixels, so binarisation smears them;
 *  - photos are frequently rotated (EXIF is not applied by `BitmapFactory`);
 *  - low-light or dark-mode pictures are *inverted* (light modules on a dark background).
 *
 * So this decoder normalises the picture to a workable size and then walks a strategy ladder - upright,
 * inverted, the three rotations, centre crops at several zoom levels, and a 2x upscale for tiny images -
 * until ZXing returns a payload. Every step is pure Kotlin on an ARGB array, which keeps it unit-testable
 * on the JVM (no `android.graphics` dependency).
 */
object QrImageDecoder {

    /** Longest edge kept while decoding; bigger images are box-filtered down (memory + speed). */
    private const val MAX_EDGE = 1600
    /** Anything below this is too small for one QR module to survive, so it is upscaled instead. */
    private const val MIN_EDGE = 320

    /** Everything the decoder works on: ARGB pixels plus their dimensions. */
    internal class Frame(val px: IntArray, val w: Int, val h: Int) {
        val maxEdge: Int get() = max(w, h)
    }

    // ------------------------------------------------------------------ still images (ARGB pixels)

    /**
     * Decode a QR code from an ARGB pixel array - the entry point used for gallery / shared pictures.
     *
     * @return the payload, or `null` when no QR code could be read.
     */
    fun decode(pixels: IntArray, width: Int, height: Int): String? {
        if (width < 16 || height < 16 || pixels.size < width * height) return null
        return try {
            decodeFrame(normalise(Frame(pixels, width, height)))
        } catch (_: Throwable) {
            null   // includes OutOfMemoryError: a huge picture must never crash the scan flow
        }
    }

    /**
     * Decode one camera frame. `luma` is the Y plane of a `YUV_420_888` image (one byte per pixel),
     * `rotationDegrees` is `ImageProxy.imageInfo.rotationDegrees`.
     */
    fun decodeLuma(luma: ByteArray, width: Int, height: Int, rotationDegrees: Int): String? {
        if (width < 16 || height < 16 || luma.size < width * height) return null
        return try {
            val upright = rotateLuma(luma, width, height, rotationDegrees)
            decodePlanar(upright.data, upright.w, upright.h)
                ?: decodePlanar(invertLuma(upright.data), upright.w, upright.h)
                ?: run {
                    // A code held away from the phone is a small patch of the frame: look at the middle
                    // of the preview too (that is where the viewfinder window tells the user to aim).
                    for (fraction in doubleArrayOf(0.62, 0.42)) {
                        val crop = cropLuma(upright, fraction)
                        if (crop.w < 48 || crop.h < 48) continue
                        decodePlanar(crop.data, crop.w, crop.h)?.let { return@run it }
                        decodePlanar(invertLuma(crop.data), crop.w, crop.h)?.let { return@run it }
                    }
                    null
                }
        } catch (_: Throwable) {
            null
        }
    }

    // ------------------------------------------------------------------ strategy ladder

    private fun decodeFrame(input: Frame): String? {
        val base = normalise(input)

        decodeArgb(base)?.let { return it }
        decodeArgb(invert(base))?.let { return it }
        for (degrees in intArrayOf(90, 180, 270)) {
            decodeArgb(rotateArgb(base, degrees))?.let { return it }
        }

        // A QR code photographed on a poster, a screen or a whole page is usually a small patch in the
        // middle: crop towards the centre and retry, inverted too (dark mode screenshots).
        for (fraction in doubleArrayOf(0.74, 0.52, 0.36)) {
            val crop = cropCentered(base, fraction)
            if (crop.w < 48 || crop.h < 48) continue
            decodeArgb(crop)?.let { return it }
            decodeArgb(invert(crop))?.let { return it }
            decodeArgb(rotateArgb(crop, 90))?.let { return it }
            decodeArgb(rotateArgb(crop, 270))?.let { return it }
        }

        // Tiny QR codes (icons, stickers, a QR inside a chat bubble) need more pixels than they have.
        if (base.maxEdge < 640) {
            val up = scaleTo(base, base.w * 2, base.h * 2)
            decodeArgb(up)?.let { return it }
            decodeArgb(invert(up))?.let { return it }
        }
        return null
    }

    private fun decodeArgb(frame: Frame): String? = try {
        read(RGBLuminanceSource(frame.w, frame.h, frame.px))
    } catch (_: Throwable) {
        null
    }

    private fun decodePlanar(luma: ByteArray, width: Int, height: Int): String? = try {
        read(PlanarYUVLuminanceSource(luma, width, height, 0, 0, width, height, false))
    } catch (_: Throwable) {
        null
    }

    private fun read(source: com.google.zxing.LuminanceSource): String? {
        val hints = mapOf(
            DecodeHintType.TRY_HARDER to true,
            DecodeHintType.CHARACTER_SET to "UTF-8",
            DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE)
        )
        val reader = MultiFormatReader()
        return try {
            reader.decode(BinaryBitmap(HybridBinarizer(source)), hints)?.text
        } catch (_: Throwable) {
            null
        } finally {
            reader.reset()
        }
    }

    // ------------------------------------------------------------------ pixel helpers

    /** Bring the picture into the [MIN_EDGE]..[MAX_EDGE] working range. */
    private fun normalise(input: Frame): Frame = when {
        input.maxEdge > MAX_EDGE -> {
            val ratio = MAX_EDGE.toDouble() / input.maxEdge
            scaleTo(input, max(16, (input.w * ratio).roundToInt()), max(16, (input.h * ratio).roundToInt()))
        }
        input.maxEdge < MIN_EDGE -> scaleTo(input, input.w * 2, input.h * 2)
        else -> input
    }

    internal fun invert(frame: Frame): Frame {
        val out = IntArray(frame.px.size)
        for (i in out.indices) {
            val p = frame.px[i]
            out[i] = (p and 0xFF000000.toInt()) or (0x00FFFFFF and p.inv())
        }
        return Frame(out, frame.w, frame.h)
    }

    internal fun rotateArgb(frame: Frame, degrees: Int): Frame {
        val turns = ((degrees % 360) + 360) % 360
        if (turns == 0) return frame
        val src = frame.px
        val w = frame.w
        val h = frame.h
        return when (turns) {
            90 -> {
                val out = IntArray(src.size)
                for (y in 0 until h) {
                    val row = y * w
                    for (x in 0 until w) {
                        out[x * h + (h - 1 - y)] = src[row + x]
                    }
                }
                Frame(out, h, w)
            }
            180 -> {
                val out = IntArray(src.size)
                val last = src.size - 1
                for (i in src.indices) out[last - i] = src[i]
                Frame(out, w, h)
            }
            else -> {   // 270
                val out = IntArray(src.size)
                for (y in 0 until h) {
                    val row = y * w
                    for (x in 0 until w) {
                        out[(w - 1 - x) * h + y] = src[row + x]
                    }
                }
                Frame(out, h, w)
            }
        }
    }

    internal fun cropCentered(frame: Frame, fraction: Double): Frame {
        val cw = max(16, (frame.w * fraction).roundToInt())
        val ch = max(16, (frame.h * fraction).roundToInt())
        val left = (frame.w - cw) / 2
        val top = (frame.h - ch) / 2
        val out = IntArray(cw * ch)
        for (y in 0 until ch) {
            System.arraycopy(frame.px, (top + y) * frame.w + left, out, y * cw, cw)
        }
        return Frame(out, cw, ch)
    }

    /**
     * Box-filter (area average) resample in both directions. Averaging - rather than nearest neighbour -
     * is what keeps a downscaled QR readable: it preserves the module's average darkness instead of
     * randomly keeping single pixels.
     */
    internal fun scaleTo(frame: Frame, dstW: Int, dstH: Int): Frame {
        val tw = max(1, dstW)
        val th = max(1, dstH)
        if (tw == frame.w && th == frame.h) return frame
        val src = frame.px
        val out = IntArray(tw * th)
        val sy = frame.h.toDouble() / th
        val sx = frame.w.toDouble() / tw
        for (y in 0 until th) {
            val y0 = min(frame.h - 1, (y * sy).toInt())
            val y1 = min(frame.h, max(y0 + 1, ((y + 1) * sy).toInt()))
            for (x in 0 until tw) {
                val x0 = min(frame.w - 1, (x * sx).toInt())
                val x1 = min(frame.w, max(x0 + 1, ((x + 1) * sx).toInt()))
                var r = 0
                var g = 0
                var b = 0
                var n = 0
                for (yy in y0 until y1) {
                    var idx = yy * frame.w + x0
                    for (xx in x0 until x1) {
                        val p = src[idx++]
                        r += (p shr 16) and 0xFF
                        g += (p shr 8) and 0xFF
                        b += p and 0xFF
                        n++
                    }
                }
                if (n == 0) n = 1
                out[y * tw + x] = (0xFF shl 24) or ((r / n) shl 16) or ((g / n) shl 8) or (b / n)
            }
        }
        return Frame(out, tw, th)
    }

    // ------------------------------------------------------------------ luma helpers (camera frames)

    private class Luma(val data: ByteArray, val w: Int, val h: Int)

    private fun rotateLuma(luma: ByteArray, width: Int, height: Int, degrees: Int): Luma {
        val turns = ((degrees % 360) + 360) % 360
        if (turns == 0) return Luma(luma, width, height)
        return when (turns) {
            90 -> {
                val out = ByteArray(luma.size)
                for (y in 0 until height) {
                    val row = y * width
                    for (x in 0 until width) out[x * height + (height - 1 - y)] = luma[row + x]
                }
                Luma(out, height, width)
            }
            180 -> {
                val out = ByteArray(luma.size)
                val last = luma.size - 1
                for (i in luma.indices) out[last - i] = luma[i]
                Luma(out, width, height)
            }
            else -> {
                val out = ByteArray(luma.size)
                for (y in 0 until height) {
                    val row = y * width
                    for (x in 0 until width) out[(width - 1 - x) * height + y] = luma[row + x]
                }
                Luma(out, height, width)
            }
        }
    }

    private fun cropLuma(src: Luma, fraction: Double): Luma {
        val cw = max(16, (src.w * fraction).roundToInt())
        val ch = max(16, (src.h * fraction).roundToInt())
        val left = (src.w - cw) / 2
        val top = (src.h - ch) / 2
        val out = ByteArray(cw * ch)
        for (y in 0 until ch) {
            System.arraycopy(src.data, (top + y) * src.w + left, out, y * cw, cw)
        }
        return Luma(out, cw, ch)
    }

    private fun invertLuma(luma: ByteArray): ByteArray {
        val out = ByteArray(luma.size)
        for (i in luma.indices) out[i] = (255 - (luma[i].toInt() and 0xFF)).toByte()
        return out
    }
}
