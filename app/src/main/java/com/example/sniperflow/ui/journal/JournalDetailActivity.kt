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

class JournalDetailActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.act_journal_detail)

        val id = intent.getIntExtra("id", -1)
        lifecycleScope.launch {
            val e = (application as App).db.journalDao().get(id) ?: return@launch
            bind(e)
        }
    }

    private fun bind(e: JournalEntity) {
        findViewById<android.widget.TextView>(R.id.title).text =
            if (e.realizedRR != null && e.realizedRR > 0) "Take Profit" else "Journal"

        findViewById<android.widget.TextView>(R.id.txtRRPlanned).text =
            e.plannedRR?.let { "Planned R:R ${"%.2f".format(it)}" } ?: "Planned R:R —"
        findViewById<android.widget.TextView>(R.id.txtRRReal).text =
            e.realizedRR?.let { "Realized R:R ${"%.2f".format(it)}" } ?: "Realized R:R —"

        findViewById<android.widget.TextView>(R.id.txtContext).text =
            "${e.bias} • ${e.session} • DO ${fmt(e.doLvl)} • PDH ${fmt(e.pdh)} • PDL ${fmt(e.pdl)}"

        findViewById<android.widget.TextView>(R.id.txtSetup).text =
            "${e.timeframe} ${e.direction} @ ${fmt(e.entry)}"

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

    private fun fmt(d: Double?) = d?.let { String.format("%,.2f", it) } ?: "—"
}

private class ShotsGallery(private val onClick: (Uri)->Unit)
    : androidx.recyclerview.widget.RecyclerView.Adapter<ShotsAdapter.VH>() {

    private val data = mutableListOf<Uri>()
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










