package com.example.sniperflow.network

import com.squareup.moshi.Json

data class HomeResponse(
    val price: PricePanel?,
    val metrics: MetricsSection?,
    val levels: LevelsSection?,
    val sessions: SessionsSection?,
    val calendar: CalendarSection?,
    val quality: QualitySection?,
    val alerts: List<AlertItem>?,
    val gates: GatesSection?
)

data class PricePanel(
    val last: Double?,
    @Json(name = "change24h") val change24h: Double?,
    @Json(name = "pct24h") val pct24h: Double?,
    @Json(name = "high24h") val high24h: Double?,
    @Json(name = "low24h") val low24h: Double?,
    @Json(name = "updatedAt") val updatedAt: Long?,
    val closes: List<Double>? = null
)

data class MetricsSection(
    @Json(name = "gap_pct") val gapPct: Double?,
    @Json(name = "range_to_atr20") val rangeToAtr20: Double?,
    @Json(name = "volume_percentile") val volumePercentile: Int?,
    @Json(name = "activity_index") val activityIndex: Double?,
    val nowcast: Nowcast?,
    val quality: QualitySection? // some payloads might include here
)

data class Nowcast(
    val direction: String?,
    val confidence: Double?,
    @Json(name = "window_min") val windowMin: Int?,
    val drivers: List<DriverChip>?,
    @Json(name = "model_id") val modelId: String?,
    @Json(name = "updated_at") val updatedAt: Long?
)

data class DriverChip(
    val key: String?,
    val value: Double?,
    val stale: Boolean? = null,
    @Json(name = "contribution") val contribution: Double? = null
)

data class QualitySection(
    @Json(name = "spread_pts") val spreadPts: Int? = null,
    @Json(name = "latency_ms") val latencyMs: Long? = null,
    val state: String? = null
)

data class LevelsSection(
    @Json(name = "do") val doLevel: LevelEntry?,
    val pdh: LevelEntry?,
    val pdl: LevelEntry?,
    val asia: AsiaLevels? = null
)

data class LevelEntry(
    val price: Double?,
    @Json(name = "swept_today") val sweptToday: Boolean? = null
)

data class AsiaLevels(
    val high: Double?,
    val low: Double?
)

data class SessionsSection(
    val current: String? = null,
    @Json(name = "overlap_with_ny") val overlapWithNy: Boolean? = null
)

data class CalendarSection(
    @Json(name = "next_red") val nextRed: ApiEvent?
)

data class AlertItem(
    val id: String?,
    val title: String?,
    @Json(name = "age_sec") val ageSec: Int? = null,
    val conf: Double? = null,
    @Json(name = "ev_r") val evR: Double? = null,
    val severity: String? = null
)

data class GatesSection(
    @Json(name = "plan_lock") val planLock: Boolean? = null,
    val reason: String? = null,
    @Json(name = "news_lock") val newsLock: Boolean? = null
)


