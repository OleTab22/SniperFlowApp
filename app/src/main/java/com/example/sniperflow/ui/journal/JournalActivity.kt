package com.example.sniperflow.ui.journal

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.lifecycle.lifecycleScope
import com.example.sniperflow.R
import com.example.sniperflow.App
import kotlinx.coroutines.launch

class JournalActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_journal)

        findViewById<android.view.View>(R.id.btnBack)?.setOnClickListener { finish() }

        // Host the existing JournalListFragment inside our container so the full UI shows
        if (savedInstanceState == null) {
            supportFragmentManager.beginTransaction()
                .replace(R.id.container, JournalListFragment())
                .commit()
        }

        findViewById<android.view.View>(R.id.fabAddJournal)?.setOnClickListener {
            NewJournalSheet().show(supportFragmentManager, "newJournal")
        }
    }
}


