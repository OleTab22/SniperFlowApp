package com.example.sniperflow.data.user

import android.content.Context
import kotlinx.coroutines.flow.Flow

class UserProfileRepository(
    private val dao: UserProfileDao,
    private val userId: String = "default"
) {
    suspend fun getProfile(): UserProfileEntity {
        return dao.get(userId) ?: UserProfileEntity(userId = userId).also {
            dao.insert(it)
        }
    }
    
    fun observeProfile(): Flow<UserProfileEntity?> = dao.observe(userId)
    
    suspend fun updateProfile(profile: UserProfileEntity) {
        dao.update(profile.copy(updatedAt = System.currentTimeMillis()))
    }
    
    suspend fun updatePlanLock(locked: Boolean, reason: String?, untilSession: String?) {
        dao.updatePlanLock(userId, locked, reason, untilSession)
    }
    
    @Suppress("unused")
    suspend fun setOnboardingCompleted(completed: Boolean = true) {
        dao.setOnboardingCompleted(userId, completed)
    }
    
    companion object {
        fun create(context: Context, userId: String = "default"): UserProfileRepository {
            val db = (context.applicationContext as com.example.sniperflow.App).db
            return UserProfileRepository(db.userProfileDao(), userId)
        }
    }
}



