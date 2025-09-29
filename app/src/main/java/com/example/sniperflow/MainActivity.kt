package com.example.sniperflow

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import android.content.Intent
import android.view.View
import android.widget.TextView
import androidx.lifecycle.lifecycleScope
import com.example.sniperflow.levels.LevelsActivity
import com.example.sniperflow.network.RetrofitModule
import com.example.sniperflow.settings.SettingsRepository
import com.example.sniperflow.ui.BiasRingView
import com.google.android.material.button.MaterialButton
import androidx.core.content.ContextCompat
import com.google.android.material.snackbar.Snackbar
import kotlinx.coroutines.launch
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import timber.log.Timber
import java.text.SimpleDateFormat
import java.util.TimeZone
import com.example.sniperflow.ui.SparklineView
import java.time.Instant
import android.content.SharedPreferences
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory

class MainActivity : AppCompatActivity() {
    private var countdownJob: Job? = null
    private var periodicJob: Job? = null
    private var lastRefreshAt: Long = 0L

    // Simple cache for fast first paint
    private data class HomeCache(
        val asOf: Long,
        val last: Double,
        val DO: Double?,
        val PDH: Double?,
        val PDL: Double?,
        val high24h: Double?,
        val low24h: Double?,
        val closes: List<Double>?
    )

    private val moshi: Moshi by lazy { Moshi.Builder().add(KotlinJsonAdapterFactory()).build() }
    private val cacheAdapter by lazy { moshi.adapter(HomeCache::class.java) }
    private fun homePrefs(): SharedPreferences = getSharedPreferences("sniperflow_home", MODE_PRIVATE)
    private fun saveHomeCache(cache: HomeCache) {
        runCatching { homePrefs().edit().putString("home", cacheAdapter.toJson(cache)).apply() }
    }
    private fun loadHomeCache(): HomeCache? = runCatching {
        val s = homePrefs().getString("home", null) ?: return null
        cacheAdapter.fromJson(s)
    }.getOrNull()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Quick actions
        findViewById<MaterialButton>(R.id.openChartBtn)?.setOnClickListener {
            startActivity(Intent(this, LevelsActivity::class.java))
        }
        findViewById<MaterialButton>(R.id.alertsBtn)?.setOnClickListener { v ->
            Snackbar.make(v, "Alerts coming soon", Snackbar.LENGTH_SHORT).show()
        }
        findViewById<View>(R.id.fabAdd)?.setOnClickListener { v ->
            Snackbar.make(v, "Add: journal or alert", Snackbar.LENGTH_SHORT).show()
        }

        // Pull-to-refresh
        val swipeLayout = findViewById<androidx.swiperefreshlayout.widget.SwipeRefreshLayout>(R.id.swipe)
        swipeLayout?.setOnRefreshListener {
            val cooldownMs = SettingsRepository(this).load().second
            val now = System.currentTimeMillis()
            if (now - lastRefreshAt < cooldownMs) {
                Snackbar.make(swipeLayout, "Please wait…", Snackbar.LENGTH_SHORT).show()
                swipeLayout.isRefreshing = false
            } else {
                fetchAndRender()
            }
        }

        // Show cached UI immediately, then refresh
        loadHomeCache()?.let { showFromCache(it) }
        fetchAndRender()

        // Manual refresh with cooldown
        findViewById<MaterialButton>(R.id.refreshBtn)?.setOnClickListener { v ->
            val cooldownMs = SettingsRepository(this).load().second
            val tag = v.getTag(R.id.refreshBtn) as? Long ?: 0L
            val now = System.currentTimeMillis()
            if (now - tag < cooldownMs) {
                Snackbar.make(v, "Please wait…", Snackbar.LENGTH_SHORT).show()
            } else {
                v.setTag(R.id.refreshBtn, now)
                fetchAndRender()
            }
        }

        // Auto-refresh loop respecting cooldown
        periodicJob?.cancel()
        periodicJob = lifecycleScope.launch {
            while (isActive) {
                val cooldownMs = SettingsRepository(this@MainActivity).load().second
                val now = System.currentTimeMillis()
                if (now - lastRefreshAt >= cooldownMs) {
                    fetchAndRender()
                }
                delay(5_000)
            }
        }
    }

    private fun fetchAndRender() {
        val baseUrl = BuildConfig.BASE_URL
        val api = RetrofitModule.api(baseUrl)
        val settings = SettingsRepository(this)
        lastRefreshAt = System.currentTimeMillis()

        val priceText = findViewById<TextView>(R.id.priceText)
        val changeText = findViewById<TextView>(R.id.changeText)
        val updatedText = findViewById<TextView>(R.id.updatedText)
        val miniChart = findViewById<SparklineView>(R.id.miniChart)

        val doVal = findViewById<TextView>(R.id.doVal)
        val pdhVal = findViewById<TextView>(R.id.pdhVal)
        val pdlVal = findViewById<TextView>(R.id.pdlVal)

        val dailyText = findViewById<TextView>(R.id.eventText) // reused for small notices
        val gapText = findViewById<TextView>(R.id.gapText)

        val biasRing = findViewById<BiasRingView>(R.id.biasRing)
        val biasTitle = findViewById<TextView>(R.id.biasTitle)
        val biasConfidence = findViewById<TextView>(R.id.biasConfidence)
        val driversFlex = findViewById<com.google.android.flexbox.FlexboxLayout>(R.id.driversFlex)

        val pillAsia = findViewById<TextView>(R.id.asiaBtn)
        val pillLondon = findViewById<TextView>(R.id.londonBtn)
        val pillNY = findViewById<TextView>(R.id.newyorkBtn)

        fun highlightSessionsSAST() {
            // SAST windows: Asia 01:00–09:00, London 09:00–13:00, New York 14:30–18:00
            val tz = TimeZone.getTimeZone("Africa/Johannesburg")
            val now = java.util.Calendar.getInstance(tz)
            val h = now.get(java.util.Calendar.HOUR_OF_DAY)
            val m = now.get(java.util.Calendar.MINUTE)
            fun inRange(startH: Int, startM: Int, endH: Int, endM: Int): Boolean {
                val nowMin = h * 60 + m
                val sMin = startH * 60 + startM
                val eMin = endH * 60 + endM
                return nowMin in sMin until eMin
            }
            val asiaActive = inRange(1, 0, 9, 0)
            val londonActive = inRange(9, 0, 13, 0)
            val nyActive = inRange(14, 30, 18, 0)

            fun stylePill(tv: TextView, active: Boolean) {
                if (active) {
                    tv.background = ContextCompat.getDrawable(this, R.drawable.bg_chip_active)
                    tv.setTextColor(ContextCompat.getColor(this, R.color.colorOnPrimary))
                } else {
                    tv.background = ContextCompat.getDrawable(this, R.drawable.bg_chip)
                    tv.setTextColor(ContextCompat.getColor(this, R.color.colorOnSurface))
                }
            }

            stylePill(pillAsia, asiaActive)
            stylePill(pillLondon, londonActive)
            stylePill(pillNY, nyActive)
        }

        highlightSessionsSAST()

        val swipe = findViewById<androidx.swiperefreshlayout.widget.SwipeRefreshLayout>(R.id.swipe)
        swipe?.isRefreshing = true
        lifecycleScope.launch {
            try {
                Timber.i("Home: fetching intraday levels…")
                val intraday = api.levels("XAUUSD")
                priceText.text = String.format("%.2f", intraday.lastPrice)
                // Approx percent change vs DO when available
                val pct = intraday.DO?.let { if (it != 0.0) (intraday.lastPrice - it) / it * 100.0 else 0.0 }
                changeText.text = pct?.let { String.format("%+.2f%%", it) } ?: "—"
                pct?.let {
                    val color = if (it >= 0) ContextCompat.getColor(this@MainActivity, R.color.colorPositive)
                                 else ContextCompat.getColor(this@MainActivity, R.color.colorNegative)
                    changeText.setTextColor(color)
                }
                gapText?.text = pct?.let { "Gap ${String.format("%+.2f%%", it)}" } ?: "Gap —"

                val sdf = SimpleDateFormat("HH:mm:ss")
                sdf.timeZone = TimeZone.getDefault()
                updatedText.text = "Updated ${sdf.format(java.util.Date(intraday.asOf))}"

                doVal.text = "DO ${intraday.DO?.let { String.format("%.2f", it) } ?: "-"}"
                pdhVal.text = "PDH ${intraday.PDH?.let { String.format("%.2f", it) } ?: "-"}"
                pdlVal.text = "PDL ${intraday.PDL?.let { String.format("%.2f", it) } ?: "-"}"

                // Simple bias estimation from DO distance
                intraday.DO?.let { doPrice ->
                    val delta = intraday.lastPrice - doPrice
                    val pctMove = if (doPrice != 0.0) delta / doPrice else 0.0
                    val direction = if (pctMove >= 0) BiasRingView.Direction.BULL else BiasRingView.Direction.BEAR
                    // map |pctMove| to confidence 0..1 over a 2% move window
                    val confidence = kotlin.math.min(kotlin.math.abs(pctMove) / 0.02, 1.0)
                    biasRing.setData(confidence.toFloat(), direction)
                    biasTitle.text = "Bias: ${if (direction == BiasRingView.Direction.BULL) "Bull" else "Bear"}"
                    biasConfidence.text = "Conf. ${String.format("%.0f", confidence * 100)}%"

                    // Driver chips (sign-colored)
                    driversFlex.removeAllViews()
                    fun addChip(label: String, value: Double) {
                        val tv = TextView(this@MainActivity)
                        tv.text = "$label ${if (value >= 0) "+" else ""}${String.format("%.1f", value)}"
                        val color = if (value >= 0) R.color.colorPositive else R.color.colorNegative
                        tv.setTextColor(ContextCompat.getColor(this@MainActivity, color))
                        tv.setPadding(16, 10, 16, 10)
                        tv.background = resources.getDrawable(R.drawable.bg_chip, theme)
                        val lp = com.google.android.flexbox.FlexboxLayout.LayoutParams(
                            com.google.android.flexbox.FlexboxLayout.LayoutParams.WRAP_CONTENT,
                            com.google.android.flexbox.FlexboxLayout.LayoutParams.WRAP_CONTENT
                        )
                        lp.setMargins(0, 0, 12, 12)
                        tv.layoutParams = lp
                        driversFlex.addView(tv)
                    }
                    // Illustrative mapping from pct move
                    addChip("realZ", pctMove * 3.0)
                    addChip("dxyZ", -pctMove * 2.0)
                    addChip("cotZ", pctMove * 1.5)
                }

                Timber.i("Home: fetching session levels…")
                val sessions = api.levelsSessions("XAUUSD")
                // Optionally reflect daily nowcast text using daily levels
                dailyText.text = "Sessions loaded"

                // 24h OHLC + sparkline
                runCatching {
                    val d = api.ohlc24h("XAUUSD")
                    findViewById<TextView>(R.id.high24Text)?.text = "H24 ${String.format("%.2f", d.high24h)}"
                    findViewById<TextView>(R.id.low24Text)?.text = "L24 ${String.format("%.2f", d.low24h)}"
                    miniChart.setSeries(d.closes)

                    // Save cache
                    saveHomeCache(
                        HomeCache(
                            asOf = intraday.asOf,
                            last = intraday.lastPrice,
                            DO = intraday.DO,
                            PDH = intraday.PDH,
                            PDL = intraday.PDL,
                            high24h = d.high24h,
                            low24h = d.low24h,
                            closes = d.closes
                        )
                    )
                }

                // Upcoming event pill and countdown
                runCatching {
                    val cal = api.upcoming("USD", 72)
                    val event = cal.next_red ?: return@runCatching
                    val pill = findViewById<TextView>(R.id.eventText)
                    pill.visibility = View.VISIBLE

                    countdownJob?.cancel()
                    countdownJob = lifecycleScope.launch {
                        while (isActive) {
                            val targetMs = Instant.parse(event.time_utc).toEpochMilli()
                            val mins = ((targetMs - System.currentTimeMillis()) / 60000).coerceAtLeast(0)
                            pill.text = "${event.title} in ${mins}m (${event.impact.replaceFirstChar { it.uppercase() }})"
                            // Highlight when in lock window
                            event.lock_window?.let { lw ->
                                val start = Instant.parse(lw.start_utc).toEpochMilli()
                                val end = Instant.parse(lw.end_utc).toEpochMilli()
                                val now = System.currentTimeMillis()
                                val inLock = now in start..end
                                pill.background = ContextCompat.getDrawable(this@MainActivity,
                                    if (inLock) R.drawable.bg_news_red else R.drawable.bg_chip)
                            }
                            delay(1000)
                        }
                    }
                }

            } catch (t: Throwable) {
                Timber.e(t, "Home fetch failed")
                findViewById<View>(R.id.livePriceCard)?.let {
                    Snackbar.make(it, t.message ?: "Failed to load", Snackbar.LENGTH_LONG).show()
                }
            }
            finally {
                swipe?.isRefreshing = false
            }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        countdownJob?.cancel()
        periodicJob?.cancel()
    }

    private fun showFromCache(c: HomeCache) {
        val priceText = findViewById<TextView>(R.id.priceText)
        val changeText = findViewById<TextView>(R.id.changeText)
        val updatedText = findViewById<TextView>(R.id.updatedText)
        val doVal = findViewById<TextView>(R.id.doVal)
        val pdhVal = findViewById<TextView>(R.id.pdhVal)
        val pdlVal = findViewById<TextView>(R.id.pdlVal)
        val miniChart = findViewById<SparklineView>(R.id.miniChart)

        priceText.text = String.format("%.2f", c.last)
        val pct = c.DO?.let { if (it != 0.0) (c.last - it) / it * 100.0 else 0.0 }
        changeText.text = pct?.let { String.format("%+.2f%%", it) } ?: "—"
        pct?.let {
            val color = if (it >= 0) ContextCompat.getColor(this, R.color.colorPositive)
            else ContextCompat.getColor(this, R.color.colorNegative)
            changeText.setTextColor(color)
        }
        val sdf = SimpleDateFormat("HH:mm:ss"); sdf.timeZone = TimeZone.getDefault()
        updatedText.text = "Updated ${sdf.format(java.util.Date(c.asOf))}"

        doVal.text = "DO ${c.DO?.let { String.format("%.2f", it) } ?: "-"}"
        pdhVal.text = "PDH ${c.PDH?.let { String.format("%.2f", it) } ?: "-"}"
        pdlVal.text = "PDL ${c.PDL?.let { String.format("%.2f", it) } ?: "-"}"
        c.closes?.let { miniChart.setSeries(it) }
        findViewById<TextView>(R.id.high24Text)?.text = c.high24h?.let { "H24 ${String.format("%.2f", it)}" } ?: "H24 —"
        findViewById<TextView>(R.id.low24Text)?.text = c.low24h?.let { "L24 ${String.format("%.2f", it)}" } ?: "L24 —"
    }
}