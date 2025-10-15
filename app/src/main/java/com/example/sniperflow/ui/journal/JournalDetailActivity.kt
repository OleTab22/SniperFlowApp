package com.example.sniperflow.ui.journal

import android.app.Dialog
import android.net.Uri
import android.os.Bundle
import android.widget.ImageView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import coil.load
import com.example.sniperflow.App
import com.example.sniperflow.R
import com.example.sniperflow.data.journal.JournalEntity
import kotlinx.coroutines.launch
import java.util.Locale
import com.google.android.material.bottomnavigation.BottomNavigationView
import android.content.Intent

class JournalDetailActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.act_journal_detail)

        val id = intent.getIntExtra("id", -1)
        lifecycleScope.launch {
            val e = (application as App).db.journalDao().get(id) ?: return@launch
            bind(e)
        }

        findViewById<android.view.View?>(R.id.fabEdit)?.setOnClickListener {
            NewJournalSheet.forEdit(id).show(supportFragmentManager, "editJournal")
        }
        findViewById<android.view.View?>(R.id.fabDelete)?.setOnClickListener {
            android.app.AlertDialog.Builder(this)
                .setTitle("Delete Journal")
                .setMessage("Are you sure?")
                .setPositiveButton("Delete") { d, _ ->
                    lifecycleScope.launch {
                        val dao = (application as App).db.journalDao()
                        dao.deleteById(id)
                        // Best-effort remote delete if available
                        runCatching {
                            val api = com.example.sniperflow.network.RetrofitModule.api(com.example.sniperflow.BuildConfig.BASE_URL)
                            api.deleteJournal(id)
                        }
                        finish()
                    }
                    d.dismiss()
                }
                .setNegativeButton("Cancel") { d, _ -> d.dismiss() }
                .show()
        }

        // Bottom navigation wiring
        findViewById<BottomNavigationView>(R.id.bottomNav)?.apply {
            setOnItemSelectedListener { item ->
                when (item.itemId) {
                    R.id.nav_home -> { startActivity(Intent(this@JournalDetailActivity, com.example.sniperflow.MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
                    R.id.nav_journal -> { startActivity(Intent(this@JournalDetailActivity, JournalActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
                    R.id.nav_alerts -> { startActivity(Intent(this@JournalDetailActivity, com.example.sniperflow.notifications.NotificationsActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
                    R.id.nav_chart -> { startActivity(Intent(this@JournalDetailActivity, com.example.sniperflow.chart.ChartActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
                    R.id.nav_settings -> { startActivity(Intent(this@JournalDetailActivity, com.example.sniperflow.settings.SettingsActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); true }
                    else -> false
                }
            }
            setOnItemReselectedListener { }
            selectedItemId = R.id.nav_journal
        }
    }

    override fun onCreateOptionsMenu(menu: android.view.Menu): Boolean {
        menu.add(0, 1001, 0, "Edit")
        menu.add(0, 1002, 1, "Delete")
        return true
    }

    override fun onOptionsItemSelected(item: android.view.MenuItem): Boolean {
        val id = intent.getIntExtra("id", -1)
        when (item.itemId) {
            1001 -> { // Edit: open full sheet
                NewJournalSheet.forEdit(id).show(supportFragmentManager, "editJournal")
                return true
            }
            1002 -> { // Delete
                android.app.AlertDialog.Builder(this)
                    .setTitle("Delete Journal")
                    .setMessage("Are you sure?")
                    .setPositiveButton("Delete") { d, _ ->
                        lifecycleScope.launch {
                            val dao = (application as App).db.journalDao()
                            dao.deleteById(id)
                            finish()
                        }
                        d.dismiss()
                    }
                    .setNegativeButton("Cancel") { d, _ -> d.dismiss() }
                    .show()
                return true
            }
        }
        return super.onOptionsItemSelected(item)
    }

    override fun onResume() {
        super.onResume()
        val id = intent.getIntExtra("id", -1)
        lifecycleScope.launch {
            val e = (application as App).db.journalDao().get(id) ?: return@launch
            bind(e)
        }
        findViewById<BottomNavigationView?>(R.id.bottomNav)?.selectedItemId = R.id.nav_journal
    }

    private fun bind(e: JournalEntity) {
        findViewById<android.widget.TextView>(R.id.title).text =
            if (e.realizedRR != null && e.realizedRR > 0) getString(R.string.journal_take_profit_title) else getString(R.string.journal_title)

        findViewById<android.widget.TextView>(R.id.txtRRPlanned).text =
            e.plannedRR?.let { getString(R.string.rr_planned_fmt, it) } ?: getString(R.string.rr_planned_empty)
        findViewById<android.widget.TextView>(R.id.txtRRReal).text =
            e.realizedRR?.let { getString(R.string.rr_realized_fmt, it) } ?: getString(R.string.rr_realized_empty)

        findViewById<android.widget.TextView>(R.id.txtContext).text =
            getString(
                R.string.context_detail_fmt,
                e.bias,
                e.session,
                fmt(e.doLvl),
                fmt(e.pdh),
                fmt(e.pdl)
            )

        findViewById<android.widget.TextView>(R.id.txtSetup).text =
            getString(R.string.setup_detail_fmt, e.timeframe, e.direction, fmt(e.entry))

        findViewById<android.widget.TextView>(R.id.txtNotes).text = e.notes.ifBlank { "—" }

        val uris = e.shotUrisCsv.split(",").filter { it.isNotBlank() }.map(Uri::parse)
        val rv = findViewById<androidx.recyclerview.widget.RecyclerView>(R.id.rvShotsDetail)
        val adapter = ShotsGallery { showFullscreen(it) }
        rv.adapter = adapter; rv.layoutManager = LinearLayoutManager(this, LinearLayoutManager.HORIZONTAL, false)
        adapter.submit(uris)
    }

    private fun showFullscreen(uri: Uri) {
        val d = Dialog(this, android.R.style.Theme_Black_NoTitleBar_Fullscreen)
        val iv = ImageView(this).apply { adjustViewBounds = true; scaleType = ImageView.ScaleType.FIT_CENTER }
        d.setContentView(iv); iv.load(uri); iv.setOnClickListener { d.dismiss() }; d.show()
    }

    private fun fmt(d: Double?) = d?.let { String.format(Locale.getDefault(), "%,.2f", it) } ?: "—"
}

private class ShotsGallery(private val onClick: (Uri)->Unit)
    : androidx.recyclerview.widget.RecyclerView.Adapter<ShotsAdapter.VH>() {

    private val data = mutableListOf<Uri>()
    @Suppress("NotifyDataSetChanged")
    fun submit(list: List<Uri>) { data.setAll(list); notifyDataSetChanged() }
    override fun getItemCount() = data.size
    override fun onCreateViewHolder(p: android.view.ViewGroup, t: Int) =
        ShotsAdapter(null as ((Int) -> Unit)?).VH(
            android.view.LayoutInflater.from(p.context).inflate(R.layout.item_shot_thumb, p, false)
        ).also { it.itemView.findViewById<android.widget.TextView>(R.id.btnRemove).visibility = android.view.View.GONE }
    override fun onBindViewHolder(h: ShotsAdapter.VH, i: Int) {
        val uri = data[i]
        h.itemView.findViewById<ImageView>(R.id.img).load(uri)
        h.itemView.setOnClickListener { onClick(uri) }
    }
}
private fun <T> MutableList<T>.setAll(list: List<T>) { clear(); addAll(list) }










