package com.example.sniperflow.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import androidx.core.content.withStyledAttributes
import com.example.sniperflow.R
import kotlin.math.min

class BiasRingView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    enum class Direction { BULL, BEAR }

    private var confidence: Float = 0.62f // 0..1
    private var direction: Direction = Direction.BULL

    private val backgroundPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 12f
        color = Color.parseColor("#242A31")
        strokeCap = Paint.Cap.ROUND
    }

    private val foregroundPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 12f
        color = Color.parseColor("#66E27A")
        strokeCap = Paint.Cap.ROUND
    }

    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        textAlign = Paint.Align.CENTER
        textSize = 28f
    }

    private val arcBounds = RectF()

    init {
        context.withStyledAttributes(attrs, R.styleable.BiasRingView) {
            confidence = getFloat(R.styleable.BiasRingView_sf_confidence, 0.62f)
            val dir = getInt(R.styleable.BiasRingView_sf_direction, 0)
            direction = if (dir == 0) Direction.BULL else Direction.BEAR
            applyDirectionColor()
        }
    }

    fun setData(confidence01: Float, dir: Direction) {
        confidence = confidence01.coerceIn(0f, 1f)
        direction = dir
        applyDirectionColor()
        invalidate()
    }

    private fun applyDirectionColor() {
        foregroundPaint.color = if (direction == Direction.BULL) {
            Color.parseColor("#16A34A") // green
        } else {
            Color.parseColor("#DC2626") // red
        }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val size = min(width, height).toFloat()
        val padding = 10f
        arcBounds.set(padding, padding, size - padding, size - padding)

        // Base arc
        canvas.drawArc(arcBounds, 135f, 270f, false, backgroundPaint)

        // Foreground progress
        val sweep = 270f * confidence
        canvas.drawArc(arcBounds, 135f, sweep, false, foregroundPaint)

        // Center text
        val pct = (confidence * 100f).toInt()
        canvas.drawText("$pct%", size / 2f, size / 2f + 10f, textPaint)
    }
}


