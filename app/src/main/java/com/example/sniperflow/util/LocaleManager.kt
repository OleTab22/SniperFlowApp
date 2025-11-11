package com.example.sniperflow.util

import android.content.Context
import android.content.res.Configuration
import androidx.core.content.edit
import java.util.Locale

object LocaleManager {
    private const val PREFS_NAME = "sniperflow_locale"
    private const val KEY_LANGUAGE = "language"
    private val SUPPORTED = setOf("en", "zu", "af")

    fun normalize(language: String?): String {
        val raw = language?.lowercase(Locale.US) ?: Locale.getDefault().language
        return if (raw in SUPPORTED) raw else "en"
    }

    fun getStoredLanguage(context: Context): String {
        val prefs = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        return normalize(prefs.getString(KEY_LANGUAGE, null))
    }

    fun setStoredLanguage(context: Context, language: String) {
        val normalized = normalize(language)
        context.applicationContext
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit { putString(KEY_LANGUAGE, normalized) }
    }

    fun wrapContext(context: Context): Context {
        val language = getStoredLanguage(context)
        return updateResources(context, language)
    }

    fun updateLocale(context: Context, language: String) {
        setStoredLanguage(context, language)
        updateResources(context, normalize(language))
    }

    fun getCurrentLocale(context: Context): Locale {
        val language = getStoredLanguage(context)
        val locale = Locale.forLanguageTag(language)
        return if (locale.language.isNullOrBlank()) Locale.ENGLISH else locale
    }

    private fun updateResources(context: Context, language: String): Context {
        val locale = Locale.forLanguageTag(language)
        Locale.setDefault(locale)
        val config = Configuration(context.resources.configuration)
        config.setLocale(locale)
        config.setLayoutDirection(locale)
        return context.createConfigurationContext(config)
    }
}

