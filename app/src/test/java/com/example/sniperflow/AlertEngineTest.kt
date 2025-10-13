package com.example.sniperflow

import com.example.sniperflow.alerts.AlertEngine
import com.example.sniperflow.alerts.AlertMessage
import com.example.sniperflow.alerts.AlertParams
import com.example.sniperflow.alerts.LevelDef
import com.example.sniperflow.alerts.LevelState
import org.junit.Assert.*
import org.junit.Test

class AlertEngineTest {
    @Test
    fun touch_then_cooldown_blocks() {
        val st = LevelState()
        val lvl = LevelDef("PDH", 2430.0)
        val p = AlertParams(epsilon = 0.2, cooldownMs = 300_000)
        val t0 = 1_000_000L
        val m1 = AlertEngine.eval(2430.05, lvl, st, t0, p)
        assertTrue(m1 is AlertMessage.Touch)
        val m2 = AlertEngine.eval(2430.06, lvl, st, t0 + 1000, p)
        assertNull(m2)
    }
}


