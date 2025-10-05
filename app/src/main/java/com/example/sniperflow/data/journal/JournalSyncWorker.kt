package com.example.sniperflow.data.journal

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequest
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.example.sniperflow.App
import com.example.sniperflow.network.RetrofitModule
import java.util.concurrent.TimeUnit

class JournalSyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    override suspend fun doWork(): Result {
        val app = applicationContext as App
        val dao = app.db.journalDao()
        val api = RetrofitModule.api(com.example.sniperflow.BuildConfig.BASE_URL)
        val pending = dao.unsynced()
        pending.forEach { e ->
            runCatching { api.postJournal(e.toReq()) }
                .onSuccess { dao.markSynced(e.id) }
        }
        return Result.success()
    }

    companion object {
        fun schedule(context: Context) {
            val req = PeriodicWorkRequestBuilder<JournalSyncWorker>(15, TimeUnit.MINUTES)
                .setConstraints(
                    Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
                )
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                "journal-sync", ExistingPeriodicWorkPolicy.KEEP, req
            )
        }

        fun kickOnce(context: Context) {
            WorkManager.getInstance(context)
                .enqueue(OneTimeWorkRequest.from(JournalSyncWorker::class.java))
        }
    }
}


