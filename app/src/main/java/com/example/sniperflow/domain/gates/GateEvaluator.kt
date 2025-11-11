package com.example.sniperflow.domain.gates

import com.example.sniperflow.domain.model.QualityState
import java.util.Calendar
import java.util.TimeZone

// Gate evaluator - checks if alerts are actionable
// Returns receipts so we can show why something was blocked
object GateEvaluator {
    
    data class GateReceipt(
        val name: String,
        val passed: Boolean,
        val reason: String? = null,
        val threshold: String? = null,
        val current: String? = null
    )
    
    data class GateResult(
        val actionable: Boolean,
        val receipts: List<GateReceipt>,
        val blockedReason: String? = null
    )
    
    data class GateInputs(
        val newsLockActive: Boolean,
        val newsLockReason: String? = null,
        val dailyLossR: Double = 0.0,
        val maxDailyLossR: Double = 5.0,
        val tradesToday: Int = 0,
        val maxTradesPerSession: Int = 3,
        val currentSession: String? = null,
        val latencyMs: Long = 0,
        val maxLatencyMs: Long = 500,
        val spreadPts: Int = 0,
        val maxSpreadPts: Int = 30,
        val quality: QualityState? = null
    )
    
    fun evaluate(inputs: GateInputs): GateResult {
        val receipts = mutableListOf<GateReceipt>()
        
        // News lock check
        val newsLockOk = !inputs.newsLockActive
        receipts.add(
            GateReceipt(
                name = "News Lock",
                passed = newsLockOk,
                reason = if (inputs.newsLockActive) inputs.newsLockReason ?: "High-impact event window active" else null,
                threshold = "Event window",
                current = if (inputs.newsLockActive) "LOCKED" else "OK"
            )
        )
        
        // Daily loss check (R-based)
        val lossOk = inputs.dailyLossR < inputs.maxDailyLossR
        receipts.add(
            GateReceipt(
                name = "Daily Loss",
                passed = lossOk,
                reason = if (!lossOk) "Daily loss limit reached (${inputs.dailyLossR}R >= ${inputs.maxDailyLossR}R)" else null,
                threshold = "${inputs.maxDailyLossR}R",
                current = "${inputs.dailyLossR}R"
            )
        )
        
        // Max trades per session
        val tradesOk = inputs.tradesToday < inputs.maxTradesPerSession
        receipts.add(
            GateReceipt(
                name = "Max Trades",
                passed = tradesOk,
                reason = if (!tradesOk) "Session trade limit reached (${inputs.tradesToday} >= ${inputs.maxTradesPerSession})" else null,
                threshold = "${inputs.maxTradesPerSession} trades/${inputs.currentSession ?: "session"}",
                current = "${inputs.tradesToday} trades"
            )
        )
        
        // Latency check
        val latencyOk = inputs.latencyMs <= inputs.maxLatencyMs
        receipts.add(
            GateReceipt(
                name = "Latency",
                passed = latencyOk,
                reason = if (!latencyOk) "Data latency too high (${inputs.latencyMs}ms > ${inputs.maxLatencyMs}ms)" else null,
                threshold = "${inputs.maxLatencyMs}ms",
                current = "${inputs.latencyMs}ms"
            )
        )
        
        // Spread check
        val spreadOk = inputs.spreadPts <= inputs.maxSpreadPts
        receipts.add(
            GateReceipt(
                name = "Spread",
                passed = spreadOk,
                reason = if (!spreadOk) "Spread too wide (${inputs.spreadPts}pts > ${inputs.maxSpreadPts}pts)" else null,
                threshold = "${inputs.maxSpreadPts}pts",
                current = "${inputs.spreadPts}pts"
            )
        )
        
        // Quality check (optional)
        inputs.quality?.let { q ->
            val qualityOk = q.state == "OK" || q.state == "DEGRADED"
            receipts.add(
                GateReceipt(
                    name = "Quality",
                    passed = qualityOk,
                    reason = if (!qualityOk) "Data quality too poor (${q.state})" else null,
                    threshold = "OK/DEGRADED",
                    current = q.state
                )
            )
        }
        
        val actionable = receipts.all { it.passed }
        val blockedReason = if (!actionable) {
            receipts.firstOrNull { !it.passed }?.reason ?: "One or more gates failed"
        } else null
        
        return GateResult(
            actionable = actionable,
            receipts = receipts,
            blockedReason = blockedReason
        )
    }
    
    // Check if we're in a news lock window
    @Suppress("unused")
    fun isInNewsLockWindow(lockStartUtc: Long?, lockEndUtc: Long?): Boolean {
        if (lockStartUtc == null || lockEndUtc == null) return false
        val now = System.currentTimeMillis() / 1000
        return now >= lockStartUtc && now <= lockEndUtc
    }
    
    // Get current session (SAST timezone)
    @Suppress("unused")
    fun getCurrentSession(tzId: String = "Africa/Johannesburg"): String? {
        val tz = TimeZone.getTimeZone(tzId)
        val cal = Calendar.getInstance(tz)
        val hour = cal.get(Calendar.HOUR_OF_DAY)
        val minute = cal.get(Calendar.MINUTE)
        val totalMinutes = hour * 60 + minute
        
        return when {
            totalMinutes in 60..539 -> "asia"      // 01:00–09:00 SAST
            totalMinutes in 540..779 -> "london"   // 09:00–13:00 SAST
            totalMinutes in 870..1079 -> "newyork" // 14:30–18:00 SAST
            else -> null
        }
    }
}

