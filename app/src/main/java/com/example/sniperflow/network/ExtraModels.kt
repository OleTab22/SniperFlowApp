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

// --- Signals / Ledger (MVP) ---
data class SignalDto(
    val id: String?,
    val ts: Long?,
    val symbol: String?,
    val side: String?,
    val entry: Double?,
    val sl: Double?,
    val tp1: Double?,
    val tp2: Double?,
    val time_stop_min: Int?,
    val confidence: Double?,
    val regime: String?,
    val reasons: List<String>?,
    val reason: String?,
    val status: String?
)

data class LedgerEntryDto(
    val signal_id: String?,
    val open_ts: Long?,
    val close_ts: Long?,
    val open_price: Double?,
    val close_price: Double?,
    val mae: Double?,
    val mfe: Double?,
    val outcome_r: Double?,
    val slippage: Double?,
    val spread: Double?,
    val reason_close: String?
)


