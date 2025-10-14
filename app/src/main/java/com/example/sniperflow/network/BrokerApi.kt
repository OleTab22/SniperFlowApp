package com.example.sniperflow.network

import retrofit2.http.GET
import retrofit2.http.Query
import retrofit2.http.Body
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.DELETE
import retrofit2.http.Path

data class IntradayLevels(
    val asOf: Long,
    val lastPrice: Double,
    val DO: Double?,
    val PDH: Double?,
    val PDL: Double?
)

interface BrokerApi {
    @GET("levels/intraday")
    @Suppress("unused")
    suspend fun levels(@Query("symbol") symbol: String = "XAUUSD"): IntradayLevels

    @GET("levels/intraday/sessions")
    @Suppress("unused")
    suspend fun levelsSessions(@Query("symbol") symbol: String = "XAUUSD"): SessionsResponse

    @GET("market/ohlc24h")
    @Suppress("unused")
    suspend fun ohlc24h(@Query("symbol") symbol: String = "XAUUSD"): Ohlc24hResponse

    @GET("calendar/upcoming")
    @Suppress("unused")
    suspend fun upcoming(@Query("ccy") ccy: String = "USD", @Query("hours") hours: Int = 72): CalendarResponse

    @GET("home")
    suspend fun home(): HomeResponse

    @GET("health")
    suspend fun health(): Map<String, Any>

    // --- Nowcast/Drivers fallbacks ---
    @GET("v1/nowcast")
    suspend fun nowcastV1(): NowcastV1Response

    @GET("v1/drivers")
    suspend fun driversV1(): Map<String, V1DriverZ>

    // --- Levels (UTC today + previous UTC day) ---
    @GET("/v1/levels/today")
    suspend fun levelsToday(@Query("symbol") symbol: String = "XAUUSD"): V1LevelsToday

    // --- Journal ---
    @POST("v1/journal")
    suspend fun postJournal(@Body body: JournalReq): Map<String, Any>

    @PUT("v1/journal/{id}")
    @Suppress("unused")
    suspend fun putJournal(@Path("id") id: Int, @Body body: JournalReq): Map<String, Any>

    @DELETE("v1/journal/{id}")
    @Suppress("unused")
    suspend fun deleteJournal(@Path("id") id: Int): Map<String, Any>
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
    val realized_rr: Double? = null,
    val client_id: Int? = null,
    val created_at_ms: Long? = null,
)

// v1 nowcast minimal models (fallback for Home rendering)
data class V1Driver(
    val id: String?,
    val z: Double?,
    val w: Double?,
    val fresh: Boolean? = null,
    val staleSec: Long? = null
)

data class NowcastV1Response(
    val score: Int?,
    val drivers: List<V1Driver>?,
    val ts: Long?
)

data class V1DriverZ(
    val z: Double?,
    val w: Double?,
    val fresh: Boolean? = null,
    val staleSec: Long? = null
)

data class V1LevelsToday(
    val DO: Double?,
    val PDH: Double?,
    val PDL: Double?,
    val ts: Long?
)


