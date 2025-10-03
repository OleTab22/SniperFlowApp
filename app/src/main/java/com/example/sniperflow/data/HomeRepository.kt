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

class HomeRepository(
    private val api: BrokerApi,
    private val io: CoroutineDispatcher = Dispatchers.IO
) {
    /** Pull /home every 2 seconds; server computes heavy metrics. */
    val homePollFlow: Flow<HomeResponse> = flow {
        while (true) {
            val resp = api.home()
            emit(resp)
            delay(2000L)
        }
    }.flowOn(io).distinctUntilChanged()

    /** 1s ticker useful for countdowns on the client. */
    val ticker1s: Flow<Long> = flow {
        while (true) {
            emit(System.currentTimeMillis())
            delay(1000L)
        }
    }.flowOn(io)
}


