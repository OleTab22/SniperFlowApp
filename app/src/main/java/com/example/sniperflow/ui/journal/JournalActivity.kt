package com.example.sniperflow.ui.journal

import android.os.Bundle
import androidx.lifecycle.lifecycleScope
import com.example.sniperflow.R
import com.example.sniperflow.App
import com.example.sniperflow.util.LocaleAwareActivity
import kotlinx.coroutines.launch

class JournalActivity : LocaleAwareActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_journal)

        findViewById<android.view.View>(R.id.btnBack)?.setOnClickListener { finish() }
        findViewById<android.view.View>(R.id.btnAddTop)?.setOnClickListener {
            NewJournalSheet().show(supportFragmentManager, "newJournal")
        }

        // Host the existing JournalListFragment inside our container so the full UI shows
        if (savedInstanceState == null) {
            supportFragmentManager.beginTransaction()
                .replace(R.id.container, JournalListFragment())
                .commit()
        }

        // FAB moved inside fragment; no local FAB here

        // Bottom nav wiring
        val bottom = findViewById<com.google.android.material.bottomnavigation.BottomNavigationView?>(R.id.bottomNav)
        bottom?.setOnItemSelectedListener { item ->
            when (item.itemId) {
                R.id.nav_home -> {
                    startActivity(android.content.Intent(this, com.example.sniperflow.MainActivity::class.java)
                        .addFlags(android.content.Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
                R.id.nav_journal -> true
                R.id.nav_alerts -> { startActivity(android.content.Intent(this, com.example.sniperflow.notifications.NotificationsActivity::class.java)
                        .addFlags(android.content.Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
                R.id.nav_chart -> { startActivity(android.content.Intent(this, com.example.sniperflow.chart.ChartActivity::class.java)
                        .addFlags(android.content.Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
                R.id.nav_settings -> { startActivity(android.content.Intent(this, com.example.sniperflow.settings.SettingsActivity::class.java)
                        .addFlags(android.content.Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
                else -> false
            }
        }
        bottom?.selectedItemId = R.id.nav_journal
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


