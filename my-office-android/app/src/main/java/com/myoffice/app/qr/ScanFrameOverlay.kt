package com.myoffice.app.qr

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View
import android.view.animation.LinearInterpolator
import kotlin.math.min

/**
 * Viewfinder overlay drawn on top of the CameraX preview: dims everything outside a square window,
 * paints four corner brackets and sweeps a thin scan line through the window while looking for a code.
 * Turns green and stops sweeping the moment a QR code is decoded.
 */
class ScanFrameOverlay @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : View(context, attrs) {

    enum class State { SCANNING, FOUND }

    var state: State = State.SCANNING
        set(value) {
            if (field == value) return
            field = value
            invalidate()
        }

    private val density = resources.displayMetrics.density
    private val windowFraction = 0.70f

    private val scrimPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.parseColor("#B80B0D12") }
    private val scanningPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 3f * density
        strokeCap = Paint.Cap.ROUND
        color = Color.WHITE
    }
    private val foundPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 3.5f * density
        strokeCap = Paint.Cap.ROUND
        color = Color.parseColor("#22C55E")
    }
    private val beamPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        strokeWidth = 2f * density
        color = Color.parseColor("#7FA0FF")
    }
    private val windowRect = RectF()
    private var phase = 0f

    private val sweep = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = 2100L
        repeatCount = ValueAnimator.INFINITE
        repeatMode = ValueAnimator.RESTART
        interpolator = LinearInterpolator()
        addUpdateListener {
            phase = it.animatedValue as Float
            if (state == State.SCANNING) invalidate()
        }
    }

    init {
        setLayerType(LAYER_TYPE_HARDWARE, null)
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (!sweep.isStarted) sweep.start()
    }

    override fun onDetachedFromWindow() {
        sweep.cancel()
        super.onDetachedFromWindow()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val side = min(width, height) * windowFraction
        if (side <= 0f) return
        val left = (width - side) / 2f
        val top = (height - side) / 2f + height * 0.02f
        windowRect.set(left, top, left + side, top + side)

        // dim everything outside the window
        canvas.drawRect(0f, 0f, width.toFloat(), windowRect.top, scrimPaint)
        canvas.drawRect(0f, windowRect.bottom, width.toFloat(), height.toFloat(), scrimPaint)
        canvas.drawRect(0f, windowRect.top, windowRect.left, windowRect.bottom, scrimPaint)
        canvas.drawRect(windowRect.right, windowRect.top, width.toFloat(), windowRect.bottom, scrimPaint)

        val paint = if (state == State.FOUND) foundPaint else scanningPaint
        val arm = side * 0.17f
        val r = windowRect
        // four rounded corner brackets
        canvas.drawLine(r.left, r.top + arm, r.left, r.top, paint)
        canvas.drawLine(r.left, r.top, r.left + arm, r.top, paint)
        canvas.drawLine(r.right - arm, r.top, r.right, r.top, paint)
        canvas.drawLine(r.right, r.top, r.right, r.top + arm, paint)
        canvas.drawLine(r.right, r.bottom - arm, r.right, r.bottom, paint)
        canvas.drawLine(r.right, r.bottom, r.right - arm, r.bottom, paint)
        canvas.drawLine(r.left + arm, r.bottom, r.left, r.bottom, paint)
        canvas.drawLine(r.left, r.bottom, r.left, r.bottom - arm, paint)

        if (state == State.SCANNING) {
            val inset = side * 0.06f
            val y = r.top + inset + (r.height() - 2 * inset) * phase
            val solid = Color.parseColor("#8FB3FF")
            val clear = Color.parseColor("#008FB3FF")
            beamPaint.shader = LinearGradient(
                r.left, y, r.right, y,
                intArrayOf(clear, solid, clear), floatArrayOf(0f, 0.5f, 1f), Shader.TileMode.CLAMP
            )
            canvas.drawLine(r.left + inset, y, r.right - inset, y, beamPaint)
            beamPaint.shader = null
        }
    }
}
