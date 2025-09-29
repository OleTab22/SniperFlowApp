package com.example.sniperflow.settings

import android.content.Context

class SettingsRepository(ctx: Context) {
    private val prefs = ctx.getSharedPreferences("sniperflow_settings", Context.MODE_PRIVATE)

    fun save(epsilon: Double, cooldownMs: Long) {
        prefs.edit()
            .putString("epsilon", epsilon.toString())
            .putLong("cooldownMs", cooldownMs)
            .apply()
    }

    fun load(): Pair<Double, Long> {
        val epsilon = prefs.getString("epsilon", "0.2")!!.toDouble()
        val cooldown = prefs.getLong("cooldownMs", 300_000L)
        return epsilon to cooldown
    }
}


