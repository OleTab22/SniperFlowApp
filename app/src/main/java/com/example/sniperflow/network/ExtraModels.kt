package com.example.sniperflow.network

data class Ohlc24hResponse(
    val asOf: Long,
    val last: Double,
    val high24h: Double,
    val low24h: Double,
    val change24h: Double,
    val pct24h: Double,
    val closes: List<Double>
)

data class CalendarResponse(
    val next_red: ApiEvent?
)

data class ApiEvent(
    val title: String,
    val impact: String,
    val time_utc: String,
    val lock_window: LockWindow?
)

data class LockWindow(
    val start_utc: String,
    val end_utc: String
)


