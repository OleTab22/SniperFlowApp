package com.example.sniperflow.data.user

import androidx.room.Entity
import androidx.room.PrimaryKey

// User profile - stores all user settings and risk limits
@Entity(tableName = "user_profile")
data class UserProfileEntity(
    @PrimaryKey val userId: String = "default",
    
    // Trading sessions (SAST timezone)
    val asiaEnabled: Boolean = true,
    val londonEnabled: Boolean = true,
    val newyorkEnabled: Boolean = true,
    
    // Risk limits
    val maxDailyLossR: Double = 5.0,
    val maxTradesPerSession: Int = 3,
    
    // News lock presets
    val newsLockCpi: Boolean = true,
    val newsLockNfp: Boolean = true,
    val newsLockFomc: Boolean = true,
    val newsLockCustom: String? = null,
    
    // Notification preferences
    val notifyPlanReady: Boolean = true,
    val notifyGatePass: Boolean = true,
    val notifyGateBlocked: Boolean = true,
    val notifyEcon: Boolean = true,
    val notifyNews: Boolean = true,
    
    // Quiet hours (0-23)
    val quietHoursStart: Int = 22,
    val quietHoursEnd: Int = 6,
    val quietHoursEnabled: Boolean = false,
    
    // Language: en, zu, af
    val language: String = "en",
    
    // Plan-lock state
    val planLockEnabled: Boolean = true,
    val planLocked: Boolean = false,
    val planLockReason: String? = null,
    val planLockUntilSession: String? = null,
    
    // Onboarding flag
    val onboardingCompleted: Boolean = false,
    
    // Timestamps
    val createdAt: Long = System.currentTimeMillis(),
    val updatedAt: Long = System.currentTimeMillis()
)

