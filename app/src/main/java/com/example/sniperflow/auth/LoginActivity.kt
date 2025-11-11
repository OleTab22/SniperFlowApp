package com.example.sniperflow.auth

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import androidx.lifecycle.lifecycleScope
import com.example.sniperflow.R
import com.example.sniperflow.MainActivity
import com.google.android.material.tabs.TabLayout
import com.google.android.material.textfield.TextInputLayout
import kotlinx.coroutines.launch
import timber.log.Timber
import com.example.sniperflow.util.LocaleAwareActivity

class LoginActivity : LocaleAwareActivity() {
    private lateinit var repo: AuthRepository

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_login)

        repo = AuthRepository(this)

        val email = findViewById<EditText>(R.id.email)
        val password = findViewById<EditText>(R.id.password)
        val emailLayout = findViewById<TextInputLayout>(R.id.emailLayout)
        val passwordLayout = findViewById<TextInputLayout>(R.id.passwordLayout)
        val login = findViewById<Button>(R.id.login)
        val progress = findViewById<android.view.View>(R.id.progress)

        // Toolbar back
        findViewById<androidx.appcompat.widget.Toolbar>(R.id.toolbar)?.setNavigationOnClickListener { finish() }

        // Tabs: ensure Login is selected and switch to Register when chosen
        val tabs = findViewById<TabLayout>(R.id.tabLayout)
        tabs.getTabAt(0)?.select()
        tabs.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: TabLayout.Tab) {
                if (tab.position == 1) {
                    startActivity(Intent(this@LoginActivity, RegisterActivity::class.java))
                    finish()
                }
            }
            override fun onTabUnselected(tab: TabLayout.Tab) {}
            override fun onTabReselected(tab: TabLayout.Tab) {}
        })

        login.setOnClickListener {
            val e = email.text.toString().trim()
            val p = password.text.toString()

            var valid = true
            if (e.isEmpty() || !e.contains("@")) { emailLayout.error = "Enter valid email"; valid = false } else emailLayout.error = null
            if (p.length < 6) { passwordLayout.error = "Min 6 chars"; valid = false } else passwordLayout.error = null
            if (!valid) return@setOnClickListener

            login.isEnabled = false
            progress.visibility = android.view.View.VISIBLE
            lifecycleScope.launch {
                try {
                    repo.login(e, p)
                    Toast.makeText(this@LoginActivity, "Login success", Toast.LENGTH_SHORT).show()
                    startActivity(Intent(this@LoginActivity, MainActivity::class.java).apply {
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
                    })
                } catch (t: Throwable) {
                    Timber.e(t, "login failed")
                    Toast.makeText(this@LoginActivity, t.message ?: "Login failed", Toast.LENGTH_SHORT).show()
                } finally {
                    login.isEnabled = true
                    progress.visibility = android.view.View.GONE
                }
            }
        }

        // Forgot password flow
        findViewById<android.widget.TextView>(R.id.forgot).setOnClickListener {
            val e = email.text.toString().trim()
            if (e.isEmpty() || !e.contains("@")) {
                emailLayout.error = "Enter valid email"
                return@setOnClickListener
            }
            lifecycleScope.launch {
                try {
                    repo.sendPasswordReset(e)
                    Toast.makeText(this@LoginActivity, "Reset email sent", Toast.LENGTH_SHORT).show()
                } catch (t: Throwable) {
                    Timber.e(t, "reset failed")
                    Toast.makeText(this@LoginActivity, t.message ?: "Reset failed", Toast.LENGTH_SHORT).show()
                }
            }
        }
    }
}


