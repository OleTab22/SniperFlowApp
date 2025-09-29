package com.example.sniperflow.levels

import android.os.Bundle
import android.view.View
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.example.sniperflow.R
import com.example.sniperflow.network.RetrofitModule
import com.example.sniperflow.settings.SettingsRepository
import com.google.android.material.snackbar.Snackbar
import com.google.android.material.button.MaterialButton
import com.google.android.material.progressindicator.CircularProgressIndicator
import kotlinx.coroutines.launch
import timber.log.Timber

class LevelsActivity : AppCompatActivity() {
    private var lastFetchAt: Long = 0L
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_levels)

        val baseUrl = getString(R.string.base_url)
        val api = RetrofitModule.api(baseUrl)
        val settings = SettingsRepository(this)

        val fetchBtn = findViewById<MaterialButton>(R.id.fetchBtn)
        val progress = findViewById<CircularProgressIndicator>(R.id.progress)
        val asOfText = findViewById<TextView>(R.id.asOfText)
        val lastPriceText = findViewById<TextView>(R.id.lastPriceText)
        val doText = findViewById<TextView>(R.id.doText)
        val pdhText = findViewById<TextView>(R.id.pdhText)
        val pdlText = findViewById<TextView>(R.id.pdlText)

        fun setLoading(loading: Boolean) {
            progress.visibility = if (loading) View.VISIBLE else View.GONE
            fetchBtn.isEnabled = !loading
        }

        fun fmtTriplet(title: String, triplet: com.example.sniperflow.network.LevelsTriplet?): String {
            if (triplet == null) return "$title: -"
            fun d(v: Double?) = v?.let { String.format("%.2f", it) } ?: "-"
            return "$title: DO ${d(triplet.DO)} | PDH ${d(triplet.PDH)} | PDL ${d(triplet.PDL)}"
        }

        val dailyText = findViewById<TextView>(R.id.dailyText)
        val sydneyText = findViewById<TextView>(R.id.sydneyText)
        val tokyoText = findViewById<TextView>(R.id.tokyoText)
        val londonText = findViewById<TextView>(R.id.londonText)
        val newyorkText = findViewById<TextView>(R.id.newyorkText)

        fetchBtn.setOnClickListener { view ->
            val now = System.currentTimeMillis()
            val cooldownMs = settings.load().second
            if (now - lastFetchAt < cooldownMs) {
                Snackbar.make(view, "Please wait…", Snackbar.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            setLoading(true)
            lifecycleScope.launch {
                try {
                    Timber.i("Fetching intraday levels…")
                    val resp = api.levels("XAUUSD")
                    Timber.i("Levels received: %s", resp)
                    asOfText.text = "asOf: ${resp.asOf}"
                    lastPriceText.text = String.format("lastPrice: %.2f", resp.lastPrice)
                    doText.text = "DO: ${resp.DO?.let { String.format("%.2f", it) } ?: "-"}"
                    pdhText.text = "PDH: ${resp.PDH?.let { String.format("%.2f", it) } ?: "-"}"
                    pdlText.text = "PDL: ${resp.PDL?.let { String.format("%.2f", it) } ?: "-"}"

                    Timber.i("Fetching session levels…")
                    val sessions = api.levelsSessions("XAUUSD")
                    dailyText.text = fmtTriplet("Daily", sessions.daily)
                    sydneyText.text = fmtTriplet("Sydney", sessions.sessions?.sydney)
                    tokyoText.text = fmtTriplet("Tokyo", sessions.sessions?.tokyo)
                    londonText.text = fmtTriplet("London", sessions.sessions?.london)
                    newyorkText.text = fmtTriplet("New York", sessions.sessions?.newyork)

                    lastFetchAt = now
                } catch (t: Throwable) {
                    Timber.e(t, "Failed to fetch levels")
                    Snackbar.make(view, t.message ?: "Failed to fetch", Snackbar.LENGTH_LONG).show()
                } finally {
                    setLoading(false)
                }
            }
        }
    }
}


