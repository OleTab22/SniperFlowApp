package com.example.sniperflow.domain.metrics

import com.example.sniperflow.domain.model.Ohlc
import java.util.Calendar
import java.util.TimeZone

object TimeUtil {
    private const val TZ_SAST_ID: String = "Africa/Johannesburg"

    private fun midnightSastEpochSec(nowUtcMs: Long = System.currentTimeMillis()): Long {
        val tz = TimeZone.getTimeZone(TZ_SAST_ID)
        val calNow = Calendar.getInstance(tz)
        calNow.timeInMillis = nowUtcMs
        calNow.set(Calendar.HOUR_OF_DAY, 0)
        calNow.set(Calendar.MINUTE, 0)
        calNow.set(Calendar.SECOND, 0)
        calNow.set(Calendar.MILLISECOND, 0)
        return (calNow.timeInMillis / 1000L)
    }

    fun indexFromMidnightSAST(bars: List<Ohlc>): Int {
        if (bars.isEmpty()) return 0
        val midnightEpoch = midnightSastEpochSec()
        val idx = bars.indexOfFirst { it.tsSecUtc >= midnightEpoch }
        return if (idx == -1) 0 else idx
    }

    fun minutesSinceMidnightSAST(nowUtcMs: Long = System.currentTimeMillis()): Int {
        val mSec = midnightSastEpochSec(nowUtcMs) * 1000L
        val diff = nowUtcMs - mSec
        return ((diff / 60000L).toInt()).coerceIn(0, 1439)
    }
}


