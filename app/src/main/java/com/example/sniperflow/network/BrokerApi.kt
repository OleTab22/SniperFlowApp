package com.example.sniperflow.network

import retrofit2.http.GET
import retrofit2.http.Query

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
}


