package com.example.sniperflow.settings

import android.content.Context
import androidx.core.content.edit

class SettingsRepository(ctx: Context) {
    private val prefs = ctx.getSharedPreferences("sniperflow_settings", Context.MODE_PRIVATE)

    fun save(epsilon: Double, cooldownMs: Long, tzId: String? = null) {
        prefs.edit {
            putString("epsilon", epsilon.toString())
                .putLong("cooldownMs", cooldownMs)
                .apply {
                    if (tzId != null) putString("tzId", tzId)
                }
        }
    }

    fun load(): Pair<Double, Long> {
        val epsilon = prefs.getString("epsilon", "0.2")!!.toDouble()
        val cooldown = prefs.getLong("cooldownMs", 300_000L)
        return epsilon to cooldown
    }

    fun loadTimezone(): String = prefs.getString("tzId", "Africa/Johannesburg") ?: "Africa/Johannesburg"
}


