package com.example.sniperflow.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.util.AttributeSet
import android.view.View
import kotlin.math.max
import kotlin.math.min

class SparklineView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#22C55E")
        strokeWidth = 3f
        style = Paint.Style.STROKE
    }

    private var points: FloatArray = floatArrayOf()

    fun setSeries(values: List<Double>) {
        if (values.isEmpty()) {
            points = floatArrayOf()
            invalidate()
            return
        }
        val w = max(width - paddingLeft - paddingRight, 1)
        val h = max(height - paddingTop - paddingBottom, 1)
        val minV = values.minOrNull() ?: 0.0
        val maxV = values.maxOrNull() ?: 1.0
        val span = (maxV - minV).let { if (it == 0.0) 1.0 else it }
        val stepX = w.toFloat() / max(1, values.size - 1)
        val temp = ArrayList<Float>(values.size * 4)
        var prevX = paddingLeft.toFloat()
        var prevY = paddingTop + (h - ((values.first() - minV) / span) * h).toFloat()
        for (i in 1 until values.size) {
            val x = paddingLeft + i * stepX
            val y = paddingTop + (h - ((values[i] - minV) / span) * h).toFloat()
            temp.add(prevX); temp.add(prevY); temp.add(x); temp.add(y)
            prevX = x; prevY = y
        }
        points = temp.toFloatArray()
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (points.isNotEmpty()) {
            canvas.drawLines(points, linePaint)
        }
    }
}


