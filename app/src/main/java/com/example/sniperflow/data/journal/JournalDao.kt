package com.example.sniperflow.data.journal

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Update
import androidx.room.Delete
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

    @Query("SELECT * FROM journal ORDER BY createdAt ASC")
    suspend fun listAll(): List<JournalEntity>

    @Delete
    @Suppress("unused")
    suspend fun delete(e: JournalEntity)

    @Query("DELETE FROM journal WHERE id=:id")
    suspend fun deleteById(id: Int)

    @Query(
        "SELECT COALESCE(SUM(CASE WHEN realizedRR < 0 THEN -realizedRR ELSE 0 END), 0.0) " +
        "FROM journal WHERE createdAt BETWEEN :startMillis AND :endMillis"
    )
    suspend fun sumLossRBetween(startMillis: Long, endMillis: Long): Double?

    @Query("SELECT COUNT(*) FROM journal WHERE createdAt BETWEEN :startMillis AND :endMillis")
    suspend fun countTradesBetween(startMillis: Long, endMillis: Long): Int

    @Query(
        "SELECT COUNT(*) FROM journal " +
        "WHERE createdAt BETWEEN :startMillis AND :endMillis " +
        "AND LOWER(session) = LOWER(:session)"
    )
    suspend fun countTradesForSession(session: String, startMillis: Long, endMillis: Long): Int
}










