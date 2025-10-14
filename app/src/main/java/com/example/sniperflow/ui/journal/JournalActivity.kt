package com.example.sniperflow.ui.journal

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
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

    override fun onResume() {
        super.onResume()
        // If a BottomNavigationView is present in this layout in future, select nav_journal.
        val bottomNavId = R.id.bottomNav
        val bottomNavView = findViewById<com.google.android.material.bottomnavigation.BottomNavigationView?>(bottomNavId)
        bottomNavView?.selectedItemId = R.id.nav_journal
    }

    override fun onCreateOptionsMenu(menu: android.view.Menu): Boolean {
        menuInflater.inflate(R.menu.menu_journal, menu)
        return true
    }

    override fun onOptionsItemSelected(item: android.view.MenuItem): Boolean {
        if (item.itemId == R.id.action_export_csv) {
            lifecycleScope.launch {
                val dao = (application as App).db.journalDao()
                val rows = dao.listAll()
                val file = CsvExporter.export(this@JournalActivity, rows)
                android.widget.Toast.makeText(this@JournalActivity, "Exported to ${file.absolutePath}", android.widget.Toast.LENGTH_LONG).show()
            }
            return true
        }
        return super.onOptionsItemSelected(item)
    }
}


