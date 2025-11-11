package com.example.sniperflow.domain.planlock

import com.example.sniperflow.data.user.UserProfileRepository
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

// Plan-lock manager - locks trading when limits hit, unlocks next session
class PlanLockManager(
    private val profileRepo: UserProfileRepository
) {
    suspend fun checkAndUpdateLock(
        dailyLossR: Double,
        tradesToday: Int,
        currentSession: String?
    ): Boolean {
        val profile = profileRepo.getProfile()
        
        if (!profile.planLockEnabled) {
            return false // Plan-lock disabled
        }
        
        val shouldLock = when {
            dailyLossR >= profile.maxDailyLossR -> true
            tradesToday >= profile.maxTradesPerSession -> true
            else -> false
        }
        
        if (shouldLock && !profile.planLocked) {
            // Lock it
            val reason = when {
                dailyLossR >= profile.maxDailyLossR -> "Daily loss limit reached (${dailyLossR}R >= ${profile.maxDailyLossR}R)"
                tradesToday >= profile.maxTradesPerSession -> "Session trade limit reached ($tradesToday >= ${profile.maxTradesPerSession})"
                else -> "Risk limits exceeded"
            }
            profileRepo.updatePlanLock(
                locked = true,
                reason = reason,
                untilSession = getNextSession(currentSession)
            )
            return true
        }
        
        // Auto-unlock when next session starts
        if (profile.planLocked && profile.planLockUntilSession != null) {
            if (currentSession == profile.planLockUntilSession) {
                profileRepo.updatePlanLock(locked = false, reason = null, untilSession = null)
                return false
            }
        }
        
        return profile.planLocked
    }
    
    @Suppress("unused")
    fun observeLockStatus(): Flow<Boolean> {
        return profileRepo.observeProfile().map { it?.planLocked ?: false }
    }
    
    suspend fun getLockReason(): String? {
        return profileRepo.getProfile().planLockReason
    }
    
    private fun getNextSession(current: String?): String {
        return when (current) {
            "asia" -> "london"
            "london" -> "newyork"
            "newyork" -> "asia"
            else -> "asia"
        }
    }
}

