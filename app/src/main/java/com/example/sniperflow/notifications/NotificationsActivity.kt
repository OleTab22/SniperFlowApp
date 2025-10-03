package com.example.sniperflow.notifications

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.example.sniperflow.R

class NotificationsActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_notifications)
        findViewById<androidx.appcompat.widget.Toolbar?>(R.id.toolbar)?.setNavigationOnClickListener { finish() }
    }
}


