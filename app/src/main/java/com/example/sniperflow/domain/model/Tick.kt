package com.example.sniperflow.domain.model

data class PriceTick(
    val tsSecUtc: Long,
    val bid: Double,
    val ask: Double
) {
    val mid: Double get() = (bid + ask) / 2.0
}


