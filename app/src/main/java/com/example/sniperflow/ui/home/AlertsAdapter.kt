package com.example.sniperflow.ui.home

import android.view.LayoutInflater
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.example.sniperflow.R
import com.example.sniperflow.network.AlertItem

class AlertsAdapter(
    private val onClick: (AlertItem) -> Unit
) : ListAdapter<AlertItem, AlertsAdapter.VH>(DIFF) {

    override fun onCreateViewHolder(p: ViewGroup, viewType: Int): VH {
        val v = LayoutInflater.from(p.context).inflate(R.layout.item_alert_row, p, false)
        return VH(v as ViewGroup, onClick)
    }

    override fun onBindViewHolder(h: VH, pos: Int) = h.bind(getItem(pos))

    class VH(private val root: ViewGroup, val onClick: (AlertItem) -> Unit) : RecyclerView.ViewHolder(root) {
        private val tvTitle = root.findViewById<TextView>(R.id.tvAlertTitle)
        private val tvMeta = root.findViewById<TextView>(R.id.tvAlertMeta)
        fun bind(a: AlertItem) {
            tvTitle.text = a.title ?: "Alert"
            val conf = a.conf?.let { String.format(java.util.Locale.getDefault(), "%.0f", it * 100) } ?: "-"
            val ev = a.evR?.let { String.format(java.util.Locale.getDefault(), "%.2f", it) } ?: "-"
            val age = a.ageSec ?: 0
            val sev = (a.severity ?: "").lowercase()
            val sevLabel = when (sev) {
                "actionable" -> "Actionable"
                "setup" -> "Setup"
                "info" -> "Info"
                else -> ""
            }
            tvMeta.text = listOfNotNull(
                if (sevLabel.isNotEmpty()) sevLabel else null,
                "EV ${ev}R",
                "Conf ${conf}%",
                "${age}s ago"
            ).joinToString(" • ")
            root.setOnClickListener { onClick(a) }
        }
    }

    companion object {
        private val DIFF = object : DiffUtil.ItemCallback<AlertItem>() {
            override fun areItemsTheSame(o: AlertItem, n: AlertItem) = o.id == n.id
            override fun areContentsTheSame(o: AlertItem, n: AlertItem) = o == n
        }
    }
}


