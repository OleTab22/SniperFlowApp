package com.example.sniperflow.onboarding

import android.content.Intent
import android.os.Bundle
import android.view.View
import androidx.fragment.app.Fragment
import androidx.viewpager2.adapter.FragmentStateAdapter
import androidx.viewpager2.widget.ViewPager2
import com.example.sniperflow.MainActivity
import com.example.sniperflow.R
import com.example.sniperflow.data.user.UserProfileRepository
import com.example.sniperflow.util.LocaleAwareActivity
import com.example.sniperflow.util.LocaleManager
import com.google.android.material.button.MaterialButton
import com.google.android.material.tabs.TabLayout
import com.google.android.material.tabs.TabLayoutMediator
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

// Onboarding wizard - 4 steps to set up trading plan
// Can't finish without setting risk limits
class OnboardingActivity : LocaleAwareActivity() {
    private lateinit var viewPager: ViewPager2
    private lateinit var tabLayout: TabLayout
    private lateinit var btnNext: MaterialButton
    private lateinit var btnBack: MaterialButton
    private lateinit var profileRepo: UserProfileRepository
    private var pagerMediator: TabLayoutMediator? = null
    private lateinit var pageChangeCallback: ViewPager2.OnPageChangeCallback
    
    private val fragments = listOf(
        OnboardingSessionsFragment(),
        OnboardingRiskFragment(),
        OnboardingNewsLockFragment(),
        OnboardingLanguageNotificationsFragment()
    )
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_onboarding)
        
        profileRepo = UserProfileRepository.create(this)
        
        viewPager = findViewById(R.id.viewPager)
        tabLayout = findViewById(R.id.tabLayout)
        btnNext = findViewById(R.id.btnNext)
        btnBack = findViewById(R.id.btnBack)
        
        viewPager.adapter = OnboardingPagerAdapter(this, fragments)
        pagerMediator = TabLayoutMediator(tabLayout, viewPager) { tab, position ->
            tab.text = when (position) {
                0 -> "Sessions"
                1 -> "Risk"
                2 -> "News Lock"
                3 -> "Language"
                else -> ""
            }
        }.apply { attach() }
        
        btnNext.setOnClickListener {
            if (viewPager.currentItem < fragments.size - 1) {
                viewPager.currentItem++
            } else {
                finishOnboarding()
            }
        }
        
        btnBack.setOnClickListener {
            if (viewPager.currentItem > 0) {
                viewPager.currentItem--
            }
        }
        
        updateButtonStates()
        pageChangeCallback = object : ViewPager2.OnPageChangeCallback() {
            override fun onPageSelected(position: Int) {
                updateButtonStates()
            }
        }
        viewPager.registerOnPageChangeCallback(pageChangeCallback)
    }
    
    override fun onDestroy() {
        super.onDestroy()
        pagerMediator?.detach()
        if (::pageChangeCallback.isInitialized) {
            viewPager.unregisterOnPageChangeCallback(pageChangeCallback)
        }
    }

    private fun updateButtonStates() {
        val current = viewPager.currentItem
        btnBack.visibility = if (current > 0) View.VISIBLE else View.GONE
        btnNext.text = if (current == fragments.size - 1) getString(R.string.action_finish) else getString(R.string.action_next)
        
        // Check if current step is valid
        val fragment = fragments[current]
        btnNext.isEnabled = fragment.isValid()
    }
    
    private fun finishOnboarding() {
        CoroutineScope(Dispatchers.Main).launch {
            val profile = profileRepo.getProfile()
            
            // Collect data from all fragments
            val sessionsData = (fragments[0] as? OnboardingSessionsFragment)?.collectData() ?: emptyMap()
            val riskData = (fragments[1] as? OnboardingRiskFragment)?.collectData() ?: emptyMap()
            val newsData = (fragments[2] as? OnboardingNewsLockFragment)?.collectData() ?: emptyMap()
            val langData = (fragments[3] as? OnboardingLanguageNotificationsFragment)?.collectData() ?: emptyMap()
            
            // Update profile with collected data
            profileRepo.updateProfile(
                profile.copy(
                    onboardingCompleted = true,
                    planLockEnabled = true,
                    asiaEnabled = sessionsData["asiaEnabled"] as? Boolean ?: true,
                    londonEnabled = sessionsData["londonEnabled"] as? Boolean ?: true,
                    newyorkEnabled = sessionsData["newyorkEnabled"] as? Boolean ?: true,
                    maxDailyLossR = riskData["maxDailyLossR"] as? Double ?: 5.0,
                    maxTradesPerSession = riskData["maxTradesPerSession"] as? Int ?: 3,
                    newsLockCpi = newsData["newsLockCpi"] as? Boolean ?: true,
                    newsLockNfp = newsData["newsLockNfp"] as? Boolean ?: true,
                    newsLockFomc = newsData["newsLockFomc"] as? Boolean ?: true,
                    language = langData["language"] as? String ?: "en",
                    notifyPlanReady = langData["notifyPlanReady"] as? Boolean ?: true,
                    notifyGatePass = langData["notifyGatePass"] as? Boolean ?: true,
                    notifyGateBlocked = langData["notifyGateBlocked"] as? Boolean ?: true,
                    notifyEcon = langData["notifyEcon"] as? Boolean ?: true,
                    notifyNews = langData["notifyNews"] as? Boolean ?: true
                )
            )
            
            // Apply language change if needed
            val selectedLang = langData["language"] as? String ?: "en"
            LocaleManager.updateLocale(applicationContext, selectedLang)
            
            startActivity(Intent(this@OnboardingActivity, MainActivity::class.java))
            finish()
        }
    }
    
    private class OnboardingPagerAdapter(
        activity: OnboardingActivity,
        private val fragments: List<Fragment>
    ) : FragmentStateAdapter(activity) {
        override fun getItemCount() = fragments.size
        override fun createFragment(position: Int) = fragments[position]
    }
}

// Base class for onboarding fragments
abstract class OnboardingFragment : Fragment() {
    abstract fun isValid(): Boolean
    abstract fun collectData(): Map<String, Any>
}

