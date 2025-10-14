package com.example.sniperflow.chart

import android.annotation.SuppressLint
import android.os.Build
import android.os.Bundle
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity
import com.example.sniperflow.R

class ChartActivity : AppCompatActivity() {
    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_chart)

        val web = findViewById<WebView>(R.id.webChart)
        val s = web.settings
        // TradingView requires JS; lock down other risky capabilities
        s.javaScriptEnabled = true
        s.domStorageEnabled = true
        s.allowFileAccess = false
        s.allowContentAccess = false
        s.javaScriptCanOpenWindowsAutomatically = false
        s.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
        if (Build.VERSION.SDK_INT >= 26) s.safeBrowsingEnabled = true

        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val host = request.url.host ?: return true
                return !host.endsWith("tradingview.com")
            }
        }

        web.loadUrl("https://www.tradingview.com/chart/E80VbYXG/?symbol=OANDA%3AXAUUSD")
    }
}


