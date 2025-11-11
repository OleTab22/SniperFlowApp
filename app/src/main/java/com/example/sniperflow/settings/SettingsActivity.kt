package com.example.sniperflow.settings

import android.os.Bundle
import androidx.appcompat.app.AlertDialog
import com.example.sniperflow.R
import com.example.sniperflow.data.user.UserProfileRepository
import com.example.sniperflow.ui.journal.CsvExporter
import android.widget.Button
import android.widget.CheckBox
import android.widget.Spinner
import com.example.sniperflow.BuildConfig
import com.example.sniperflow.network.RetrofitModule
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import com.google.android.material.bottomnavigation.BottomNavigationView
import android.content.Intent
import com.google.android.material.snackbar.Snackbar
import com.google.android.material.textfield.MaterialAutoCompleteTextView
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.example.sniperflow.util.LocaleAwareActivity
import com.example.sniperflow.util.LocaleManager

class SettingsActivity : LocaleAwareActivity() {
    private lateinit var repository: SettingsRepository
    private lateinit var profileRepo: UserProfileRepository

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        repository = SettingsRepository(this)
        profileRepo = UserProfileRepository.create(this)

        val epsilonLayout = findViewById<TextInputLayout>(R.id.epsilonLayout)
        val cooldownLayout = findViewById<TextInputLayout>(R.id.cooldownLayout)
        val epsilonInput = findViewById<TextInputEditText>(R.id.epsilonInput)
        val cooldownInput = findViewById<TextInputEditText>(R.id.cooldownInput)
        val tzInput = findViewById<MaterialAutoCompleteTextView>(R.id.tzInput)
        val saveBtn = findViewById<android.view.View>(R.id.saveBtn)

        // Optional: quick Test connection button if present (resolve by name to avoid compile-time R error)
        @Suppress("DiscouragedApi")
        val testId = resources.getIdentifier("btnTest", "id", packageName)
        val testBtn = if (testId != 0) findViewById<Button>(testId) else null

        val (savedEpsilon, savedCooldown) = repository.load()
        epsilonInput?.setText(savedEpsilon.toString())
        cooldownInput?.setText(savedCooldown.toString())
        // Populate timezone dropdown
        val tzIds = java.util.TimeZone.getAvailableIDs().sorted()
        val tzAdapter = android.widget.ArrayAdapter(this, android.R.layout.simple_list_item_1, tzIds)
        tzInput?.setAdapter(tzAdapter)
        tzInput?.setText(repository.loadTimezone(), false)

        saveBtn?.setOnClickListener { view ->
            val epsilon = epsilonInput?.text?.toString()?.toDoubleOrNull()
            val cooldown = cooldownInput?.text?.toString()?.toLongOrNull()
            val tzId = tzInput?.text?.toString()?.trim().orEmpty().ifBlank { null }

            var valid = true
            if (!Validators.isEpsilonValid(epsilon)) {
                epsilonLayout?.error = "0.01–5.0"
                valid = false
            } else {
                epsilonLayout?.error = null
            }

            if (!Validators.isCooldownValid(cooldown)) {
                cooldownLayout?.error = "10000–900000"
                valid = false
            } else {
                cooldownLayout?.error = null
            }

            if (!valid) return@setOnClickListener

            repository.save(epsilon!!, cooldown!!, tzId)
            tzId?.let { com.example.sniperflow.domain.metrics.UserTimezone.tzId = it }

            lifecycleScope.launch {
                val profile = profileRepo.getProfile()
                val gateCheckbox = findViewById<CheckBox>(R.id.cbNotifyGate)
                val maxLossR = findViewById<TextInputEditText>(R.id.maxLossRInput)?.text?.toString()?.toDoubleOrNull() ?: profile.maxDailyLossR
                val maxTrades = findViewById<TextInputEditText>(R.id.maxTradesInput)?.text?.toString()?.toIntOrNull() ?: profile.maxTradesPerSession
                val newLang = getSelectedLanguage()

                profileRepo.updateProfile(
                    profile.copy(
                        maxDailyLossR = maxLossR,
                        maxTradesPerSession = maxTrades,
                        newsLockCpi = findViewById<CheckBox>(R.id.cbNewsLockCpi)?.isChecked ?: profile.newsLockCpi,
                        newsLockNfp = findViewById<CheckBox>(R.id.cbNewsLockNfp)?.isChecked ?: profile.newsLockNfp,
                        newsLockFomc = findViewById<CheckBox>(R.id.cbNewsLockFomc)?.isChecked ?: profile.newsLockFomc,
                        notifyPlanReady = findViewById<CheckBox>(R.id.cbNotifyPlan)?.isChecked ?: profile.notifyPlanReady,
                        notifyGatePass = gateCheckbox?.isChecked ?: profile.notifyGatePass,
                        notifyGateBlocked = gateCheckbox?.isChecked ?: profile.notifyGateBlocked,
                        notifyEcon = findViewById<CheckBox>(R.id.cbNotifyEcon)?.isChecked ?: profile.notifyEcon,
                        notifyNews = findViewById<CheckBox>(R.id.cbNotifyNews)?.isChecked ?: profile.notifyNews,
                        quietHoursEnabled = findViewById<CheckBox>(R.id.cbQuietHoursEnabled)?.isChecked ?: profile.quietHoursEnabled,
                        quietHoursStart = findViewById<TextInputEditText>(R.id.quietHoursStartInput)?.text?.toString()?.toIntOrNull() ?: profile.quietHoursStart,
                        quietHoursEnd = findViewById<TextInputEditText>(R.id.quietHoursEndInput)?.text?.toString()?.toIntOrNull() ?: profile.quietHoursEnd,
                        language = newLang
                    )
                )

                if (profile.language != newLang) {
                    LocaleManager.updateLocale(applicationContext, newLang)
                    Snackbar.make(view, "Language changed - restarting...", Snackbar.LENGTH_SHORT).show()
                    finish()
                    startActivity(intent)
                } else {
                    Snackbar.make(view, "Saved", Snackbar.LENGTH_SHORT).show()
                }
            }
        }
        
        // Load profile settings
        lifecycleScope.launch {
            val profile = profileRepo.getProfile()
            LocaleManager.setStoredLanguage(applicationContext, profile.language)
            findViewById<TextInputEditText>(R.id.maxLossRInput)?.setText(profile.maxDailyLossR.toString())
            findViewById<TextInputEditText>(R.id.maxTradesInput)?.setText(profile.maxTradesPerSession.toString())
            findViewById<CheckBox>(R.id.cbNewsLockCpi)?.isChecked = profile.newsLockCpi
            findViewById<CheckBox>(R.id.cbNewsLockNfp)?.isChecked = profile.newsLockNfp
            findViewById<CheckBox>(R.id.cbNewsLockFomc)?.isChecked = profile.newsLockFomc
            findViewById<CheckBox>(R.id.cbNotifyPlan)?.isChecked = profile.notifyPlanReady
            findViewById<CheckBox>(R.id.cbNotifyGate)?.isChecked = profile.notifyGatePass
            findViewById<CheckBox>(R.id.cbNotifyEcon)?.isChecked = profile.notifyEcon
            findViewById<CheckBox>(R.id.cbNotifyNews)?.isChecked = profile.notifyNews
            findViewById<CheckBox>(R.id.cbQuietHoursEnabled)?.isChecked = profile.quietHoursEnabled
            findViewById<TextInputEditText>(R.id.quietHoursStartInput)?.setText(profile.quietHoursStart.toString())
            findViewById<TextInputEditText>(R.id.quietHoursEndInput)?.setText(profile.quietHoursEnd.toString())
            
            // Language spinner
            val spinner = findViewById<Spinner>(R.id.spinnerLanguage)
            val languages = arrayOf(
                getString(R.string.language_english),
                getString(R.string.language_zulu),
                getString(R.string.language_afrikaans)
            )
            val adapter = android.widget.ArrayAdapter(this@SettingsActivity, android.R.layout.simple_spinner_item, languages)
            adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
            spinner?.adapter = adapter
            spinner?.setSelection(when (profile.language) {
                "zu" -> 1
                "af" -> 2
                else -> 0
            })
        }
        
        // Privacy buttons
        findViewById<Button>(R.id.btnExportData)?.setOnClickListener {
            lifecycleScope.launch {
                val dao = (application as com.example.sniperflow.App).db.journalDao()
                val rows = dao.listAll()
                val file = CsvExporter.export(this@SettingsActivity, rows)
                Snackbar.make(it, getString(R.string.privacy_export_success) + ": ${file.absolutePath}", Snackbar.LENGTH_LONG).show()
            }
        }
        
        findViewById<Button>(R.id.btnDeleteData)?.setOnClickListener {
            AlertDialog.Builder(this)
                .setTitle(getString(R.string.privacy_delete))
                .setMessage(getString(R.string.privacy_delete_confirm))
                .setPositiveButton("Delete") { _, _ ->
                    lifecycleScope.launch {
                        val dao = (application as com.example.sniperflow.App).db.journalDao()
                        dao.listAll().forEach { dao.delete(it) }
                        Snackbar.make(it, "All data deleted", Snackbar.LENGTH_SHORT).show()
                    }
                }
                .setNegativeButton("Cancel", null)
                .show()
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
    
    private fun getSelectedLanguage(): String {
        val spinner = findViewById<Spinner>(R.id.spinnerLanguage)
        return when (spinner?.selectedItemPosition) {
            1 -> "zu"
            2 -> "af"
            else -> "en"
        }
    }
}


