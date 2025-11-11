package com.example.sniperflow.data.user

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import kotlinx.coroutines.flow.Flow

@Dao
interface UserProfileDao {
    @Query("SELECT * FROM user_profile WHERE userId = :userId LIMIT 1")
    suspend fun get(userId: String = "default"): UserProfileEntity?
    
    @Query("SELECT * FROM user_profile WHERE userId = :userId LIMIT 1")
    fun observe(userId: String = "default"): Flow<UserProfileEntity?>
    
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(profile: UserProfileEntity)
    
    @Update
    suspend fun update(profile: UserProfileEntity)
    
    @Query("UPDATE user_profile SET planLocked = :locked, planLockReason = :reason, planLockUntilSession = :untilSession WHERE userId = :userId")
    suspend fun updatePlanLock(userId: String, locked: Boolean, reason: String?, untilSession: String?)
    
    @Query("UPDATE user_profile SET onboardingCompleted = :completed WHERE userId = :userId")
    suspend fun setOnboardingCompleted(userId: String, completed: Boolean = true)
}



