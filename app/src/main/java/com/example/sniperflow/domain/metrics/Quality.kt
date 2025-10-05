package com.example.sniperflow.domain.metrics

import com.example.sniperflow.domain.model.QualityState

object Quality {
    data class Thresholds(
        val spreadOkMax: Int = 20,
        val spreadDegradedMax: Int = 30,
        val latencyOkMaxMs: Long = 300,
        val latencyDegradedMaxMs: Long = 600
    )

    fun evaluate(spreadPts: Int, latencyMs: Long, th: Thresholds = Thresholds()): QualityState {
        val state = when {
            spreadPts <= th.spreadOkMax && latencyMs <= th.latencyOkMaxMs -> "OK"
            spreadPts <= th.spreadDegradedMax && latencyMs <= th.latencyDegradedMaxMs -> "DEGRADED"
            else -> "POOR"
        }
        return QualityState(spreadPts, latencyMs, state)
    }
}


