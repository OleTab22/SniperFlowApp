package com.example.sniperflow.onboarding

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import com.example.sniperflow.R

class OnboardingRiskFragment : OnboardingFragment() {
    private var etMaxLossR: EditText? = null
    private var etMaxTrades: EditText? = null
    
    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        val view = inflater.inflate(R.layout.frag_onboarding_risk, container, false)
        etMaxLossR = view.findViewById(R.id.etMaxLossR)
        etMaxTrades = view.findViewById(R.id.etMaxTrades)
        
        // Defaults
        etMaxLossR?.setText("5.0")
        etMaxTrades?.setText("3")
        
        return view
    }
    
    override fun isValid(): Boolean {
        val lossR = etMaxLossR?.text?.toString()?.toDoubleOrNull()
        val trades = etMaxTrades?.text?.toString()?.toIntOrNull()
        return lossR != null && lossR > 0 && trades != null && trades > 0
    }
    
    override fun collectData(): Map<String, Any> {
        return mapOf(
            "maxDailyLossR" to (etMaxLossR?.text?.toString()?.toDoubleOrNull() ?: 5.0),
            "maxTradesPerSession" to (etMaxTrades?.text?.toString()?.toIntOrNull() ?: 3)
        )
    }

    override fun onDestroyView() {
        super.onDestroyView()
        etMaxLossR = null
        etMaxTrades = null
    }
}


