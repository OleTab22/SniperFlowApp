package com.example.sniperflow.network

data class LevelsTriplet(
    val DO: Double?,
    val PDH: Double?,
    val PDL: Double?
)

data class SessionsByName(
    val sydney: LevelsTriplet?,
    val tokyo: LevelsTriplet?,
    val london: LevelsTriplet?,
    val newyork: LevelsTriplet?
)

data class SessionsResponse(
    val asOf: Long,
    val lastPrice: Double,
    val daily: LevelsTriplet?,
    val sessions: SessionsByName?
)


