package com.example.sniperflow.domain.metrics

import com.example.sniperflow.domain.model.*
import kotlin.math.exp
import kotlin.math.max

object BiasNowcast {
    data class Coefs(
        val wDxyZ: Double = 0.60,
        val wRealZ: Double = 0.20,
        val wVixZ: Double = 0.20,
        val wMom: Double = 0.30,
        val bias: Double = 0.00
    )

    interface Calibrator { fun apply(p: Double): Double }

    object IdentityCalibrator : Calibrator { override fun apply(p: Double) = p.coerceIn(0.0, 1.0) }

    data class DriverInputs(
        val dxyZ: Double?,
        val realZ: Double?,
        val vixZ: Double?,
        val momentum: Double?,
        val staleDxy: Boolean,
        val staleReal: Boolean,
        val staleVix: Boolean,
        val staleMom: Boolean
    )

    data class QualityInputs(val spreadPts: Int, val latencyMs: Long)

    fun compute(
        drivers: DriverInputs,
        quality: QualityInputs,
        coefs: Coefs = Coefs(),
        calibrator: Calibrator = IdentityCalibrator,
        windowMin: Int = 60,
        modelId: String = "xau-lr-v000"
    ): NowcastResult {
        val dxy = -(drivers.dxyZ ?: 0.0)
        val real = -(drivers.realZ ?: 0.0).coerceIn(-1.5, 1.5)
        val vix = (drivers.vixZ ?: 0.0)
        val mom = (drivers.momentum ?: 0.0).coerceIn(-1.0, 1.0)

        val logit = coefs.bias + coefs.wDxyZ * dxy + coefs.wRealZ * real + coefs.wVixZ * vix + coefs.wMom * mom
        val pUp = 1.0 / (1.0 + exp(-logit))

        var p = calibrator.apply(pUp)

        val anyStale = drivers.staleDxy || drivers.staleReal || drivers.staleVix || drivers.staleMom
        val degraded = (quality.spreadPts > 25) || (quality.latencyMs > 500)
        val cap = when {
            degraded -> 0.50
            anyStale -> 0.55
            else -> 0.90
        }
        p = p.coerceIn(1.0 - cap, cap)

        val dir = if (p >= 0.5) Direction.BULL else Direction.BEAR
        val conf = max(p, 1 - p)

        val chips = listOf(
            DriverChip("realZ", -(drivers.realZ ?: 0.0), drivers.staleReal),
            DriverChip("dxyZ", -(drivers.dxyZ ?: 0.0), drivers.staleDxy),
            DriverChip("vixZ", (drivers.vixZ ?: 0.0), drivers.staleVix),
            DriverChip("mom", (drivers.momentum ?: 0.0), drivers.staleMom)
        )

        return NowcastResult(
            direction = dir,
            confidence = conf,
            windowMin = windowMin,
            drivers = chips,
            modelId = modelId,
            stale = anyStale
        )
    }
}


