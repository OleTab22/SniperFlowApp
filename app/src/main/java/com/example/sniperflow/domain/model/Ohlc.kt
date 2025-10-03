package com.example.sniperflow.domain.model

data class Ohlc(
    val tsSecUtc: Long,
    val o: Double,
    val h: Double,
    val l: Double,
    val c: Double
)


