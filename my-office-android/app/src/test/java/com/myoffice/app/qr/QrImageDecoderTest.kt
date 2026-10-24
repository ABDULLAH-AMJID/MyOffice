package com.myoffice.app.qr

import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.qrcode.QRCodeWriter
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Host-side (JVM) proof that the QR readers really decode payloads:
 *  - `QrImageDecoder.decode` for gallery / shared pictures (large photo, rotated photo, dark-mode
 *    screenshot, tiny code, low contrast, off-centre placement);
 *  - `QrImageDecoder.decodeLuma` for live CameraX frames (Y-plane input, rotated frame).
 *
 * If any of these fail the phone would show a camera and never read the code - exactly the bug this
 * screen was written to fix - so they are kept as regression tests.
 */
class QrImageDecoderTest {

    private val payload = "myoffice://pair?host=192.168.1.24&port=4188&pin=482913&v=1.6.3"

    // ---------------------------------------------------------------- image builders

    /** Renders [content] as a crisp QR bitmap (module = [modulePx] px, 4-module quiet zone). */
    private fun qrImage(content: String, modulePx: Int): Frame {
        val matrix = QRCodeWriter().encode(
            content,
            BarcodeFormat.QR_CODE,
            21 * modulePx,   // any size; the writer only needs a hint, sharpness is fixed below
            21 * modulePx,
            mapOf(EncodeHintType.MARGIN to 0)
        )
        // Re-sample the returned matrix to exact module multiples so the modules stay crisp.
        val modules = matrix.width
        val quiet = 4
        val size = (modules + quiet * 2) * modulePx
        val px = IntArray(size * size) { WHITE }
        for (y in 0 until modules) {
            for (x in 0 until modules) {
                if (!matrix.get(x, y)) continue
                val left = (x + quiet) * modulePx
                val top = (y + quiet) * modulePx
                for (dy in 0 until modulePx) {
                    val row = (top + dy) * size + left
                    for (dx in 0 until modulePx) px[row + dx] = BLACK
                }
            }
        }
        return Frame(px, size, size)
    }

    /** Puts [code] onto a bigger white "page", simulating a photo where the code is a small patch. */
    private fun paste(pageW: Int, pageH: Int, code: Frame, left: Int, top: Int, background: Int = WHITE): Frame {
        val px = IntArray(pageW * pageH) { background }
        for (y in 0 until code.h) {
            System.arraycopy(code.px, y * code.w, px, (top + y) * pageW + left, code.w)
        }
        return Frame(px, pageW, pageH)
    }

    private fun rotate(frame: Frame, degrees: Int): Frame = when (degrees) {
        90 -> {
            val out = IntArray(frame.px.size)
            for (y in 0 until frame.h) for (x in 0 until frame.w) {
                out[x * frame.h + (frame.h - 1 - y)] = frame.px[y * frame.w + x]
            }
            Frame(out, frame.h, frame.w)
        }
        180 -> Frame(IntArray(frame.px.size) { frame.px[frame.px.size - 1 - it] }, frame.w, frame.h)
        else -> {
            val out = IntArray(frame.px.size)
            for (y in 0 until frame.h) for (x in 0 until frame.w) {
                out[(frame.w - 1 - x) * frame.h + y] = frame.px[y * frame.w + x]
            }
            Frame(out, frame.h, frame.w)
        }
    }

    private fun invertPixels(frame: Frame): Frame =
        Frame(IntArray(frame.px.size) { frame.px[it] xor 0x00FFFFFF }, frame.w, frame.h)

    /** Fades the ink (dark grey on off-white) - the "printed on cheap paper" case. */
    private fun lowContrast(frame: Frame): Frame = Frame(
        IntArray(frame.px.size) {
            val p = frame.px[it]
            if (p == BLACK) 0xFF4A4A52.toInt() else 0xFFEDEDF2.toInt()
        },
        frame.w,
        frame.h
    )

    /** Centre-cropped view, like a user who zoomed the gallery picture onto the code. */
    private fun crop(frame: Frame, fraction: Double): Frame {
        val cw = (frame.w * fraction).toInt()
        val ch = (frame.h * fraction).toInt()
        val left = (frame.w - cw) / 2
        val top = (frame.h - ch) / 2
        val out = IntArray(cw * ch)
        for (y in 0 until ch) {
            System.arraycopy(frame.px, (top + y) * frame.w + left, out, y * cw, cw)
        }
        return Frame(out, cw, ch)
    }

    private fun lumaFrom(frame: Frame): ByteArray =
        ByteArray(frame.w * frame.h) { i ->
            val p = frame.px[i]
            val r = (p shr 16) and 0xFF
            val g = (p shr 8) and 0xFF
            val b = p and 0xFF
            (((r * 77 + g * 150 + b * 29) shr 8) and 0xFF).toByte()
        }

    private class Frame(val px: IntArray, val w: Int, val h: Int)

    // ---------------------------------------------------------------- still pictures

    @Test
    fun readsQrFromLargePhonePhoto() {
        val code = qrImage(payload, modulePx = 6)                       // ~174 px square
        val page = paste(3000, 4000, code, left = 1400, top = 1900)     // 12 MP portrait photo
        assertEquals(payload, QrImageDecoder.decode(page.px, page.w, page.h))
    }

    @Test
    fun readsQrFromSmallStickerInsidePage() {
        val code = qrImage(payload, modulePx = 3)
        val page = paste(1200, 1600, code, left = 480, top = 700)
        assertEquals(payload, QrImageDecoder.decode(page.px, page.w, page.h))
    }

    @Test
    fun readsQrFromRotatedPhoto() {
        val code = qrImage(payload, modulePx = 5)
        val page = paste(1000, 1400, code, left = 380, top = 560)
        for (degrees in intArrayOf(90, 180, 270)) {
            val turned = rotate(page, degrees)
            assertEquals("rotation $degrees", payload, QrImageDecoder.decode(turned.px, turned.w, turned.h))
        }
    }

    @Test
    fun readsInvertedQrFromDarkModeScreenshot() {
        val code = qrImage(payload, modulePx = 5)
        val page = invertPixels(paste(1080, 1920, code, left = 300, top = 760))
        assertEquals(payload, QrImageDecoder.decode(page.px, page.w, page.h))
    }

    @Test
    fun readsTinyQrFromChatScreenshot() {
        val code = qrImage(payload, modulePx = 2)                        // ~58 px square
        val page = paste(1080, 1920, code, left = 500, top = 900)
        assertEquals(payload, QrImageDecoder.decode(page.px, page.w, page.h))
    }

    @Test
    fun readsLowContrastPrintedQr() {
        val code = qrImage(payload, modulePx = 5)
        val page = lowContrast(paste(1400, 1400, code, left = 500, top = 500))
        assertEquals(payload, QrImageDecoder.decode(page.px, page.w, page.h))
    }

    @Test
    fun readsQrThatWasAlreadyZoomedInto() {
        val code = qrImage(payload, modulePx = 4)
        val zoomed = crop(paste(2048, 2048, code, left = 780, top = 780), 0.45)
        assertEquals(payload, QrImageDecoder.decode(zoomed.px, zoomed.w, zoomed.h))
    }

    @Test
    fun returnsNullForAPictureWithoutQr() {
        val blank = IntArray(800 * 600) { 0xFFFFFFFF.toInt() }
        assertNull(QrImageDecoder.decode(blank, 800, 600))
    }

    // ---------------------------------------------------------------- live camera frames

    @Test
    fun readsQrFromCameraLumaPlane() {
        val code = qrImage(payload, modulePx = 3)                        // ~530 px inside a 720p frame
        val page = paste(1280, 720, code, left = (1280 - code.w) / 2, top = (720 - code.h) / 2)
        assertEquals(payload, QrImageDecoder.decodeLuma(lumaFrom(page), page.w, page.h, 0))
    }

    @Test
    fun readsQrFromRotatedCameraFrame() {
        val code = qrImage(payload, modulePx = 3)
        val page = paste(1280, 720, code, left = (1280 - code.w) / 2, top = (720 - code.h) / 2)
        val sideways = rotate(page, 90)                                  // sensor is 90 deg off the display
        assertEquals(
            payload,
            QrImageDecoder.decodeLuma(lumaFrom(sideways), sideways.w, sideways.h, 90)
        )
    }

    @Test
    fun readsSmallQrOffCentreInCameraFrame() {
        val code = qrImage(payload, modulePx = 2)                        // small: phone held further away
        val page = paste(1280, 720, code, left = 620, top = 300)
        assertEquals(payload, QrImageDecoder.decodeLuma(lumaFrom(page), page.w, page.h, 0))
    }

    @Test
    fun cameraFrameWithoutQrReturnsNull() {
        val dark = ByteArray(1280 * 720) { 12 }
        assertNull(QrImageDecoder.decodeLuma(dark, 1280, 720, 0))
    }

    private companion object {
        const val WHITE = 0xFFFFFFFF.toInt()
        const val BLACK = 0xFF000000.toInt()
    }
}
