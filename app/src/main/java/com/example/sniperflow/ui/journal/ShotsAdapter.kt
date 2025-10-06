package com.example.sniperflow.ui.journal

import android.net.Uri
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import coil.load
import com.example.sniperflow.R

class ShotsAdapter(private val onRemove: ((Int) -> Unit)?)
    : ListAdapter<Uri, ShotsAdapter.VH>(diff) {

    object diff : DiffUtil.ItemCallback<Uri>() {
        override fun areItemsTheSame(o: Uri, n: Uri) = o == n
        override fun areContentsTheSame(o: Uri, n: Uri) = o == n
    }

    inner class VH(v: View) : RecyclerView.ViewHolder(v) {
        private val img = v.findViewById<ImageView>(R.id.img)
        private val remove = v.findViewById<TextView>(R.id.btnRemove)
        fun bind(uri: Uri, pos: Int) {
            img.load(uri)
            if (onRemove != null) {
                remove.visibility = View.VISIBLE
                remove.setOnClickListener { onRemove.invoke(pos) }
            } else {
                remove.visibility = View.GONE
            }
        }
    }

    override fun onCreateViewHolder(p: ViewGroup, t: Int) =
        VH(LayoutInflater.from(p.context).inflate(R.layout.item_shot_thumb, p, false))
    override fun onBindViewHolder(h: VH, i: Int) = h.bind(getItem(i), i)
}










