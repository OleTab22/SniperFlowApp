package com.example.sniperflow

import android.app.Application
import androidx.work.ExistingPeriodicWorkPolicy
import com.example.sniperflow.data.db.AppDb
import com.example.sniperflow.data.journal.JournalSyncWorker
import androidx.room.Room

class App : Application() {
    lateinit var db: AppDb; private set

    override fun onCreate() {
        super.onCreate()
        db = Room.databaseBuilder(this, AppDb::class.java, "sniperflow.db")
            .fallbackToDestructiveMigration()
            .build()
        // Schedule periodic sync once app starts (kept if already scheduled)
        JournalSyncWorker.schedule(this)
    }
}


