package com.example.sniperflow

import android.app.Application
import android.app.Activity
import android.os.Bundle
import android.view.WindowManager
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

        // Ensure screenshots/screen recording are allowed across the app
        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {
                runCatching { activity.window?.clearFlags(WindowManager.LayoutParams.FLAG_SECURE) }
            }
            override fun onActivityResumed(activity: Activity) {
                runCatching { activity.window?.clearFlags(WindowManager.LayoutParams.FLAG_SECURE) }
            }
            override fun onActivityStarted(activity: Activity) {}
            override fun onActivityPaused(activity: Activity) {}
            override fun onActivityStopped(activity: Activity) {}
            override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
            override fun onActivityDestroyed(activity: Activity) {}
        })
    }
}










