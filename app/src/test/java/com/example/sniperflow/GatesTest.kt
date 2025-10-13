package com.example.sniperflow

import com.example.sniperflow.domain.gates.Gates
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class GatesTest {
    @Test fun all_true_actionable() {
        val g = Gates(true,true,true)
        assertTrue(g.actionable)
    }
    @Test fun any_false_not_actionable() {
        assertFalse(Gates(false,true,true).actionable)
        assertFalse(Gates(true,false,true).actionable)
        assertFalse(Gates(true,true,false).actionable)
    }
}


