package com.example.sniperflow.auth

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.example.sniperflow.R
import com.example.sniperflow.MainActivity
import com.google.android.material.tabs.TabLayout
import com.google.android.material.textfield.TextInputLayout
  import kotlinx.coroutines.launch
import timber.log.Timber

class RegisterActivity : AppCompatActivity() {
    private lateinit var repo: AuthRepository

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_register)

        repo = AuthRepository(this)

        val email = findViewById<EditText>(R.id.email)
        val password = findViewById<EditText>(R.id.password)
        val confirm = findViewById<EditText>(R.id.confirm)
        val emailLayout = findViewById<TextInputLayout>(R.id.emailLayout)
        val passwordLayout = findViewById<TextInputLayout>(R.id.passwordLayout)
        val confirmLayout = findViewById<TextInputLayout>(R.id.confirmLayout)
        val register = findViewById<Button>(R.id.register)
        // Tab navigation: switch to Login
        val tabs = findViewById<TabLayout>(R.id.tabLayout)
        tabs.getTabAt(1)?.select()
        tabs.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
                override fun onTabSelected(tab: com.google.android.material.tabs.TabLayout.Tab) {
                    if (tab.position == 0) {
                        startActivity(Intent(this@RegisterActivity, LoginActivity::class.java))
                        finish()
                    }
                }
                override fun onTabUnselected(tab: com.google.android.material.tabs.TabLayout.Tab) {}
                override fun onTabReselected(tab: com.google.android.material.tabs.TabLayout.Tab) {}
            })

        register.setOnClickListener {
            val e = email.text.toString().trim()
            val p = password.text.toString()
            val c = confirm.text.toString()
            var valid = true
            if (e.isEmpty() || !e.contains("@")) { emailLayout.error = "Enter valid email"; valid = false } else emailLayout.error = null
            if (p.length < 6) { passwordLayout.error = "Min 6 chars"; valid = false } else passwordLayout.error = null
            if (c != p) { confirmLayout.error = "Passwords do not match"; valid = false } else confirmLayout.error = null
            if (!valid) return@setOnClickListener

            register.isEnabled = false
            findViewById<android.view.View>(R.id.progress).visibility = android.view.View.VISIBLE
            lifecycleScope.launch {
                try {
                    repo.register(e, p)
                    Toast.makeText(this@RegisterActivity, "Registered", Toast.LENGTH_SHORT).show()
                    startActivity(Intent(this@RegisterActivity, MainActivity::class.java).apply {
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
                    })
                } catch (t: Throwable) {
                    Timber.e(t, "register failed")
                    Toast.makeText(this@RegisterActivity, t.message ?: "Register failed", Toast.LENGTH_SHORT).show()
                } finally {
                    register.isEnabled = true
                    findViewById<android.view.View>(R.id.progress).visibility = android.view.View.GONE
                }
            }
        }
    }
}
