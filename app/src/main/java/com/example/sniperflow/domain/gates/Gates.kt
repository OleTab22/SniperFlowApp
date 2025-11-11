package com.example.sniperflow.domain.gates

import com.example.sniperflow.domain.model.QualityState

data class Gates(
    val newsLockOk: Boolean,
    val lossLockOk: Boolean,
    val maxTradesOk: Boolean,
    val latencyOk: Boolean = true,
    val spreadOk: Boolean = true,
    val qualityOk: Boolean = true
) {
    val actionable: Boolean get() = newsLockOk && lossLockOk && maxTradesOk && latencyOk && spreadOk && qualityOk
    
    companion object {
        @Suppress("unused", "UNUSED_PARAMETER")
        fun fromBackend(
            newsLock: Boolean?,
            planLock: Boolean?,
            planLockReason: String?,
            quality: QualityState?
        ): Gates {
            return Gates(
                newsLockOk = !(newsLock ?: false),
                lossLockOk = !(planLock ?: false), // Backend planLock includes loss limits
                maxTradesOk = !(planLock ?: false),
                latencyOk = quality?.latencyMs?.let { it <= 500 } ?: true,
                spreadOk = quality?.spreadPts?.let { it <= 30 } ?: true,
                qualityOk = quality?.state?.let { it == "OK" || it == "DEGRADED" } ?: true
            )
        }
    }
}


