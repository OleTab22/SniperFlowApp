package com.example.sniperflow.onboarding

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.CheckBox
import android.widget.Spinner
import com.example.sniperflow.R

class OnboardingLanguageNotificationsFragment : OnboardingFragment() {
    private var spinnerLanguage: Spinner? = null
    private var cbNotifyPlan: CheckBox? = null
    private var cbNotifyGate: CheckBox? = null
    private var cbNotifyEcon: CheckBox? = null
    private var cbNotifyNews: CheckBox? = null
    
    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        val view = inflater.inflate(R.layout.frag_onboarding_language_notifications, container, false)
        spinnerLanguage = view.findViewById(R.id.spinnerLanguage)
        cbNotifyPlan = view.findViewById(R.id.cbNotifyPlan)
        cbNotifyGate = view.findViewById(R.id.cbNotifyGate)
        cbNotifyEcon = view.findViewById(R.id.cbNotifyEcon)
        cbNotifyNews = view.findViewById(R.id.cbNotifyNews)
        
        // Populate language spinner
        val languages = arrayOf(
            getString(R.string.language_english),
            getString(R.string.language_zulu),
            getString(R.string.language_afrikaans)
        )
        val adapter = android.widget.ArrayAdapter(requireContext(), android.R.layout.simple_spinner_item, languages)
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        spinnerLanguage?.adapter = adapter
        
        // Defaults
        cbNotifyPlan?.isChecked = true
        cbNotifyGate?.isChecked = true
        cbNotifyEcon?.isChecked = true
        cbNotifyNews?.isChecked = true
        
        return view
    }
    
    override fun isValid(): Boolean = true
    
    override fun collectData(): Map<String, Any> {
        val lang = spinnerLanguage?.selectedItemPosition?.let {
            when (it) {
                0 -> "en"
                1 -> "zu"
                2 -> "af"
                else -> "en"
            }
        } ?: "en"
        return mapOf(
            "language" to lang,
            "notifyPlanReady" to (cbNotifyPlan?.isChecked ?: true),
            "notifyGatePass" to (cbNotifyGate?.isChecked ?: true),
            "notifyGateBlocked" to (cbNotifyGate?.isChecked ?: true), // Same checkbox for both
            "notifyEcon" to (cbNotifyEcon?.isChecked ?: true),
            "notifyNews" to (cbNotifyNews?.isChecked ?: true)
        )
    }

    override fun onDestroyView() {
        super.onDestroyView()
        spinnerLanguage = null
        cbNotifyPlan = null
        cbNotifyGate = null
        cbNotifyEcon = null
        cbNotifyNews = null
    }
}

