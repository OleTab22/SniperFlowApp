@file:Suppress("unused")
package com.example.sniperflow.domain.metrics

import com.example.sniperflow.domain.model.Ohlc
import com.example.sniperflow.domain.model.PricePanel

object PriceCalc {
    fun lastPrice(lastTickMid: Double?, minuteBars: List<Ohlc>): Double =
        lastTickMid ?: minuteBars.lastOrNull()?.c ?: Double.NaN

    fun delta24h(minuteBars: List<Ohlc>, last: Double, nowUtcMs: Long = System.currentTimeMillis()): Pair<Double, Double> {
        if (minuteBars.isEmpty()) return 0.0 to 0.0
        val targetEpoch = (nowUtcMs / 1000L) - 24L * 60L * 60L
        val idx = minuteBars.indexOfLast { it.tsSecUtc <= targetEpoch }.let { if (it == -1) 0 else it }
        val ref = minuteBars[idx].c
        val delta = last - ref
        val pct = if (ref != 0.0) (delta / ref) * 100.0 else 0.0
        return delta to pct
    }

    fun highLow24h(minuteBars: List<Ohlc>): Pair<Double, Double> {
        if (minuteBars.isEmpty()) return Double.NaN to Double.NaN
        val window = minuteBars.takeLast(kotlin.math.min(1440, minuteBars.size))
        val hi = window.maxOf { it.h }
        val lo = window.minOf { it.l }
        return hi to lo
    }

    fun dailyOpenSAST(minuteBars: List<Ohlc>): Double {
        if (minuteBars.isEmpty()) return Double.NaN
        val i0 = TimeUtil.indexFromMidnightSAST(minuteBars)
        val bar = minuteBars.getOrNull(i0) ?: minuteBars.last()
        return bar.o
    }

    fun gapPct(last: Double, doPrice: Double): Double =
        if (doPrice == 0.0 || doPrice.isNaN()) 0.0 else (last - doPrice) / doPrice * 100.0

    fun buildPricePanel(last: Double, minuteBars: List<Ohlc>, tickTsSec: Long?): PricePanel {
        val (delta, pct) = delta24h(minuteBars, last)
        val (hi24, lo24) = highLow24h(minuteBars)
        return PricePanel(
            last = last,
            delta24h = delta,
            pct24h = pct,
            high24h = hi24,
            low24h = lo24,
            updatedAtSec = tickTsSec ?: minuteBars.lastOrNull()?.tsSecUtc ?: 0L
        )
    }
}


