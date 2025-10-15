package com.example.sniperflow.chart

import android.annotation.SuppressLint
import android.os.Bundle
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.ConsoleMessage
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.webkit.WebResourceError
import androidx.activity.OnBackPressedCallback
import timber.log.Timber
import android.widget.Button
import androidx.appcompat.app.AppCompatActivity
import com.example.sniperflow.R
import com.google.android.material.bottomnavigation.BottomNavigationView
import android.content.Intent
import com.example.sniperflow.ui.journal.JournalActivity
import com.example.sniperflow.notifications.NotificationsActivity
import com.example.sniperflow.settings.SettingsActivity

class ChartActivity : AppCompatActivity() {
	private lateinit var webView: WebView
	private var pageReady: Boolean = false
	private val jsQueue: MutableList<String> = mutableListOf()

	@SuppressLint("SetJavaScriptEnabled")
	override fun onCreate(savedInstanceState: Bundle?) {
		super.onCreate(savedInstanceState)
		setContentView(R.layout.activity_chart)

		webView = findViewById(R.id.tvWebView)

		with(webView.settings) {
			javaScriptEnabled = true
			domStorageEnabled = true
			cacheMode = WebSettings.LOAD_DEFAULT
			useWideViewPort = true
			loadWithOverviewMode = true
			builtInZoomControls = false
			displayZoomControls = false
			allowFileAccess = false
			allowContentAccess = false
			javaScriptCanOpenWindowsAutomatically = false
			mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
		}
		webView.webChromeClient = object : WebChromeClient() {
			override fun onConsoleMessage(consoleMessage: ConsoleMessage): Boolean {
				Timber.tag("ChartActivity").d(
					"JS: %s @%s:%d",
					consoleMessage.message(), consoleMessage.sourceId(), consoleMessage.lineNumber()
				)
				return super.onConsoleMessage(consoleMessage)
			}
		}
		webView.webViewClient = object : WebViewClient() {
			override fun shouldOverrideUrlLoading(
				view: WebView?, request: WebResourceRequest?
			): Boolean = false

			override fun onPageFinished(view: WebView, url: String) {
				pageReady = true
				jsQueue.forEach { cmd -> view.evaluateJavascript(cmd, null) }
				jsQueue.clear()
				super.onPageFinished(view, url)
			}

			override fun onReceivedError(
				view: WebView,
				request: WebResourceRequest,
				error: WebResourceError
			) {
				Timber.tag("ChartActivity").e(
					"WebError: code=%d desc=%s url=%s",
					error.errorCode, error.description, request.url
				)
				super.onReceivedError(view, request, error)
			}

			@Suppress("DEPRECATION")
			override fun onReceivedError(
				view: WebView,
				errorCode: Int,
				description: String?,
				failingUrl: String?
			) {
				Timber.tag("ChartActivity").e(
					"WebError(deprecated): code=%d desc=%s url=%s",
					errorCode, description, failingUrl
				)
				super.onReceivedError(view, errorCode, description, failingUrl)
			}
		}

		CookieManager.getInstance().setAcceptCookie(true)
		CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)
		WebView.setWebContentsDebuggingEnabled(true)

		// Load from local asset to avoid CSP/referrer quirks
		webView.loadUrl("file:///android_asset/chart.html")

		findViewById<Button>(R.id.btnM15).setOnClickListener { setResolution("15") }
		findViewById<Button>(R.id.btnH1).setOnClickListener { setResolution("60") }
		findViewById<Button>(R.id.btnH4).setOnClickListener { setResolution("240") }
		findViewById<Button>(R.id.btnD1).setOnClickListener { setResolution("1D") }

		// Mirror bottom nav from MainActivity
		findViewById<BottomNavigationView>(R.id.bottomNav)?.apply {
			setOnItemSelectedListener { item ->
				when (item.itemId) {
					R.id.nav_home -> { startActivity(Intent(this@ChartActivity, com.example.sniperflow.MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
					R.id.nav_journal -> { startActivity(Intent(this@ChartActivity, JournalActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
					R.id.nav_alerts -> { startActivity(Intent(this@ChartActivity, NotificationsActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
					R.id.nav_chart -> true
					R.id.nav_settings -> { startActivity(Intent(this@ChartActivity, SettingsActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
					else -> false
				}
			}
			setOnItemReselectedListener { }
			selectedItemId = R.id.nav_chart
		}

		// Handle system back via OnBackPressedDispatcher
		onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
			override fun handleOnBackPressed() {
				if (webView.canGoBack()) webView.goBack() else finish()
			}
		})
	}

	private fun runJs(cmd: String) {
		if (pageReady) webView.evaluateJavascript(cmd, null) else jsQueue += cmd
	}

	private fun setResolution(res: String) {
		runJs("window.setResolutionFromAndroid('" + res + "');")
	}

	@Suppress("unused")
	private fun setSymbol(symbol: String, res: String = "60") {
		runJs("window.setSymbolFromAndroid('" + symbol + "', '" + res + "');")
	}

	override fun onPause() {
		super.onPause()
		webView.onPause()
	}

	override fun onResume() {
		super.onResume()
		webView.onResume()
        // Ensure bottom navigation reflects current screen when brought to front
        findViewById<BottomNavigationView>(R.id.bottomNav)?.selectedItemId = R.id.nav_chart
	}

	override fun onDestroy() {
		(webView.parent as? ViewGroup)?.removeView(webView)
		webView.removeAllViews()
		webView.destroy()
		super.onDestroy()
	}

    // Back is handled via OnBackPressedDispatcher callback above
}

