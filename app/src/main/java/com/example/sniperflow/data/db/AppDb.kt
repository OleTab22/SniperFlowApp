package com.example.sniperflow.data.db

import androidx.room.Database
import androidx.room.RoomDatabase
import com.example.sniperflow.data.journal.JournalDao
import com.example.sniperflow.data.journal.JournalEntity

@Database(
    entities = [JournalEntity::class],
    version = 1,
    exportSchema = false
)
abstract class AppDb : RoomDatabase() {
    abstract fun journalDao(): JournalDao
}










