package com.example.sniperflow.network

import retrofit2.http.GET
import retrofit2.http.Query
import retrofit2.http.Body
import retrofit2.http.POST

data class IntradayLevels(
    val asOf: Long,
    val lastPrice: Double,
    val DO: Double?,
    val PDH: Double?,
    val PDL: Double?
)

interface BrokerApi {
    @GET("levels/intraday")
    suspend fun levels(@Query("symbol") symbol: String = "XAUUSD"): IntradayLevels

    @GET("levels/intraday/sessions")
    suspend fun levelsSessions(@Query("symbol") symbol: String = "XAUUSD"): SessionsResponse

    @GET("market/ohlc24h")
    suspend fun ohlc24h(@Query("symbol") symbol: String = "XAUUSD"): Ohlc24hResponse

    @GET("calendar/upcoming")
    suspend fun upcoming(@Query("ccy") ccy: String = "USD", @Query("hours") hours: Int = 72): CalendarResponse

    @GET("home")
    suspend fun home(): HomeResponse

    @GET("health")
    suspend fun health(): Map<String, String>

    // --- Journal ---
    @POST("v1/journal")
    suspend fun postJournal(@Body body: JournalReq): Map<String, Any>
}

data class JournalReq(
    val user_id: String,
    val alert_id: String,
    val notes: String,
    val direction: String? = null,
    val timeframe: String? = null,
    val entry: Double? = null,
    val sl: Double? = null,
    val tp: Double? = null,
    val planned_rr: Double? = null,
    val tags: List<String>? = null,
    val session: String? = null,
    val bias: String? = null,
    val doLvl: Double? = null,
    val pdh: Double? = null,
    val pdl: Double? = null,
)


