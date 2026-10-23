package com.myoffice.app.qr

import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.EncodeHintType
import com.google.zxing.MultiFormatReader
import com.google.zxing.RGBLuminanceSource
import com.google.zxing.common.HybridBinarizer
import com.google.zxing.qrcode.QRCodeWriter
import com.google.zxing.qrcode.decoder.ErrorCorrectionLevel

/**
 * Pure Kotlin QR Code generator & decoder powered by ZXing:
 *  - Encodes `myoffice://pair?...` and `http://ip:port/f/<room>` QR codes as crisp SVG / ARGB pixel matrices
 *  - Decodes QR payloads directly from ARGB pixel arrays (works identically on Android `Bitmap` & JVM tests!)
 */
object QrCodec {
    fun encodeToSvg(text: String, marginModules: Int = 2): String {
        val hints = mapOf(
            EncodeHintType.CHARACTER_SET to "UTF-8",
            EncodeHintType.ERROR_CORRECTION to ErrorCorrectionLevel.M,
            EncodeHintType.MARGIN to marginModules
        )
        val matrix = QRCodeWriter().encode(text, BarcodeFormat.QR_CODE, 0, 0, hints)
        val w = matrix.width
        val h = matrix.height
        val sb = StringBuilder(w * h * 4 + 256)
        sb.append("""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 $w $h" shape-rendering="crispEdges">""")
        sb.append("""<rect width="$w" height="$h" fill="#FFFFFF"/>""")
        sb.append("""<path fill="#0F172A" d="""")
        for (y in 0 until h) {
            for (x in 0 until w) {
                if (matrix.get(x, y)) {
                    sb.append("M${x},${y}h1v1h-1z")
                }
            }
        }
        sb.append(""""/></svg>""")
        return sb.toString()
    }

    fun encodeToArgbPixels(text: String, sizePx: Int = 320): IntArray {
        val hints = mapOf(
            EncodeHintType.CHARACTER_SET to "UTF-8",
            EncodeHintType.ERROR_CORRECTION to ErrorCorrectionLevel.M,
            EncodeHintType.MARGIN to 2
        )
        val matrix = QRCodeWriter().encode(text, BarcodeFormat.QR_CODE, sizePx, sizePx, hints)
        val pixels = IntArray(sizePx * sizePx)
        val dark = 0xFF0F172A.toInt()
        val light = 0xFFFFFFFF.toInt()
        for (y in 0 until sizePx) {
            val offset = y * sizePx
            for (x in 0 until sizePx) {
                pixels[offset + x] = if (matrix.get(x, y)) dark else light
            }
        }
        return pixels
    }

    fun decodeFromArgbPixels(pixels: IntArray, width: Int, height: Int): String? {
        return try {
            val source = RGBLuminanceSource(width, height, pixels)
            val bitmap = BinaryBitmap(HybridBinarizer(source))
            val hints = mapOf(
                DecodeHintType.TRY_HARDER to true,
                DecodeHintType.CHARACTER_SET to "UTF-8",
                DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE)
            )
            MultiFormatReader().decode(bitmap, hints).text
        } catch (_: Exception) {
            null
        }
    }
}
