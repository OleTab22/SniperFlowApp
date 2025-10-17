package com.example.sniperflow.notifications

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.example.sniperflow.R
import com.google.android.material.bottomnavigation.BottomNavigationView
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import com.example.sniperflow.network.RetrofitModule
import com.example.sniperflow.BuildConfig
import android.widget.TextView
import com.google.android.flexbox.FlexboxLayout
import androidx.core.content.ContextCompat
import android.content.ActivityNotFoundException
import androidx.core.net.toUri
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.recyclerview.widget.DiffUtil
import android.view.LayoutInflater
import android.view.ViewGroup
import java.util.Locale
import com.example.sniperflow.network.SignalDto

class NotificationsActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_notifications)
        findViewById<androidx.appcompat.widget.Toolbar?>(R.id.toolbar)?.setNavigationOnClickListener { finish() }

        // Bottom navigation wiring
        findViewById<BottomNavigationView>(R.id.bottomNav)?.apply {
            setOnItemSelectedListener { item ->
                when (item.itemId) {
                    R.id.nav_home -> { startActivity(Intent(this@NotificationsActivity, com.example.sniperflow.MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
                    R.id.nav_journal -> { startActivity(Intent(this@NotificationsActivity, com.example.sniperflow.ui.journal.JournalActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
                    R.id.nav_alerts -> true
                    R.id.nav_chart -> { startActivity(Intent(this@NotificationsActivity, com.example.sniperflow.chart.ChartActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
                    R.id.nav_settings -> { startActivity(Intent(this@NotificationsActivity, com.example.sniperflow.settings.SettingsActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
                    else -> false
                }
            }
            setOnItemReselectedListener { }
            selectedItemId = R.id.nav_alerts
        }

        // Fetch and render news + fundamentals
        lifecycleScope.launch {
            runCatching {
                val api = RetrofitModule.api(BuildConfig.BASE_URL)
                val home = api.home()
                val news = fetchNews()
                val signals = runCatching { api.signalsRecent(20) }.getOrElse { emptyList() }

                // News section
                findViewById<TextView?>(R.id.tvNewsTitle)?.text = news?.items?.firstOrNull()?.title ?: "—"
                findViewById<TextView?>(R.id.tvNewsMeta)?.text = news?.items?.firstOrNull()?.src ?: ""
                val link = news?.items?.firstOrNull()?.link
                findViewById<TextView?>(R.id.tvNewsCountdown)?.apply {
                    text = if (link.isNullOrBlank()) "" else "Open"
                    setOnClickListener {
                        if (!link.isNullOrBlank()) {
                            try { startActivity(Intent(Intent.ACTION_VIEW, link.toUri())) } catch (_: ActivityNotFoundException) {}
                        }
                    }
                }

                // Fundamentals chips (drivers)
                val flex = findViewById<FlexboxLayout?>(R.id.fundamentalsFlex)
                flex?.removeAllViews()
                val drivers = home.metrics?.nowcast?.drivers.orEmpty()
                drivers.forEach { d ->
                    val tv = TextView(this@NotificationsActivity)
                    val key = (d.key ?: "").lowercase()
                    val label = when (key) {
                        "dxyz", "dxy" -> "DXY"
                        "realz", "real10y" -> "Real Yields"
                        "vixz", "vix" -> "VIX"
                        "risk_on" -> "Risk-on"
                        "nominalz", "nominal10y" -> "Nominal"
                        else -> d.key ?: ""
                    }
                    val v = d.value ?: 0.0
                    val sign = if (v >= 0) "+" else ""
                    tv.text = getString(R.string.driver_chip_text_fmt, label, sign, String.format(java.util.Locale.getDefault(), "%.1f", v))
                    tv.setPadding(12,8,12,8)
                    tv.background = ContextCompat.getDrawable(this@NotificationsActivity, R.drawable.bg_chip)
                    val lp = FlexboxLayout.LayoutParams(FlexboxLayout.LayoutParams.WRAP_CONTENT, FlexboxLayout.LayoutParams.WRAP_CONTENT)
                    lp.setMargins(0,0,8,8)
                    tv.layoutParams = lp
                    if (d.stale == true) tv.alpha = 0.6f
                    flex?.addView(tv)
                }

                // Signals list
                val rv = findViewById<RecyclerView?>(R.id.rvSignals)
                rv?.layoutManager = LinearLayoutManager(this@NotificationsActivity)
                val adapter = SignalsAdapter()
                rv?.adapter = adapter
                adapter.submitList(signals)
            }
        }
    }

    override fun onResume() {
        super.onResume()
        findViewById<BottomNavigationView>(R.id.bottomNav)?.selectedItemId = R.id.nav_alerts
    }

    private suspend fun fetchNews(): NewsResponse? = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
        runCatching {
            val client = okhttp3.OkHttpClient()
            val req = okhttp3.Request.Builder().url(BuildConfig.BASE_URL + "v1/news").build()
            val resp = client.newCall(req).execute()
            val body = resp.body?.string() ?: return@runCatching null
            val moshi = com.squareup.moshi.Moshi.Builder().add(com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory()).build()
            val adapter = moshi.adapter(NewsResponse::class.java)
            adapter.fromJson(body)
        }.getOrNull()
    }
}

data class NewsItem(val title: String?, val link: String?, val ts: Long?, val src: String?)
data class NewsResponse(val items: List<NewsItem> = emptyList())

private class SignalsAdapter : RecyclerView.Adapter<SignalsAdapter.VH>() {
    private val items = mutableListOf<SignalDto>()
    fun submitList(list: List<SignalDto>) {
        val old = items.toList()
        val diff = DiffUtil.calculateDiff(object : DiffUtil.Callback() {
            override fun getOldListSize() = old.size
            override fun getNewListSize() = list.size
            override fun areItemsTheSame(oPos: Int, nPos: Int): Boolean {
                val a = old[oPos]
                val b = list[nPos]
                return a.id == b.id && a.ts == b.ts
            }
            override fun areContentsTheSame(oPos: Int, nPos: Int): Boolean = old[oPos] == list[nPos]
        }, false)
        items.clear(); items.addAll(list)
        diff.dispatchUpdatesTo(this)
    }

    override fun getItemCount() = items.size
    override fun onCreateViewHolder(p: ViewGroup, t: Int): VH {
        val v = LayoutInflater.from(p.context).inflate(R.layout.item_signal_row, p, false)
        return VH(v)
    }
    override fun onBindViewHolder(h: VH, i: Int) = h.bind(items[i])

    class VH(v: android.view.View) : RecyclerView.ViewHolder(v) {
        private val title = v.findViewById<TextView>(R.id.tvSigTitle)
        private val meta = v.findViewById<TextView>(R.id.tvSigMeta)
        fun bind(s: SignalDto) {
            val sym = s.symbol ?: "XAUUSD"
            val side = (s.side ?: "").uppercase()
            title.text = itemView.context.getString(R.string.sig_title_fmt, sym, side)
            val entry = s.entry?.let { String.format(Locale.getDefault(), "Entry %.2f", it) } ?: "Entry —"
            val sl = s.sl?.let { String.format(Locale.getDefault(), "SL %.2f", it) } ?: "SL —"
            val conf = s.confidence?.let { String.format(Locale.getDefault(), "Conf %.0f%%", it * 100) } ?: "Conf —"
            meta.text = listOf(entry, sl, conf).joinToString("  •  ")
        }
    }
}


