package com.example.sniperflow.network

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject

class PriceWsClient(
    private val client: OkHttpClient,
    private val url: String,
    private val onTick: (ts: Long, bid: Double?, ask: Double?) -> Unit,
    private val onState: (state: State) -> Unit
) {
    enum class State { CONNECTING, OPEN, CLOSED, FAILED }
    private var ws: WebSocket? = null

    fun start() {
        onState(State.CONNECTING)
        val req = Request.Builder().url(url).build()
        ws = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                onState(State.OPEN)
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                runCatching {
                    val o = JSONObject(text)
                    val ts = o.optLong("ts", System.currentTimeMillis())
                    val bid = if (o.has("bid")) o.optDouble("bid") else null
                    val ask = if (o.has("ask")) o.optDouble("ask") else null
                    onTick(ts, bid, ask)
                }
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                // ignore binary
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                onState(State.FAILED)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                onState(State.CLOSED)
            }
        })
    }

    fun stop() {
        ws?.cancel()
        ws = null
        onState(State.CLOSED)
    }
}


