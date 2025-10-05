package com.example.sniperflow.domain.metrics

import com.example.sniperflow.domain.model.Ohlc
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.sqrt

object IntradayMetrics {
    fun wilderAtr20(daily: List<Ohlc>): Double {
        require(daily.size >= 21) { "Need >=21 daily bars" }
        fun tr(prev: Ohlc, cur: Ohlc) = max(cur.h - cur.l, max(abs(cur.h - prev.c), abs(cur.l - prev.c)))
        var atr = (1 until 21).sumOf { tr(daily[it - 1], daily[it]) } / 20.0
        for (i in 21 until daily.size) {
            val prev = daily[i - 1]; val cur = daily[i]
            val trv = tr(prev, cur)
            atr = (atr * 19.0 + trv) / 20.0
        }
        return atr
    }

    fun intradayHighLowSinceDO(minuteBars: List<Ohlc>): Pair<Double, Double> {
        if (minuteBars.isEmpty()) return Double.NaN to Double.NaN
        val start = TimeUtil.indexFromMidnightSAST(minuteBars)
        val w = minuteBars.drop(start)
        return (w.maxOf { it.h }) to (w.minOf { it.l })
    }

    fun rangeToAtr20(minuteBars: List<Ohlc>, atr20: Double): Double {
        if (atr20 <= 0.0 || atr20.isNaN()) return 0.0
        val (hi, lo) = intradayHighLowSinceDO(minuteBars)
        return (hi - lo) / atr20
    }

    fun volumePercentile(todayTicksPerMin: IntArray, historyCumSamples: List<IntArray>): Int? {
        if (todayTicksPerMin.isEmpty() || historyCumSamples.isEmpty()) return null
        val t = TimeUtil.minutesSinceMidnightSAST()
        val todayCum = todayTicksPerMin.copyOf(t + 1).sum()
        val samples = historyCumSamples.map { it[t] }
        val belowEq = samples.count { it <= todayCum }
        return ((belowEq.toDouble() / samples.size) * 100.0).toInt().coerceIn(0, 100)
    }

    fun robustZ(x: Double, median: Double, mad: Double, cap: Double = 3.0): Double {
        if (mad == 0.0) return 0.0
        val z = (x - median) / (1.4826 * mad)
        return z.coerceIn(-cap, cap)
    }

    fun activityIndex(
        rv5m: Double, medRv: Double, madRv: Double,
        tf5m: Double, medTf: Double, madTf: Double,
        spread: Double, medSp: Double, madSp: Double
    ): Double {
        val zRv = robustZ(rv5m, medRv, madRv)
        val zTf = robustZ(tf5m, medTf, madTf)
        val zSp = robustZ(-spread, -medSp, madSp)
        val raw = 0.5 * zRv + 0.3 * zTf + 0.2 * zSp
        return 1.0 / (1.0 + exp(-raw))
    }

    fun realizedVol1m(bars: List<Ohlc>, n: Int = 5): Double {
        if (bars.size < n + 1) return 0.0
        val tail = bars.takeLast(n + 1)
        val rets = tail.zipWithNext { a, b -> (b.c - a.c) / a.c }
        val mean = rets.average()
        val varc = rets.fold(0.0) { acc, r -> acc + (r - mean) * (r - mean) } / max(1, rets.size - 1)
        return sqrt(varc)
    }
}


