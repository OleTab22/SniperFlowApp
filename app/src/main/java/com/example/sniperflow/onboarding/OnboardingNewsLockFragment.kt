package com.example.sniperflow.onboarding

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.CheckBox
import com.example.sniperflow.R

class OnboardingNewsLockFragment : OnboardingFragment() {
    private var cbCpi: CheckBox? = null
    private var cbNfp: CheckBox? = null
    private var cbFomc: CheckBox? = null
    
    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        val view = inflater.inflate(R.layout.frag_onboarding_news_lock, container, false)
        cbCpi = view.findViewById(R.id.cbCpi)
        cbNfp = view.findViewById(R.id.cbNfp)
        cbFomc = view.findViewById(R.id.cbFomc)
        
        // Defaults: all enabled
        cbCpi?.isChecked = true
        cbNfp?.isChecked = true
        cbFomc?.isChecked = true
        
        return view
    }
    
    override fun isValid(): Boolean = true // Optional step
    
    override fun collectData(): Map<String, Any> {
        return mapOf(
            "newsLockCpi" to (cbCpi?.isChecked ?: true),
            "newsLockNfp" to (cbNfp?.isChecked ?: true),
            "newsLockFomc" to (cbFomc?.isChecked ?: true)
        )
    }

    override fun onDestroyView() {
        super.onDestroyView()
        cbCpi = null
        cbNfp = null
        cbFomc = null
    }
}


