package com.example.sniperflow.domain.model

data class PricePanel(
    val last: Double,
    val delta24h: Double,
    val pct24h: Double,
    val high24h: Double,
    val low24h: Double,
    val updatedAtSec: Long
)

data class MetricsPanel(
    val gapPct: Double,
    val rangeToAtr20: Double,
    val volumePercentile: Int?,
    val activityIndex: Double,
    val nowcast: NowcastResult,
    val quality: QualityState
)

enum class Direction { BULL, BEAR }

data class DriverChip(val key: String, val value: Double, val stale: Boolean)

data class NowcastResult(
    val direction: Direction,
    val confidence: Double,
    val windowMin: Int,
    val drivers: List<DriverChip>,
    val modelId: String,
    val stale: Boolean
)

data class QualityState(val spreadPts: Int, val latencyMs: Long, val state: String)


