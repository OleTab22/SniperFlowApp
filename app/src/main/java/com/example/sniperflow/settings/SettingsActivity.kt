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
        val saveBtn = findViewById<android.view.View>(R.id.saveBtn)

        // Optional: quick Test connection button if present (resolve by name to avoid compile-time R error)
        val testId = resources.getIdentifier("btnTest", "id", packageName)
        val testBtn = if (testId != 0) findViewById<Button>(testId) else null

        val (savedEpsilon, savedCooldown) = repository.load()
        epsilonInput.setText(savedEpsilon.toString())
        cooldownInput.setText(savedCooldown.toString())

        saveBtn.setOnClickListener { view ->
            val epsilon = epsilonInput.text?.toString()?.toDoubleOrNull()
            val cooldown = cooldownInput.text?.toString()?.toLongOrNull()

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
                repository.save(epsilon!!, cooldown!!)
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
    }
}


