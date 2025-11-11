package com.example.sniperflow.ui.journal

import android.content.Intent
import android.content.res.ColorStateList
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.example.sniperflow.R
import com.example.sniperflow.data.journal.JournalEntity
import androidx.core.content.ContextCompat
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup

class JournalListAdapter(private val onLongPress: ((JournalEntity) -> Unit)? = null) : ListAdapter<JournalEntity, JournalListAdapter.VH>(diff) {

    object diff : DiffUtil.ItemCallback<JournalEntity>() {
        override fun areItemsTheSame(o: JournalEntity, n: JournalEntity) = o.id == n.id
        override fun areContentsTheSame(o: JournalEntity, n: JournalEntity) = o == n
    }

    override fun onCreateViewHolder(p: ViewGroup, t: Int) =
        VH(LayoutInflater.from(p.context).inflate(R.layout.item_journal, p, false))

    override fun onBindViewHolder(h: VH, i: Int) = h.bind(getItem(i))

    inner class VH(v: View) : RecyclerView.ViewHolder(v) {
        private val title = v.findViewById<TextView>(R.id.tvTitle)
        private val rr = v.findViewById<Chip>(R.id.tvRr)
        private val ivShots = v.findViewById<android.widget.ImageView>(R.id.ivShots)
        private val statusChip = v.findViewById<Chip>(R.id.chipStatus)
        private val bias = v.findViewById<Chip>(R.id.chipBias)
        private val session = v.findViewById<Chip>(R.id.chipSession)
        private val queued = v.findViewById<Chip>(R.id.chipQueued)
        private val subtitle = v.findViewById<TextView>(R.id.tvSubtitle)
        private val meta = v.findViewById<TextView>(R.id.tvMeta)
        private val tagsRow = v.findViewById<ChipGroup>(R.id.tagsRow)

        fun bind(e: JournalEntity) {
            title.text = itemView.context.getString(
                R.string.journal_title_format,
                e.symbol,
                e.timeframe
            )
            rr.text = e.realizedRR?.let { itemView.context.getString(R.string.rr_realized_fmt, it) }
                ?: e.plannedRR?.let { itemView.context.getString(R.string.rr_planned_fmt, it) }
                ?: itemView.context.getString(R.string.rr_planned_empty)
            bias.text = if (e.bias.isNotBlank()) {
                e.bias
            } else {
                itemView.context.getString(R.string.journal_bias_placeholder)
            }
            session.text = if (e.session.isNotBlank()) {
                e.session
            } else {
                itemView.context.getString(R.string.journal_session_placeholder)
            }
            queued.visibility = if (e.synced) View.GONE else View.VISIBLE
            // Show camera icon when we have screenshots
            ivShots.visibility = if (e.shotUrisCsv.split(",").any { it.isNotBlank() }) View.VISIBLE else View.GONE
            // Status chip styling based on realized R:R
            val rrVal = e.realizedRR
            when {
                rrVal != null && rrVal > 0 -> {
                    statusChip.setText(R.string.journal_status_win)
                    statusChip.chipBackgroundColor = ColorStateList.valueOf(
                        ContextCompat.getColor(itemView.context, R.color.colorPositive)
                    )
                    statusChip.setTextColor(ContextCompat.getColor(itemView.context, R.color.colorOnPrimary))
                }
                rrVal != null && rrVal < 0 -> {
                    statusChip.setText(R.string.journal_status_loss)
                    statusChip.chipBackgroundColor = ColorStateList.valueOf(
                        ContextCompat.getColor(itemView.context, R.color.colorNegative)
                    )
                    statusChip.setTextColor(ContextCompat.getColor(itemView.context, android.R.color.white))
                }
                else -> {
                    statusChip.setText(if (e.synced) R.string.journal_status_synced else R.string.journal_status_open)
                    statusChip.chipBackgroundColor = ColorStateList.valueOf(
                        ContextCompat.getColor(itemView.context, R.color.colorSurfaceVariant)
                    )
                    statusChip.setTextColor(ContextCompat.getColor(itemView.context, R.color.colorOnSurface))
                }
            }
            val direction = if (e.direction.isNotBlank()) {
                e.direction
            } else {
                itemView.context.getString(R.string.journal_direction_unknown)
            }
            val entryText = e.entry?.let { String.format(java.util.Locale.getDefault(), "%.2f", it) }
            subtitle.text = if (!entryText.isNullOrBlank()) {
                itemView.context.getString(
                    R.string.journal_subtitle_with_entry,
                    e.timeframe,
                    direction,
                    entryText
                )
            } else {
                itemView.context.getString(
                    R.string.journal_subtitle_no_entry,
                    e.timeframe,
                    direction
                )
            }
            // created date/time metadata
            val fmt = java.text.SimpleDateFormat("dd MMM HH:mm", java.util.Locale.getDefault())
            meta.text = fmt.format(java.util.Date(e.createdAt))
            tagsRow.removeAllViews()
            e.tagsCsv.split(",").filter { it.isNotBlank() }.take(5).forEach { t ->
                val chip = Chip(itemView.context).apply {
                    text = t.trim()
                    textSize = 11f
                    chipBackgroundColor = ColorStateList.valueOf(
                        ContextCompat.getColor(itemView.context, R.color.colorSurfaceVariant)
                    )
                    setTextColor(ContextCompat.getColor(itemView.context, R.color.colorOnSurface))
                    isClickable = false
                    isCheckable = false
                }
                tagsRow.addView(chip)
            }
            itemView.setOnClickListener {
                val i = Intent(itemView.context, JournalDetailActivity::class.java)
                i.putExtra("id", e.id); itemView.context.startActivity(i)
            }
            itemView.setOnLongClickListener {
                onLongPress?.invoke(e); onLongPress != null
            }
        }
    }
}










