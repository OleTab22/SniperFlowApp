package com.example.sniperflow.data.db

import androidx.room.Database
import androidx.room.RoomDatabase
import com.example.sniperflow.data.journal.JournalDao
import com.example.sniperflow.data.journal.JournalEntity
import com.example.sniperflow.data.user.UserProfileDao
import com.example.sniperflow.data.user.UserProfileEntity

@Database(
    entities = [JournalEntity::class, UserProfileEntity::class],
    version = 2,
    exportSchema = false
)
abstract class AppDb : RoomDatabase() {
    abstract fun journalDao(): JournalDao
    abstract fun userProfileDao(): UserProfileDao
}










