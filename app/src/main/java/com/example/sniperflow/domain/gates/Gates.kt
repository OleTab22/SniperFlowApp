package com.example.sniperflow.domain.gates

data class Gates(
    val newsLockOk: Boolean,
    val lossLockOk: Boolean,
    val maxTradesOk: Boolean
) {
    val actionable: Boolean get() = newsLockOk && lossLockOk && maxTradesOk
}


