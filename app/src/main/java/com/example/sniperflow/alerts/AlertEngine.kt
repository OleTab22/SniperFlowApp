package com.example.sniperflow.alerts

data class LevelDef(val id: String, val value: Double)
data class AlertParams(val epsilon: Double = 0.2, val cooldownMs: Long = 300_000)
data class LevelState(var lastSide: Int = 0, var lastFiredAt: Long = 0L, var fireCount: Int = 0)

@Suppress("unused")
sealed class AlertMessage(val levelId: String, val price: Double, val ts: Long) {
    class Touch(levelId: String, price: Double, ts: Long) : AlertMessage(levelId, price, ts)
    class CrossUp(levelId: String, price: Double, ts: Long) : AlertMessage(levelId, price, ts)
    class CrossDown(levelId: String, price: Double, ts: Long) : AlertMessage(levelId, price, ts)
}

object AlertEngine {
    fun eval(price: Double, level: LevelDef, state: LevelState, nowMs: Long, p: AlertParams): AlertMessage? {
        val low = level.value - p.epsilon
        val high = level.value + p.epsilon
        val side = when {
            price > high -> +1
            price < low -> -1
            else -> 0
        }
        if (nowMs - state.lastFiredAt < p.cooldownMs) {
            state.lastSide = side
            return null
        }
        val msg = when {
            side == 0 && state.lastSide != 0 -> AlertMessage.Touch(level.id, price, nowMs)
            state.lastSide < 0 && side > 0 -> AlertMessage.CrossUp(level.id, price, nowMs)
            state.lastSide > 0 && side < 0 -> AlertMessage.CrossDown(level.id, price, nowMs)
            else -> null
        }
        if (msg != null) {
            state.lastFiredAt = nowMs
            state.fireCount += 1
        }
        state.lastSide = side
        return msg
    }
}


