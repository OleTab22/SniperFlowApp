package com.example.sniperflow.settings

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.example.sniperflow.R
import com.google.android.material.snackbar.Snackbar
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import android.widget.Button
import com.example.sniperflow.BuildConfig
import com.example.sniperflow.network.RetrofitModule
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import com.google.android.material.bottomnavigation.BottomNavigationView
import android.content.Intent

class SettingsActivity : AppCompatActivity() {
    private lateinit var repository: SettingsRepository

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        repository = SettingsRepository(this)

        val epsilonLayout = findViewById<TextInputLayout>(R.id.epsilonLayout)
        val cooldownLayout = findViewById<TextInputLayout>(R.id.cooldownLayout)
        val epsilonInput = findViewById<TextInputEditText>(R.id.epsilonInput)
        val cooldownInput = findViewById<TextInputEditText>(R.id.cooldownInput)
        val tzInput = findViewById<android.widget.AutoCompleteTextView>(R.id.tzInput)
        val saveBtn = findViewById<android.view.View>(R.id.saveBtn)

        // Optional: quick Test connection button if present (resolve by name to avoid compile-time R error)
        @Suppress("DiscouragedApi")
        val testId = resources.getIdentifier("btnTest", "id", packageName)
        val testBtn = if (testId != 0) findViewById<Button>(testId) else null

        val (savedEpsilon, savedCooldown) = repository.load()
        epsilonInput.setText(savedEpsilon.toString())
        cooldownInput.setText(savedCooldown.toString())
        // Populate timezone dropdown
        val tzIds = java.util.TimeZone.getAvailableIDs().sorted()
        val tzAdapter = android.widget.ArrayAdapter(this, android.R.layout.simple_list_item_1, tzIds)
        tzInput.setAdapter(tzAdapter)
        tzInput.setText(repository.loadTimezone(), false)

        saveBtn.setOnClickListener { view ->
            val epsilon = epsilonInput.text?.toString()?.toDoubleOrNull()
            val cooldown = cooldownInput.text?.toString()?.toLongOrNull()
            val tzId = tzInput.text?.toString()?.trim().orEmpty().ifBlank { null }

            var valid = true
            if (!Validators.isEpsilonValid(epsilon)) {
                epsilonLayout.error = "0.01–5.0"
                valid = false
            } else {
                epsilonLayout.error = null
            }

            if (!Validators.isCooldownValid(cooldown)) {
                cooldownLayout.error = "10000–900000"
                valid = false
            } else {
                cooldownLayout.error = null
            }

            if (valid) {
                repository.save(epsilon!!, cooldown!!, tzId)
                // Apply globally so session logic updates immediately
                tzId?.let { com.example.sniperflow.domain.metrics.UserTimezone.tzId = it }
                Snackbar.make(view, "Saved", Snackbar.LENGTH_SHORT).show()
            }
        }

        testBtn?.setOnClickListener { v ->
            // Ping /health to verify current BASE_URL is reachable
            val api = RetrofitModule.api(BuildConfig.BASE_URL)
            lifecycleScope.launch {
                runCatching { api.health() }
                    .onSuccess { Snackbar.make(v, "API OK", Snackbar.LENGTH_SHORT).show() }
                    .onFailure { Snackbar.make(v, "API unreachable", Snackbar.LENGTH_SHORT).show() }
            }
        }

        // Bottom navigation wiring
        findViewById<BottomNavigationView>(R.id.bottomNav)?.apply {
            setOnItemSelectedListener { item ->
                when (item.itemId) {
                    R.id.nav_home -> { startActivity(Intent(this@SettingsActivity, com.example.sniperflow.MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
                    R.id.nav_journal -> { startActivity(Intent(this@SettingsActivity, com.example.sniperflow.ui.journal.JournalActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
                    R.id.nav_alerts -> { startActivity(Intent(this@SettingsActivity, com.example.sniperflow.notifications.NotificationsActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
                    R.id.nav_chart -> { startActivity(Intent(this@SettingsActivity, com.example.sniperflow.chart.ChartActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
                    R.id.nav_settings -> true
                    else -> false
                }
            }
            setOnItemReselectedListener { }
            selectedItemId = R.id.nav_settings
        }
    }

    override fun onResume() {
        super.onResume()
        findViewById<BottomNavigationView>(R.id.bottomNav)?.selectedItemId = R.id.nav_settings
    }
}


