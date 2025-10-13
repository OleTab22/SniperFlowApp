package com.example.sniperflow.ui.keepalive

import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ProcessLifecycleOwner
import kotlinx.coroutines.*
import com.example.sniperflow.network.RetrofitModule
import com.example.sniperflow.BuildConfig

object BackendKeepAlive {
    private var scope: CoroutineScope? = null
    private const val WAKE_RETRIES = 4
    private const val TICK_MINUTES = 9L

    fun init() {
        ProcessLifecycleOwner.get().lifecycle.addObserver(object : DefaultLifecycleObserver {
            override fun onStart(owner: LifecycleOwner) {
                startTicker()
                warmUp()
            }
            override fun onStop(owner: LifecycleOwner) {
                stopTicker()
            }
        })
    }

    private fun startTicker() {
        if (scope != null) return
        scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        scope!!.launch {
            while (isActive) {
                ping()
                delay(TICK_MINUTES * 60 * 1000)
            }
        }
    }

    private fun stopTicker() { scope?.cancel(); scope = null }

    fun warmUp() {
        CoroutineScope(Dispatchers.IO).launch {
            var delayMs = 1000L
            repeat(WAKE_RETRIES) {
                if (ping()) return@launch
                delay(delayMs)
                delayMs = (delayMs * 2).coerceAtMost(10_000L)
            }
        }
    }

    private suspend fun ping(): Boolean = runCatching {
        RetrofitModule.api(BuildConfig.BASE_URL).health()
    }.isSuccess
}


