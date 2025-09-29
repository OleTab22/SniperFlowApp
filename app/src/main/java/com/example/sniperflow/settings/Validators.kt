package com.example.sniperflow.settings

object Validators {
    fun isEpsilonValid(value: Double?): Boolean {
        return value != null && value > 0.01 && value <= 5.0
    }

    fun isCooldownValid(value: Long?): Boolean {
        return value != null && value in 10_000..900_000
    }
}


