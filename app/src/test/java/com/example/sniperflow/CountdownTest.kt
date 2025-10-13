package com.example.sniperflow

import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.OffsetDateTime
import java.time.ZoneOffset

private fun minutesUntil(iso:String): Int {
    val t = OffsetDateTime.parse(iso)
    val now = OffsetDateTime.now(ZoneOffset.UTC)
    return (((t.toEpochSecond() - now.toEpochSecond())/60).toInt()).coerceAtLeast(0)
}

class CountdownTest {
    @Test fun future_is_positive() {
        val future = OffsetDateTime.now(ZoneOffset.UTC).plusMinutes(42).toString()
        val m = minutesUntil(future)
        assertTrue(m in 40..42)
    }
}


