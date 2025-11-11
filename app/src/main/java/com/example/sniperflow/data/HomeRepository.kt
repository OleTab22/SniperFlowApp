package com.example.sniperflow.data

import com.example.sniperflow.network.BrokerApi
import com.example.sniperflow.network.HomeResponse
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.flow.distinctUntilChanged

@Suppress("unused", "UnusedPrivateMember")
class HomeRepository(
    private val api: BrokerApi,
    io: CoroutineDispatcher = Dispatchers.IO
) {
    // Poll /home every 10s, slow to 30s if providers are down
    @Suppress("unused")
    val homePollFlow: Flow<HomeResponse> = flow {
        var period = 10_000L
        while (true) {
            val resp = api.home()
            emit(resp)
            val ps = resp.providerStatus
            val candlesFailed = (ps?.get("candles") == false)
            val tdBlocked = (ps?.keys?.any { it.startsWith("td_") } == true && ps.values.any { it == false })
            val yfBlocked = (ps?.keys?.any { it.startsWith("yahoo") } == true && ps.values.any { it == false })
            period = if (candlesFailed || tdBlocked || yfBlocked) 30_000L else 10_000L
            delay(period)
        }
    }.flowOn(io).distinctUntilChanged()

    // 1s ticker for countdown timers
    @Suppress("unused")
    val ticker1s: Flow<Long> = flow {
        while (true) {
            emit(System.currentTimeMillis())
            delay(1000L)
        }
    }.flowOn(io)
}


