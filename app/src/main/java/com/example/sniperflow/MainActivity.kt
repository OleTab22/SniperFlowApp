package com.example.sniperflow

// removed unused Context import
// R already in this package via import; redundant qualifier warnings will be fixed below
import android.content.Intent
import android.content.SharedPreferences
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.view.View
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.edit
import androidx.core.content.res.ResourcesCompat
import androidx.core.graphics.toColorInt
import androidx.core.view.isEmpty
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.RecyclerView
import com.example.sniperflow.network.PriceWsClient
import com.example.sniperflow.network.RetrofitModule
import com.example.sniperflow.notifications.NotificationsActivity
import com.example.sniperflow.settings.SettingsActivity
import com.example.sniperflow.settings.SettingsRepository
import com.example.sniperflow.ui.BiasRingView
import com.example.sniperflow.ui.SparklineView
import com.example.sniperflow.ui.home.AlertsAdapter
import com.example.sniperflow.ui.journal.JournalActivity
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.google.android.material.button.MaterialButton
import com.google.android.material.snackbar.Snackbar
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import timber.log.Timber
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone
import com.example.sniperflow.domain.metrics.UserTimezone

class MainActivity : AppCompatActivity() {
    private var countdownJob: Job? = null
    private var periodicJob: Job? = null
    private var lastRefreshAt: Long = 0L
    private var wsClient: PriceWsClient? = null
    private var lastApiOkAt: Long = 0L
    private var isWsOpen: Boolean = false

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
            startActivity(Intent(this, com.example.sniperflow.chart.ChartActivity::class.java))
        }
        findViewById<MaterialButton>(R.id.alertsBtn)?.setOnClickListener { v ->
            Snackbar.make(v, "Alerts coming soon", Snackbar.LENGTH_SHORT).show()
        }
        // Alerts switch (read-only hook for now)
        findViewById<com.google.android.material.switchmaterial.SwitchMaterial>(R.id.alertsSwitch)?.setOnCheckedChangeListener { _, on ->
            Toast.makeText(this, if (on) "Alerts ON" else "Alerts OFF", Toast.LENGTH_SHORT).show()
        }
        findViewById<View>(R.id.fabAdd)?.setOnClickListener { v ->
            // Quick Journal dialog (persist entry locally)
            val input = android.widget.EditText(this)
            input.hint = "Journal: what happened?"
            android.app.AlertDialog.Builder(this)
                .setTitle("Quick Journal")
                .setView(input)
                .setPositiveButton("Save") { d, _ ->
                    val text = input.text?.toString()?.trim()
                    if (!text.isNullOrEmpty()) {
                        lifecycleScope.launch {
                            try {
                                val dao = (application as App).db.journalDao()
                                dao.insert(
                                    com.example.sniperflow.data.journal.JournalEntity(
                                        userId = "anon",
                                        timeframe = "M5",
                                        direction = "Note",
                                        session = "",
                                        bias = "",
                                        entry = null,
                                        sl = null,
                                        tp = null,
                                        plannedRR = null,
                                        doLvl = null,
                                        pdh = null,
                                        pdl = null,
                                        notes = text,
                                        tagsCsv = "quick",
                                        shotUrisCsv = "",
                                        synced = false
                                    )
                                )
                                Snackbar.make(v, "Saved to journal", Snackbar.LENGTH_SHORT).show()
                                com.example.sniperflow.data.journal.JournalSyncWorker.kickOnce(this@MainActivity)
                            } catch (_: Throwable) {
                                Snackbar.make(v, "Failed to save", Snackbar.LENGTH_LONG).show()
                            }
                        }
                    }
                    d.dismiss()
                }
                .setNegativeButton("Cancel") { d, _ -> d.dismiss() }
                .show()
        }

        // Top bar actions
        findViewById<ImageView>(R.id.btnSettings)?.setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }
        findViewById<ImageView>(R.id.btnNotifications)?.setOnClickListener {
            startActivity(Intent(this, NotificationsActivity::class.java))
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
        // Long-press copy level values
        fun enableCopy(tv: TextView) {
            tv.setOnLongClickListener {
                val txt = tv.text?.toString() ?: return@setOnLongClickListener false
                try {
                    val cm = getSystemService(android.content.ClipboardManager::class.java)
                    cm?.setPrimaryClip(android.content.ClipData.newPlainText("price", txt))
                    Toast.makeText(this, "Copied: ${txt}", Toast.LENGTH_SHORT).show()
                    true
                } catch (_: Throwable) { false }
            }
        }
        listOfNotNull(findViewById<TextView>(R.id.doVal), findViewById<TextView>(R.id.pdhVal), findViewById<TextView>(R.id.pdlVal)).forEach { enableCopy(it) }
        // Bottom navigation
        val bottomNav = findViewById<BottomNavigationView>(R.id.bottomNav)
        bottomNav?.setOnItemSelectedListener { item ->
            when (item.itemId) {
                R.id.nav_home -> {
                    // already here
                    true
                }
                R.id.nav_journal -> {
                    startActivity(Intent(this, JournalActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
                    true
                }
                R.id.nav_alerts -> {
                    startActivity(Intent(this, NotificationsActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
                    true
                }
                R.id.nav_chart -> {
                    startActivity(Intent(this, com.example.sniperflow.chart.ChartActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
                    true
                }
                R.id.nav_settings -> {
                    startActivity(Intent(this, SettingsActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
                    true
                }
                else -> false
            }
        }
        bottomNav?.setOnItemReselectedListener { /* no-op to avoid reloading */ }
        // Ensure Home is marked selected on this screen
        bottomNav?.selectedItemId = R.id.nav_home


        // Manual refresh with cooldown + haptic
        findViewById<MaterialButton>(R.id.refreshBtn)?.setOnClickListener { v ->
            val cooldownMs = SettingsRepository(this).load().second
            val tag = v.getTag(R.id.refreshBtn) as? Long ?: 0L
            val now = System.currentTimeMillis()
            if (now - tag < cooldownMs) {
                Snackbar.make(v, "Please wait…", Snackbar.LENGTH_SHORT).show()
            } else {
                v.setTag(R.id.refreshBtn, now)
                // light haptic feedback
                try { v.performHapticFeedback(android.view.HapticFeedbackConstants.KEYBOARD_TAP) } catch (_: Throwable) {}
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
                    val status = ok["status"]?.toString()?.lowercase(Locale.getDefault())
                    val okFlag = (status == "ok") || (ok["ok"] == true)
                    if (okFlag) setConnStatusGreen() else setConnStatusAmber()
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
                    // Update status label to WS
                    findViewById<TextView>(R.id.statusLabel)?.text = getString(R.string.ws_label)
                },
                onState = { st ->
                    when (st) {
                        PriceWsClient.State.CONNECTING -> { isWsOpen = false; setConnStatusAmber() }
                        PriceWsClient.State.OPEN -> { isWsOpen = true; setConnStatusGreen(); findViewById<TextView>(R.id.statusLabel)?.text = getString(R.string.ws_label); lastApiOkAt = System.currentTimeMillis() }
                        PriceWsClient.State.CLOSED, PriceWsClient.State.FAILED -> {
                            isWsOpen = false
                            val recentOk = System.currentTimeMillis() - lastApiOkAt < 30_000L
                            if (recentOk) {
                                setConnStatusAmber()
                                findViewById<TextView>(R.id.statusLabel)?.text = getString(R.string.polling_label)
                            } else {
                                setConnStatusRed()
                                findViewById<TextView>(R.id.statusLabel)?.text = getString(R.string.offline_label)
                            }
                        }
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
        // val updatedAgoText = findViewById<TextView>(R.id.updatedAgoText) // unused
        val miniChart = findViewById<SparklineView>(R.id.miniChart)

        val doVal = findViewById<TextView>(R.id.doVal)
        val doDelta = findViewById<TextView>(R.id.doDelta)
        val pdhVal = findViewById<TextView>(R.id.pdhVal)
        val pdhDelta = findViewById<TextView>(R.id.pdhDelta)
        val pdlVal = findViewById<TextView>(R.id.pdlVal)
        val pdlDelta = findViewById<TextView>(R.id.pdlDelta)

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
        alertsList?.layoutManager = androidx.recyclerview.widget.LinearLayoutManager(this)

        fun highlightSessionsSAST() {
            // Session windows relative to selected timezone (default Africa/Johannesburg)
            val tz = UserTimezone.timeZone()
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
            val tz = UserTimezone.timeZone()
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
                setConnStatusGreen(); lastApiOkAt = System.currentTimeMillis()
                // Status label: prefer WS if open, else Polling; append staleness if any
                val label = findViewById<TextView>(R.id.statusLabel)
                val base = if (isWsOpen) getString(R.string.ws_label) else getString(R.string.polling_label)
                val updated = home.price?.updatedAt ?: 0L
                val staleMin = if (updated > 0) (((System.currentTimeMillis()) - updated) / 60000).toInt() else 0
                label?.text = if (staleMin >= 2) "$base • Stale ${staleMin}m" else base

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
                    // Confluence summary (simple preview from nowcast + drivers)
                    val confScore = ((m.nowcast?.confidence ?: 0.0) * 100).toInt().coerceIn(0, 100)
                    val confluence = findViewById<TextView>(R.id.confluenceText)
                    // Trend arrow based on last cached score
                    val prefs = homePrefs()
                    val lastScore = prefs.getInt("last_conf_score", confScore)
                    val arrow = when {
                        confScore - lastScore >= 5 -> "▲"
                        lastScore - confScore >= 5 -> "▼"
                        else -> "•"
                    }
                    confluence?.text = getString(R.string.confluence_fmt, confScore, arrow)
                    // Persist for next render
                    prefs.edit { putInt("last_conf_score", confScore) }
                    // Explain taps
                    fun showExplain(title: String, body: String) {
                        val dialog = android.app.AlertDialog.Builder(this@MainActivity)
                            .setView(layoutInflater.inflate(R.layout.dialog_driver_detail, findViewById(android.R.id.content), false))
                            .create()
                        dialog.show()
                        dialog.findViewById<TextView>(R.id.tvTitle)?.text = title
                        dialog.findViewById<TextView>(R.id.tvBody)?.text = body
                    }
                    gapText?.setOnClickListener { showExplain(getString(R.string.explain_gap_title), getString(R.string.explain_gap_body)) }
                    findViewById<TextView>(R.id.rangeText)?.setOnClickListener { showExplain(getString(R.string.explain_range_title), getString(R.string.explain_range_body)) }
                    findViewById<TextView>(R.id.volumeText)?.setOnClickListener { showExplain(getString(R.string.explain_volume_title), getString(R.string.explain_volume_body)) }
                    findViewById<TextView>(R.id.activityText)?.setOnClickListener { showExplain(getString(R.string.explain_activity_title), getString(R.string.explain_activity_body)) }
                    findViewById<TextView>(R.id.nowcastText)?.setOnClickListener { showExplain(getString(R.string.explain_nowcast_title), getString(R.string.explain_nowcast_body)) }
                    confluence?.setOnClickListener { showExplain(getString(R.string.explain_confluence_title), getString(R.string.explain_confluence_body)) }
                }

                // Quality chip
                home.quality?.let { q ->
                    val view = findViewById<TextView>(R.id.chipQuality)
                    view?.text = when ((q.state ?: "OK").uppercase(Locale.getDefault())) {
                        "OK" -> getString(R.string.quality_ok)
                        "DEGRADED" -> getString(R.string.quality_degraded)
                        else -> getString(R.string.quality_poor)
                    }
                    val state = (q.state ?: "OK").uppercase()
                    val color = when (state) {
                        "OK" -> R.color.colorPositive
                        "DEGRADED" -> R.color.colorWarning
                        else -> R.color.colorNegative
                    }
                    view?.setTextColor(ContextCompat.getColor(this@MainActivity, color))

                    // Feed banner: show degraded or offline states
                    val banner = findViewById<View>(R.id.bannerFeed)
                    val bannerText = findViewById<TextView>(R.id.tvBannerFeedText)
                    when (state) {
                        "DEGRADED" -> {
                            banner?.visibility = View.VISIBLE
                            bannerText?.text = getString(R.string.feed_degraded_text)
                        }
                        "POOR" -> {
                            banner?.visibility = View.VISIBLE
                            bannerText?.text = getString(R.string.feed_stale_text)
                        }
                        else -> banner?.visibility = View.GONE
                    }
                }

                // Levels row (use same formatting as cache path to ensure chip text renders)
                home.levels?.let { lv ->
                    var doNum = lv.doLevel?.price
                    var pdhNum = lv.pdh?.price
                    var pdlNum = lv.pdl?.price
                    // Fallback: fetch UTC levels today if previous range missing
                    if (doNum == null || pdhNum == null || pdlNum == null) {
                        runCatching {
                            val t = RetrofitModule.api(BuildConfig.BASE_URL).levelsToday("XAUUSD")
                            if (doNum == null) doNum = t.DO
                            if (pdhNum == null) pdhNum = t.PDH
                            if (pdlNum == null) pdlNum = t.PDL
                        }
                    }
                    doVal.text = doNum?.let { getString(R.string.do_fmt, it) }
                        ?: getString(R.string.do_fmt, java.lang.Double.NaN).replace("NaN", "—")
                    pdhVal.text = pdhNum?.let { getString(R.string.pdh_fmt, it) }
                        ?: getString(R.string.pdh_fmt, java.lang.Double.NaN).replace("NaN", "—")
                    pdlVal.text = pdlNum?.let { getString(R.string.pdl_fmt, it) }
                        ?: getString(R.string.pdl_fmt, java.lang.Double.NaN).replace("NaN", "—")
                    // Deltas from DO
                    val doPrice = doNum
                    fun fmtDelta(v: Double?, anchor: Double?): String? {
                        if (v == null || anchor == null) return null
                        val d = v - anchor
                        return String.format(Locale.getDefault(), "Δ %+.2f", d)
                    }
                    doDelta?.text = ""
                    pdhDelta?.text = fmtDelta(pdhNum, doPrice) ?: ""
                    pdlDelta?.text = fmtDelta(pdlNum, doPrice) ?: ""
                    // Session 50% preview from Asia range if available
                    val asi = lv.asia
                    val mid = if (asi?.high != null && asi.low != null) (asi.high + asi.low) / 2.0 else null
                    val last = home.price?.last
                    val dist = if (mid != null && last != null) kotlin.math.abs(last - mid) else null
                    findViewById<TextView>(R.id.sessionMidText)?.text = if (mid != null && dist != null) {
                        getString(R.string.session_mid_fmt, mid, dist)
                    } else getString(R.string.session_mid_fmt, java.lang.Double.NaN, java.lang.Double.NaN).replace("NaN", "—")
                }

                // Bias from nowcast (with freshness guard and v1 fallback)
                run {
                    val primary = home.metrics?.nowcast
                    var useDir = (primary?.direction ?: "").lowercase(Locale.getDefault())
                    var useConf = (primary?.confidence ?: Double.NaN)
                    val updatedAt = primary?.updatedAt ?: 0L
                    val nowMs = System.currentTimeMillis()
                    val isStale = (updatedAt == 0L) || (nowMs - updatedAt > 10 * 60 * 1000)
                    if (primary == null || isStale || useConf.isNaN()) {
                        runCatching {
                            val v1 = api.nowcastV1()
                            val score = (v1.score ?: 50).coerceIn(0, 100)
                            useDir = if (score >= 50) "bull" else "bear"
                            useConf = kotlin.math.max(score, 100 - score) / 100.0
                        }
                    }
                    val conf = useConf.coerceIn(0.0, 1.0)
                    val dir = if (useDir == "bull") BiasRingView.Direction.BULL else BiasRingView.Direction.BEAR
                    biasRing.setData(conf.toFloat(), dir)
                    biasTitle.text = getString(R.string.bias_title_fmt, if (dir == BiasRingView.Direction.BULL) "Bull" else "Bear")
                    biasConfidence.text = getString(R.string.bias_conf_fmt, String.format(Locale.getDefault(), "%.0f", conf * 100))
                    driversFlex.removeAllViews()
                    // Driver label mapping (handles both /home keys and /v1 keys)
                    val DRIVER_LABEL = mapOf(
                        "dxyZ" to "DXY",
                        "dxy" to "DXY",
                        "realZ" to "Real Yields",
                        "real10y" to "Real Yields",
                        "real10Y" to "Real Yields",
                        "DFII10" to "Real Yields",
                        "vixZ" to "VIX",
                        "risk_on" to "Risk-on",
                        "do_ctx" to "DO context",
                        "mom" to "Momentum"
                    )
                    var chips = (primary?.drivers ?: emptyList()).filter { it.key != null }
                        .distinctBy { it.key ?: "" }
                        .sortedByDescending { kotlin.math.abs(it.contribution ?: 0.0) }
                        .take(4)

                
                    if (chips.isEmpty()) {
                        runCatching {
                            // Cache /v1/nowcast for 60s in SharedPreferences to save quotas
                            val prefs = homePrefs()
                            val key = "nowcast_cache_json"
                            val keyTs = "nowcast_cache_ts"
                            val cached = prefs.getString(key, null)
                            val cachedTs = prefs.getLong(keyTs, 0L)
                            val now = System.currentTimeMillis()
                            val v1 = if (cached != null && now - cachedTs < 60_000L) {
                                // parse cached
                                val o = org.json.JSONObject(cached)
                                val arr = o.optJSONArray("drivers")
                                val list = mutableListOf<com.example.sniperflow.network.V1Driver>()
                                if (arr != null) {
                                    for (i in 0 until arr.length()) {
                                        val it = arr.getJSONObject(i)
                                        val idStr = it.optString("id")
                                        list += com.example.sniperflow.network.V1Driver(
                                            id = if (idStr.isBlank()) null else idStr,
                                            z = it.optDouble("z"),
                                            w = it.optDouble("w"),
                                            fresh = it.optBoolean("fresh", true),
                                            staleSec = if (it.has("staleSec")) it.optLong("staleSec") else null
                                        )
                                    }
                                }
                                com.example.sniperflow.network.NowcastV1Response(
                                    score = o.optInt("score"),
                                    drivers = list,
                                    ts = o.optLong("ts")
                                )
                            } else {
                                val resp = api.nowcastV1()
                                // store compact cache
                                val arr = org.json.JSONArray()
                                resp.drivers?.forEach { d ->
                                    val it = org.json.JSONObject()
                                    it.put("id", d.id)
                                    it.put("z", d.z)
                                    it.put("w", d.w)
                                    it.put("fresh", d.fresh)
                                    if (d.staleSec != null) it.put("staleSec", d.staleSec)
                                    arr.put(it)
                                }
                                val obj = org.json.JSONObject()
                                obj.put("score", resp.score)
                                obj.put("drivers", arr)
                                obj.put("ts", resp.ts)
                                prefs.edit { putString(key, obj.toString()); putLong(keyTs, now) }
                                resp
                            }
                            val mapped = (v1.drivers ?: emptyList()).map { d ->
                                com.example.sniperflow.network.DriverChip(
                                    key = d.id,
                                    value = d.z,
                                    stale = (d.fresh == false),
                                    contribution = d.w
                                )
                            }
                            chips = mapped
                                .distinctBy { it.key ?: "" }
                                .sortedByDescending { kotlin.math.abs(it.contribution ?: 0.0) }
                                .take(4)
                        }
                        if (chips.isEmpty()) {
                            runCatching {
                                val zmap = api.driversV1() // { id -> {z,w,fresh,staleSec} }
                                val mapped = zmap.map { (id, d) ->
                                    com.example.sniperflow.network.DriverChip(
                                        key = id,
                                        value = d.z,
                                        stale = d.fresh == false,
                                        contribution = d.w
                                    )
                                }
                                chips = mapped
                                    .distinctBy { it.key ?: "" }
                                    .sortedByDescending { kotlin.math.abs(it.contribution ?: 0.0) }
                                    .take(4)
                            }
                        }
                    } else {
                        // Persist drivers snapshot so re-open shows something instantly
                        runCatching {
                            val prefs = homePrefs()
                            val arr = org.json.JSONArray()
                            chips.forEach { d ->
                                val it = org.json.JSONObject()
                                it.put("id", d.key)
                                it.put("z", d.value)
                                it.put("w", d.contribution)
                                it.put("fresh", d.stale?.not() ?: true)
                                arr.put(it)
                            }
                            val obj = org.json.JSONObject()
                            obj.put("score", ((conf) * 100).toInt())
                            obj.put("drivers", arr)
                            obj.put("ts", System.currentTimeMillis())
                            prefs.edit { putString("nowcast_cache_json", obj.toString()); putLong("nowcast_cache_ts", System.currentTimeMillis()) }
                        }
                    }
                    // Always show the container; if empty, we'll add a neutral placeholder
                    driversFlex.visibility = View.VISIBLE
                    Timber.i("Drivers: rendering %d chips", chips.size)
                    chips.forEach { d ->
                        Timber.i("Driver chip %s = %.3f (w=%.2f)", d.key, (d.value ?: 0.0), (d.contribution ?: 0.0))
                        val tv = TextView(this@MainActivity)
                        val v = d.value ?: 0.0
                        val contribVal = d.contribution ?: 0.0
                        val contrib = String.format(Locale.getDefault(), " (%.0f%%)", kotlin.math.abs(contribVal * 100))
                        val sign = if (v >= 0) "+" else ""
                        val valStr = String.format(Locale.getDefault(), "%.1f", v)
                        val label = DRIVER_LABEL[d.key ?: ""] ?: (d.key ?: "")
                        tv.text = getString(R.string.driver_chip_text_fmt, label, "$sign$valStr", contrib)
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
                            title?.text = getString(R.string.driver_detail_title_fmt, label)
                            val staleText = if (d.stale == true) getString(R.string.stale_label) else ""
                            body?.text = getString(R.string.driver_detail_body_fmt, v, contrib, staleText)
                        }
                        driversFlex.addView(tv)
                    }
                    if (driversFlex.isEmpty()) {
                        // Show a neutral placeholder so the section is visible
                        val tv = TextView(this@MainActivity)
                        tv.text = getString(R.string.nowcast_fmt, "-", (primary?.windowMin ?: 60))
                        tv.setTextColor(ContextCompat.getColor(this@MainActivity, R.color.colorOnSurface))
                        tv.alpha = 0.7f
                        tv.setPadding(16, 10, 16, 10)
                        tv.background = ResourcesCompat.getDrawable(resources, R.drawable.bg_chip, theme)
                        val lp = com.google.android.flexbox.FlexboxLayout.LayoutParams(
                            com.google.android.flexbox.FlexboxLayout.LayoutParams.WRAP_CONTENT,
                            com.google.android.flexbox.FlexboxLayout.LayoutParams.WRAP_CONTENT
                        )
                        lp.setMargins(0, 0, 12, 12)
                        tv.layoutParams = lp
                        driversFlex.addView(tv)
                    }
                    // Ensure layout updates immediately
                    driversFlex.post { driversFlex.requestLayout(); driversFlex.invalidate() }
                }

                // Sessions overlap badge & haptics for news lock transitions
                home.sessions?.let { s ->
                    val badge = findViewById<TextView>(R.id.badgeOverlap)
                    badge?.visibility = if (s.overlapWithNy == true) View.VISIBLE else View.GONE
                }

                // Haptics: bias flip and news lock
                runCatching {
                    if (home.gates?.newsLock == true) {
                        vibrateOnce()
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
                                val targetMs = (event.time_utc.toLongOrNull() ?: 0L) * 1000L
                                val mins = ((targetMs - System.currentTimeMillis()) / 60000).coerceAtLeast(0)
                                pill.text = getString(
                                    R.string.event_countdown_fmt,
                                    event.title,
                                    mins,
                                    event.impact.replaceFirstChar { it.titlecase(Locale.getDefault()) }
                                )
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
                home.gates?.let { gates ->
                    val locked = gates.planLock == true
                    findViewById<View>(R.id.bannerPlanLock)?.visibility = if (locked) View.VISIBLE else View.GONE
                    if (locked) findViewById<TextView>(R.id.tvBannerText)?.text = gates.reason ?: "Your plan is protecting you. New entries locked."
                } ?: run {
                    findViewById<View>(R.id.bannerPlanLock)?.visibility = View.GONE
                }

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
                // Prefer cached display if available; mark as polling/stale instead of hard offline
                val cached = loadHomeCache()
                if (cached != null) {
                    showFromCache(cached)
                    setConnStatusAmber()
                    // If WS is open but API failed, show WS
                    val label = findViewById<TextView>(R.id.statusLabel)
                    label?.text = if (isWsOpen) getString(R.string.ws_label) else getString(R.string.polling_label)
                } else {
                    setConnStatusRed()
                }
                // Banner to indicate degraded state
                findViewById<View>(R.id.bannerFeed)?.visibility = View.VISIBLE
                findViewById<TextView>(R.id.tvBannerFeedText)?.text = getString(R.string.offline_cached_text)
                findViewById<View>(R.id.livePriceCard)?.let { Snackbar.make(it, t.message ?: "Failed to load", Snackbar.LENGTH_LONG).show() }
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

        priceText.text = String.format(Locale.getDefault(), "%.2f", c.last)
        val pct = c.DO?.let { if (it != 0.0) (c.last - it) / it * 100.0 else 0.0 }
        changeText.text = pct?.let { String.format(Locale.getDefault(), "%+.2f%%", it) } ?: "—"
        pct?.let {
            val color = if (it >= 0) ContextCompat.getColor(this, R.color.colorPositive)
            else ContextCompat.getColor(this, R.color.colorNegative)
            changeText.setTextColor(color)
        }
        val sdf = SimpleDateFormat("HH:mm:ss", Locale.getDefault()); sdf.timeZone = TimeZone.getDefault()
        updatedText.text = getString(R.string.updated_fmt, sdf.format(java.util.Date(c.asOf)))
        doVal.text = c.DO?.let { getString(R.string.do_fmt, it) } ?: getString(R.string.do_fmt, Double.NaN).replace("NaN", "—")
        pdhVal.text = c.PDH?.let { getString(R.string.pdh_fmt, it) } ?: getString(R.string.pdh_fmt, Double.NaN).replace("NaN", "—")
        pdlVal.text = c.PDL?.let { getString(R.string.pdl_fmt, it) } ?: getString(R.string.pdl_fmt, Double.NaN).replace("NaN", "—")
        c.closes?.let { miniChart.setSeries(it) }
        findViewById<TextView>(R.id.high24Text)?.text = c.high24h?.let { getString(R.string.h24_fmt, it) } ?: getString(R.string.h24_fmt, Double.NaN).replace("NaN", "—")
        findViewById<TextView>(R.id.low24Text)?.text = c.low24h?.let { getString(R.string.l24_fmt, it) } ?: getString(R.string.l24_fmt, Double.NaN).replace("NaN", "—")
    }

    private fun setConnStatus(colorHex: String) {
        val dot = findViewById<View>(R.id.viewConnStatus) ?: return
        val bg = GradientDrawable()
        bg.shape = GradientDrawable.OVAL
        bg.setColor(colorHex.toColorInt())
        dot.background = bg
    }
    private fun setConnStatusGreen() = setConnStatus("#16A34A")
    private fun setConnStatusAmber() = setConnStatus("#F59E0B")
    private fun setConnStatusRed() = setConnStatus("#DC2626")

    private fun vibrateOnce(durationMs: Long = 30L, amplitude: Int = 60) {
        try {
            val vibrator = if (android.os.Build.VERSION.SDK_INT >= 31) {
                val manager = getSystemService(VibratorManager::class.java)
                manager?.defaultVibrator
            } else {
                @Suppress("DEPRECATION")
                getSystemService(VIBRATOR_SERVICE) as Vibrator
            }
            if (vibrator != null) {
                if (android.os.Build.VERSION.SDK_INT >= 26) {
                    vibrator.vibrate(VibrationEffect.createOneShot(durationMs, amplitude))
                } else {
                    @Suppress("DEPRECATION")
                    vibrator.vibrate(durationMs)
                }
            }
        } catch (_: Throwable) { }
    }
}