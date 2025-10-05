package com.example.sniperflow.data.journal

import com.example.sniperflow.network.BrokerApi
import kotlinx.coroutines.flow.Flow

class JournalRepository(
    private val dao: JournalDao,
    private val api: BrokerApi
) {
    fun stream(): Flow<List<JournalEntity>> = dao.observeAll()

    suspend fun addLocal(e: JournalEntity): Long = dao.insert(e)

    suspend fun trySyncOne(e: JournalEntity) {
        runCatching { api.postJournal(e.toReq()) }
            .onSuccess { dao.markSynced(e.id) }
    }
}


