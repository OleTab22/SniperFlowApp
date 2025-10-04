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
import java.util.Locale
import androidx.core.content.edit
import androidx.core.content.res.ResourcesCompat
import android.content.SharedPreferences
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import androidx.recyclerview.widget.RecyclerView
import com.example.sniperflow.ui.home.AlertsAdapter
import android.widget.ImageView
import android.graphics.drawable.GradientDrawable
import android.widget.Toast
import okhttp3.OkHttpClient
import com.example.sniperflow.network.PriceWsClient
import com.example.sniperflow.R

class MainActivity : AppCompatActivity() {
    private var countdownJob: Job? = null
    private var periodicJob: Job? = null
    private var lastRefreshAt: Long = 0L
    private var wsClient: PriceWsClient? = null

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
        runCatching { homePrefs().edit { putString("home", cacheAdapter.toJson(cache)) } }
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
            // Quick Journal dialog
            val input = android.widget.EditText(this)
            input.hint = "Journal: what happened?"
            android.app.AlertDialog.Builder(this)
                .setTitle("Quick Journal")
                .setView(input)
                .setPositiveButton("Save") { d, _ ->
                    val text = input.text?.toString()?.trim()
                    if (!text.isNullOrEmpty()) {
                        Snackbar.make(v, "Saved to journal", Snackbar.LENGTH_SHORT).show()
                    }
                    d.dismiss()
                }
                .setNegativeButton("Cancel") { d, _ -> d.dismiss() }
                .show()
        }

        // Top bar actions
        findViewById<ImageView>(R.id.btnSettings)?.setOnClickListener {
            startActivity(Intent(this, com.example.sniperflow.settings.SettingsActivity::class.java))
        }
        findViewById<ImageView>(R.id.btnNotifications)?.setOnClickListener {
            startActivity(Intent(this, com.example.sniperflow.notifications.NotificationsActivity::class.java))
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
                // ping health every loop to keep connection dot fresh
                runCatching {
                    val ok = RetrofitModule.api(BuildConfig.BASE_URL).health()
                    if (ok["status"] == "ok") setConnStatusGreen() else setConnStatusAmber()
                }.onFailure { setConnStatusRed() }
                delay(5_000)
            }
        }

        // Start WS ticks if backend provides it (optional). If not available, this is harmless.
        runCatching {
            val url = BuildConfig.BASE_URL.replace("http", "ws") + "ticks"
            wsClient = PriceWsClient(OkHttpClient(), url,
                onTick = { ts, bid, ask ->
                    // Update connection dot and maybe compute quality (latency/spread) later
                    setConnStatusGreen()
                },
                onState = { st ->
                    when (st) {
                        PriceWsClient.State.CONNECTING -> setConnStatusAmber()
                        PriceWsClient.State.OPEN -> setConnStatusGreen()
                        PriceWsClient.State.CLOSED, PriceWsClient.State.FAILED -> setConnStatusRed()
                    }
                }
            )
            wsClient?.start()
        }
    }

    private fun fetchAndRender() {
        val baseUrl = BuildConfig.BASE_URL
        val api = RetrofitModule.api(baseUrl)
        lastRefreshAt = System.currentTimeMillis()

        val priceText = findViewById<TextView>(R.id.priceText)
        val changeText = findViewById<TextView>(R.id.changeText)
        val updatedText = findViewById<TextView>(R.id.updatedText)
        val miniChart = findViewById<SparklineView>(R.id.miniChart)

        val doVal = findViewById<TextView>(R.id.doVal)
        val pdhVal = findViewById<TextView>(R.id.pdhVal)
        val pdlVal = findViewById<TextView>(R.id.pdlVal)

        // val dailyText = findViewById<TextView>(R.id.eventText) // deprecated usage; using specific 'pill' later
        val gapText = findViewById<TextView>(R.id.gapText)

        val biasRing = findViewById<BiasRingView>(R.id.biasRing)
        val biasTitle = findViewById<TextView>(R.id.biasTitle)
        val biasConfidence = findViewById<TextView>(R.id.biasConfidence)
        val driversFlex = findViewById<com.google.android.flexbox.FlexboxLayout>(R.id.driversFlex)

        val pillAsia = findViewById<TextView>(R.id.asiaBtn)
        val pillLondon = findViewById<TextView>(R.id.londonBtn)
        val pillNY = findViewById<TextView>(R.id.newyorkBtn)

        val alertsList = findViewById<RecyclerView>(R.id.listAlerts)
        val alertsAdapter = AlertsAdapter { /* open alert detail */ }
        alertsList?.adapter = alertsAdapter

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

        // Session pill tap: show minutes left in SAST
        fun minutesLeft(endH: Int, endM: Int): Int {
            val tz = TimeZone.getTimeZone("Africa/Johannesburg")
            val now = java.util.Calendar.getInstance(tz)
            val end = java.util.Calendar.getInstance(tz)
            end.set(java.util.Calendar.HOUR_OF_DAY, endH)
            end.set(java.util.Calendar.MINUTE, endM)
            end.set(java.util.Calendar.SECOND, 0)
            end.set(java.util.Calendar.MILLISECOND, 0)
            if (end.before(now)) end.add(java.util.Calendar.DAY_OF_MONTH, 1)
            val diffMs = end.timeInMillis - now.timeInMillis
            return (diffMs / 60000L).toInt()
        }

        pillAsia?.setOnClickListener {
            val mins = minutesLeft(9, 0)
            Toast.makeText(this, "Asia ends in ${mins}m", Toast.LENGTH_SHORT).show()
        }
        pillLondon?.setOnClickListener {
            val mins = minutesLeft(13, 0)
            Toast.makeText(this, "London ends in ${mins}m", Toast.LENGTH_SHORT).show()
        }
        pillNY?.setOnClickListener {
            val mins = minutesLeft(18, 0)
            Toast.makeText(this, "New York ends in ${mins}m", Toast.LENGTH_SHORT).show()
        }

        val swipe = findViewById<androidx.swiperefreshlayout.widget.SwipeRefreshLayout>(R.id.swipe)
        swipe?.isRefreshing = true
        // Connection dot: set amber while loading
        setConnStatusAmber()
        lifecycleScope.launch {
            try {
                Timber.i("Home: fetching consolidated payload…")
                val home = api.home()

                // Price panel
                home.price?.let { p ->
                    p.last?.let { priceText.text = String.format(Locale.getDefault(), "%.2f", it) }
                    val pct = p.pct24h
                    changeText.text = pct?.let { String.format(Locale.getDefault(), "%+.2f%%", it) } ?: "—"
                    pct?.let {
                        val color = if (it >= 0) ContextCompat.getColor(this@MainActivity, R.color.colorPositive)
                        else ContextCompat.getColor(this@MainActivity, R.color.colorNegative)
                        changeText.setTextColor(color)
                    }
                    findViewById<TextView>(R.id.high24Text)?.text = p.high24h?.let { getString(R.string.h24_fmt, it) } ?: "H24 —"
                    findViewById<TextView>(R.id.low24Text)?.text = p.low24h?.let { getString(R.string.l24_fmt, it) } ?: "L24 —"
                    p.updatedAt?.let {
                        val sdf = SimpleDateFormat("HH:mm:ss", Locale.getDefault()); sdf.timeZone = TimeZone.getDefault()
                        updatedText.text = getString(R.string.updated_fmt, sdf.format(java.util.Date(it)))
                    }
                    p.closes?.let { miniChart.setSeries(it) }
                }
                // Connection healthy
                setConnStatusGreen()

                // Metrics chips
                home.metrics?.let { m ->
                    val gap = m.gapPct
                    gapText?.text = gap?.let { getString(R.string.gap_fmt, it) } ?: "Gap —"
                    findViewById<TextView>(R.id.rangeText)?.text = m.rangeToAtr20?.let { getString(R.string.range_fmt, it) } ?: "Range —"
                    findViewById<TextView>(R.id.volumeText)?.text = m.volumePercentile?.let { getString(R.string.volume_fmt, it) } ?: "Volume —"
                    findViewById<TextView>(R.id.activityText)?.text = m.activityIndex?.let { getString(R.string.activity_fmt, it) } ?: "Activity —"
                    findViewById<TextView>(R.id.nowcastText)?.text = m.nowcast?.let { nc ->
                        val confStr = nc.confidence?.let { String.format(Locale.getDefault(), "%.0f", it * 100) } ?: "-"
                        getString(R.string.nowcast_fmt, confStr, nc.windowMin ?: 60)
                    } ?: "Nowcast —"
                }

                // Quality chip
                home.quality?.let { q ->
                    val view = findViewById<TextView>(R.id.chipQuality)
                    view?.text = when ((q.state ?: "OK").uppercase()) {
                        "OK" -> "Quality OK"
                        "DEGRADED" -> "Quality Degraded"
                        else -> "Quality Poor"
                    }
                    val state = (q.state ?: "OK").uppercase()
                    val color = when (state) {
                        "OK" -> R.color.colorPositive
                        "DEGRADED" -> R.color.colorWarning
                        else -> R.color.colorNegative
                    }
                    view?.setTextColor(ContextCompat.getColor(this@MainActivity, color))
                }

                // Levels row
                home.levels?.let { lv ->
                    doVal.text = lv.doLevel?.price?.let { getString(R.string.do_fmt, it) } ?: "DO —"
                    pdhVal.text = lv.pdh?.price?.let { getString(R.string.pdh_fmt, it) } ?: "PDH —"
                    pdlVal.text = lv.pdl?.price?.let { getString(R.string.pdl_fmt, it) } ?: "PDL —"
                }

                // Bias from nowcast
                home.metrics?.nowcast?.let { nc ->
                    val conf = (nc.confidence ?: 0.0).coerceIn(0.0, 1.0)
                    val dir = if ((nc.direction ?: "").lowercase() == "bull") BiasRingView.Direction.BULL else BiasRingView.Direction.BEAR
                    biasRing.setData(conf.toFloat(), dir)
                    biasTitle.text = getString(R.string.bias_title_fmt, if (dir == BiasRingView.Direction.BULL) "Bull" else "Bear")
                    biasConfidence.text = getString(R.string.bias_conf_fmt, String.format(Locale.getDefault(), "%.0f", conf * 100))
                    driversFlex.removeAllViews()
                    nc.drivers?.take(4)?.forEach { d ->
                        val tv = TextView(this@MainActivity)
                        val v = d.value ?: 0.0
                        val contrib = d.contribution?.let { String.format(Locale.getDefault(), " (%.0f%%)", kotlin.math.abs(it * 100)) } ?: ""
                        val sign = if (v >= 0) "+" else ""
                        val valStr = String.format(Locale.getDefault(), "%.1f", v)
                        tv.text = getString(R.string.driver_chip_text_fmt, d.key ?: "", "$sign$valStr", contrib)
                        val color = if (v >= 0) R.color.colorPositive else R.color.colorNegative
                        tv.setTextColor(ContextCompat.getColor(this@MainActivity, color))
                        tv.setPadding(16, 10, 16, 10)
                        tv.background = ResourcesCompat.getDrawable(resources, R.drawable.bg_chip, theme)
                        val lp = com.google.android.flexbox.FlexboxLayout.LayoutParams(
                            com.google.android.flexbox.FlexboxLayout.LayoutParams.WRAP_CONTENT,
                            com.google.android.flexbox.FlexboxLayout.LayoutParams.WRAP_CONTENT
                        )
                        lp.setMargins(0, 0, 12, 12)
                        tv.layoutParams = lp
                        if (d.stale == true) tv.alpha = 0.6f else tv.alpha = 1.0f
                        tv.setOnClickListener {
                            val dialog = android.app.AlertDialog.Builder(this@MainActivity)
                                .setView(layoutInflater.inflate(R.layout.dialog_driver_detail, driversFlex, false))
                                .create()
                            dialog.show()
                            val title = dialog.findViewById<TextView>(R.id.tvTitle)
                            val body = dialog.findViewById<TextView>(R.id.tvBody)
                            title?.text = getString(R.string.driver_detail_title_fmt, d.key ?: "")
                            val staleText = if (d.stale == true) getString(R.string.stale_label) else ""
                            body?.text = getString(R.string.driver_detail_body_fmt, v, contrib, staleText)
                        }
                        driversFlex.addView(tv)
                    }
                }

                // Sessions overlap badge & haptics for news lock transitions
                home.sessions?.let { s ->
                    val badge = findViewById<TextView>(R.id.badgeOverlap)
                    badge?.visibility = if (s.overlapWithNy == true) View.VISIBLE else View.GONE
                }

                // Haptics: bias flip and news lock
                runCatching {
                    if (home.gates?.newsLock == true) {
                        vibrateOnce(30, 60)
                    }
                }

                // News/countdown
                home.calendar?.nextRed?.let { event ->
                    val pill = findViewById<TextView>(R.id.eventText)
                    pill.visibility = View.VISIBLE
                    countdownJob?.cancel()
                    countdownJob = lifecycleScope.launch {
                        while (isActive) {
                            runCatching {
                                val targetMs = (event.time_utc?.toLongOrNull() ?: 0L) * 1000L
                                val mins = ((targetMs - System.currentTimeMillis()) / 60000).coerceAtLeast(0)
                                pill.text = getString(R.string.event_countdown_fmt, event.title, mins, event.impact.replaceFirstChar { it.uppercase() })
                                event.lock_window?.let { lw ->
                                    val start = (lw.start_utc.toLongOrNull() ?: 0L) * 1000L
                                    val end = (lw.end_utc.toLongOrNull() ?: 0L) * 1000L
                                    val now = System.currentTimeMillis()
                                    val inLock = now in start..end
                                    pill.background = ContextCompat.getDrawable(this@MainActivity,
                                        if (inLock) R.drawable.bg_news_red else R.drawable.bg_chip)
                                }
                            }
                            delay(1000)
                        }
                    }
                }

                // Alerts
                home.alerts?.let { list -> alertsAdapter.submitList(list.take(3)) }

                // Plan-Lock banner
                val locked = (home.gates?.planLock == true)
                findViewById<View>(R.id.bannerPlanLock)?.visibility = if (locked) View.VISIBLE else View.GONE
                if (locked) findViewById<TextView>(R.id.tvBannerText)?.text = home.gates?.reason ?: "Your plan is protecting you. New entries locked."

                // Save lightweight cache for fast first paint next launch
                runCatching {
                    val cache = HomeCache(
                        asOf = home.price?.updatedAt ?: System.currentTimeMillis(),
                        last = home.price?.last ?: 0.0,
                        DO = home.levels?.doLevel?.price,
                        PDH = home.levels?.pdh?.price,
                        PDL = home.levels?.pdl?.price,
                        high24h = home.price?.high24h,
                        low24h = home.price?.low24h,
                        closes = home.price?.closes
                    )
                    saveHomeCache(cache)
                }

            } catch (t: Throwable) {
                Timber.e(t, "Home fetch failed")
                setConnStatusRed()
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

    private fun setConnStatus(colorHex: String) {
        val dot = findViewById<View>(R.id.viewConnStatus) ?: return
        val bg = GradientDrawable()
        bg.shape = GradientDrawable.OVAL
        bg.setColor(android.graphics.Color.parseColor(colorHex))
        dot.background = bg
    }
    private fun setConnStatusGreen() = setConnStatus("#16A34A")
    private fun setConnStatusAmber() = setConnStatus("#F59E0B")
    private fun setConnStatusRed() = setConnStatus("#DC2626")

    private fun vibrateOnce(durationMs: Long, amplitude: Int) {
        try {
            val vibrator = getSystemService(android.content.Context.VIBRATOR_SERVICE) as android.os.Vibrator
            if (android.os.Build.VERSION.SDK_INT >= 26) {
                vibrator.vibrate(android.os.VibrationEffect.createOneShot(durationMs, amplitude))
            } else {
                @Suppress("DEPRECATION")
                vibrator.vibrate(durationMs)
            }
        } catch (_: Throwable) { }
    }
}