package com.example.sniperflow.domain.metrics

import com.example.sniperflow.domain.model.Ohlc
import java.time.Instant
import java.time.ZoneId

object TimeUtil {
    val TZ_SAST: ZoneId = ZoneId.of("Africa/Johannesburg")

    fun midnightSastInstant(nowUtc: Instant = Instant.now()): Instant {
        val zdt = nowUtc.atZone(TZ_SAST)
        return zdt.toLocalDate().atStartOfDay(TZ_SAST).toInstant()
    }

    fun indexFromMidnightSAST(bars: List<Ohlc>): Int {
        if (bars.isEmpty()) return 0
        val midnightEpoch = midnightSastInstant().epochSecond
        val idx = bars.indexOfFirst { it.tsSecUtc >= midnightEpoch }
        return if (idx == -1) 0 else idx
    }

    fun minutesSinceMidnightSAST(nowUtc: Instant = Instant.now()): Int {
        val m = midnightSastInstant(nowUtc).epochSecond
        val n = nowUtc.epochSecond
        return ((n - m) / 60).toInt().coerceIn(0, 1439)
    }
}


