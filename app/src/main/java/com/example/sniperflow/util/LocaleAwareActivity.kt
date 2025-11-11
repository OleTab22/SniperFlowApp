package com.example.sniperflow.util

import android.content.Context
import android.content.res.Configuration
import androidx.appcompat.app.AppCompatActivity
import android.os.Bundle
import android.view.WindowManager

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

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        allowScreenCapture()
    }

    override fun onPostCreate(savedInstanceState: Bundle?) {
        super.onPostCreate(savedInstanceState)
        allowScreenCapture()
    }

    override fun onResume() {
        super.onResume()
        val current = LocaleManager.getStoredLanguage(this)
        if (current != appliedLanguage) {
            recreate()
        }
        allowScreenCapture()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) {
            allowScreenCapture()
        }
    }

    private fun allowScreenCapture() {
        runCatching { window?.clearFlags(WindowManager.LayoutParams.FLAG_SECURE) }
    }
}
