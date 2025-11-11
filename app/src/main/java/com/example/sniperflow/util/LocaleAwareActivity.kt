package com.example.sniperflow.util

import android.content.Context
import android.content.res.Configuration
import androidx.appcompat.app.AppCompatActivity

abstract class LocaleAwareActivity : AppCompatActivity() {
    private var appliedLanguage: String = LocaleManager.normalize(null)

    override fun attachBaseContext(newBase: Context) {
        appliedLanguage = LocaleManager.getStoredLanguage(newBase)
        val wrapped = LocaleManager.wrapContext(newBase)
        super.attachBaseContext(wrapped)
    }

    override fun applyOverrideConfiguration(overrideConfiguration: Configuration?) {
        if (overrideConfiguration != null) {
            val locale = LocaleManager.getCurrentLocale(this)
            overrideConfiguration.setLocale(locale)
            overrideConfiguration.setLayoutDirection(locale)
        }
        super.applyOverrideConfiguration(overrideConfiguration)
    }

    override fun onResume() {
        super.onResume()
        val current = LocaleManager.getStoredLanguage(this)
        if (current != appliedLanguage) {
            recreate()
        }
    }
}
