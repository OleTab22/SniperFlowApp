package com.example.sniperflow.data.journal

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Update
import kotlinx.coroutines.flow.Flow

@Dao
interface JournalDao {
    @Insert
    suspend fun insert(e: JournalEntity): Long

    @Update
    suspend fun update(e: JournalEntity)

    @Query("SELECT * FROM journal ORDER BY createdAt DESC")
    fun observeAll(): Flow<List<JournalEntity>>

    @Query("SELECT * FROM journal WHERE id=:id")
    suspend fun get(id: Int): JournalEntity?

    @Query("SELECT * FROM journal WHERE synced = 0")
    suspend fun unsynced(): List<JournalEntity>

    @Query("UPDATE journal SET synced=1 WHERE id=:id")
    suspend fun markSynced(id: Int)
}










