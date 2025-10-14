package com.example.sniperflow.chart

import android.annotation.SuppressLint
import android.os.Bundle
import android.view.ViewGroup
import android.webkit.JavascriptInterface
import android.webkit.CookieManager
import android.webkit.ConsoleMessage
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.webkit.WebResourceError
import androidx.activity.OnBackPressedCallback
import androidx.annotation.Keep
import timber.log.Timber
import android.widget.Button
import androidx.appcompat.app.AppCompatActivity
import com.example.sniperflow.R

class ChartActivity : AppCompatActivity() {
	private lateinit var webView: WebView
	private var tvReady: Boolean = false
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

		webView.addJavascriptInterface(object {
			@Keep
			@JavascriptInterface
			@Suppress("unused")
			fun onTVReady() {
				tvReady = true
				runOnUiThread {
					jsQueue.forEach { cmd -> webView.evaluateJavascript(cmd, null) }
					jsQueue.clear()
				}
			}
		}, "Android")

		val html = """
			<!doctype html>
			<html>
			  <head>
				<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, maximum-scale=1\"/>
				<style>
				  html, body, #container { margin:0; padding:0; height:100%; background:#0B0B10; }
				</style>
				<script src=\"https://s3.tradingview.com/tv.js\"></script>
			  </head>
			  <body>
				<div id=\"container\"></div>
				<script>
				  window.tvWidget = new TradingView.widget({
					symbol: \"OANDA:XAUUSD\",
					interval: \"60\",
					theme: \"dark\",
					style: \"1\",
					locale: \"en\",
					allow_symbol_change: false,
					hide_top_toolbar: false,
					hide_side_toolbar: false,
					autosize: true,
					studies: [\"RSI@tv-basicstudies\",\"MAExp@tv-basicstudies\"],
					container_id: \"container\"
				  });

				  window.tvWidget.onChartReady(function () {
					if (window.Android && Android.onTVReady) {
					  Android.onTVReady();
					}
				  });

				  window.setResolutionFromAndroid = function(res) {
					if (window.tvWidget && window.tvWidget.chart) {
					  window.tvWidget.chart().setResolution(res);
					}
				  }
				  window.setSymbolFromAndroid = function(sym, res) {
					if (window.tvWidget && window.tvWidget.chart) {
					  window.tvWidget.chart().setSymbol(sym, res || \"60\", function(){});
					}
				  }
				</script>
			  </body>
			</html>
		""".trimIndent()

		webView.loadDataWithBaseURL(
			"https://s3.tradingview.com",
			html,
			"text/html",
			"UTF-8",
			null
		)

		findViewById<Button>(R.id.btnM15).setOnClickListener { setResolution("15") }
		findViewById<Button>(R.id.btnH1).setOnClickListener { setResolution("60") }
		findViewById<Button>(R.id.btnH4).setOnClickListener { setResolution("240") }
		findViewById<Button>(R.id.btnD1).setOnClickListener { setResolution("1D") }

		// Handle system back via OnBackPressedDispatcher
		onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
			override fun handleOnBackPressed() {
				if (webView.canGoBack()) webView.goBack() else finish()
			}
		})
	}

	private fun runJs(cmd: String) {
		if (tvReady) {
			webView.evaluateJavascript(cmd, null)
		} else {
			jsQueue += cmd
		}
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
	}

	override fun onDestroy() {
		(webView.parent as? ViewGroup)?.removeView(webView)
		webView.removeAllViews()
		webView.destroy()
		super.onDestroy()
	}

    // Back is handled via OnBackPressedDispatcher callback above
}

