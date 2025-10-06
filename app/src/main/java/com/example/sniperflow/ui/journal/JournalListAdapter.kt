package com.example.sniperflow.ui.journal

import android.content.Intent
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.example.sniperflow.R
import com.example.sniperflow.data.journal.JournalEntity

class JournalListAdapter : ListAdapter<JournalEntity, JournalListAdapter.VH>(diff) {

    object diff : DiffUtil.ItemCallback<JournalEntity>() {
        override fun areItemsTheSame(o: JournalEntity, n: JournalEntity) = o.id == n.id
        override fun areContentsTheSame(o: JournalEntity, n: JournalEntity) = o == n
    }

    override fun onCreateViewHolder(p: ViewGroup, t: Int) =
        VH(LayoutInflater.from(p.context).inflate(R.layout.item_journal, p, false))

    override fun onBindViewHolder(h: VH, i: Int) = h.bind(getItem(i))

    inner class VH(v: View) : RecyclerView.ViewHolder(v) {
        private val title = v.findViewById<TextView>(R.id.tvTitle)
        private val rr = v.findViewById<TextView>(R.id.tvRr)
        private val ivShots = v.findViewById<android.widget.ImageView>(R.id.ivShots)
        private val ivStatus = v.findViewById<android.widget.ImageView>(R.id.ivStatus)
        private val bias = v.findViewById<TextView>(R.id.chipBias)
        private val session = v.findViewById<TextView>(R.id.chipSession)
        private val queued = v.findViewById<TextView>(R.id.chipQueued)
        private val subtitle = v.findViewById<TextView>(R.id.tvSubtitle)
        private val tagsRow = v.findViewById<LinearLayout>(R.id.tagsRow)

        fun bind(e: JournalEntity) {
            title.text = "${e.symbol} · ${e.timeframe}"
            rr.text = e.realizedRR?.let { "R:R ${"%.2f".format(it)}" }
                ?: e.plannedRR?.let { "R:R ${"%.2f".format(it)}" } ?: "R:R —"
            bias.text = e.bias; session.text = e.session
            queued.visibility = if (e.synced) View.GONE else View.VISIBLE
            // Show camera icon when we have screenshots
            ivShots.visibility = if (e.shotUrisCsv.split(",").any { it.isNotBlank() }) View.VISIBLE else View.GONE
            // Status dot: green if realizedRR>0, red if realizedRR<0, gray otherwise
            val rrVal = e.realizedRR
            ivStatus.setImageResource(
                when {
                    rrVal != null && rrVal > 0 -> android.R.drawable.presence_online
                    rrVal != null && rrVal < 0 -> android.R.drawable.presence_busy
                    else -> android.R.drawable.presence_invisible
                }
            )
            subtitle.text = buildString {
                append("${e.timeframe} ${e.direction}")
                e.entry?.let { append(" @ ${"%.2f".format(it)}") }
            }
            tagsRow.removeAllViews()
            e.tagsCsv.split(",").filter { it.isNotBlank() }.take(5).forEach { t ->
                val chip = TextView(itemView.context).apply {
                    text = t; setPadding(10,6,10,6)
                    setBackgroundColor(0xFFE0E0E0.toInt())
                }
                tagsRow.addView(chip)
            }
            itemView.setOnClickListener {
                val i = Intent(itemView.context, JournalDetailActivity::class.java)
                i.putExtra("id", e.id); itemView.context.startActivity(i)
            }
        }
    }
}










