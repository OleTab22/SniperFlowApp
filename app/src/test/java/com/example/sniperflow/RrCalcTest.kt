package com.example.sniperflow

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

private fun rr(entry:Double?, sl:Double?, tp:Double?): Double? {
    if (entry==null||sl==null||tp==null) return null
    val risk = kotlin.math.abs(entry - sl)
    val reward = kotlin.math.abs(tp - entry)
    return if (risk>0) reward/risk else null
}

class RrCalcTest {
    @Test fun rr_basic() {
        val r = rr(100.0, 99.0, 101.0)
        assertEquals(2.0, r!!, 1e-6)
    }
    @Test fun rr_nulls() {
        assertNull(rr(null, 99.0, 101.0))
        assertNull(rr(100.0, null, 101.0))
        assertNull(rr(100.0, 99.0, null))
    }
}


