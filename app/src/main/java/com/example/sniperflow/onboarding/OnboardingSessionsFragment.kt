package com.example.sniperflow.onboarding

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.CheckBox
import com.example.sniperflow.R

class OnboardingSessionsFragment : OnboardingFragment() {
    private var cbAsia: CheckBox? = null
    private var cbLondon: CheckBox? = null
    private var cbNewYork: CheckBox? = null
    
    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        val view = inflater.inflate(R.layout.frag_onboarding_sessions, container, false)
        cbAsia = view.findViewById(R.id.cbAsia)
        cbLondon = view.findViewById(R.id.cbLondon)
        cbNewYork = view.findViewById(R.id.cbNewYork)
        
        // SAST defaults
        cbAsia?.isChecked = true
        cbLondon?.isChecked = true
        cbNewYork?.isChecked = true
        
        return view
    }
    
    override fun isValid(): Boolean {
        val asia = cbAsia?.isChecked ?: true
        val london = cbLondon?.isChecked ?: true
        val newYork = cbNewYork?.isChecked ?: true
        return asia || london || newYork
    }
    
    override fun collectData(): Map<String, Any> {
        return mapOf(
            "asiaEnabled" to (cbAsia?.isChecked ?: true),
            "londonEnabled" to (cbLondon?.isChecked ?: true),
            "newyorkEnabled" to (cbNewYork?.isChecked ?: true)
        )
    }

    override fun onDestroyView() {
        super.onDestroyView()
        cbAsia = null
        cbLondon = null
        cbNewYork = null
    }
}


