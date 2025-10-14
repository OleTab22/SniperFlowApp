package com.example.sniperflow

import android.app.Application
import com.example.sniperflow.data.db.AppDb
import com.example.sniperflow.data.journal.JournalSyncWorker
import androidx.room.Room
import com.example.sniperflow.domain.metrics.UserTimezone
import com.example.sniperflow.settings.SettingsRepository

class App : Application() {
    lateinit var db: AppDb; private set

    override fun onCreate() {
        super.onCreate()
        db = Room.databaseBuilder(this, AppDb::class.java, "sniperflow.db")
            .fallbackToDestructiveMigration(true)
            .build()
        // Schedule periodic sync once app starts (kept if already scheduled)
        JournalSyncWorker.schedule(this)
        // Keep backend warm while app in foreground
        com.example.sniperflow.ui.keepalive.BackendKeepAlive.init()
        // Initialize user-selected timezone (default Africa/Johannesburg)
        runCatching { UserTimezone.tzId = SettingsRepository(this).loadTimezone() }
    }
}










