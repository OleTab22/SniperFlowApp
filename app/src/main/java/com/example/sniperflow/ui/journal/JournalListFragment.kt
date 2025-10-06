package com.example.sniperflow.ui.journal

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.example.sniperflow.App
import com.example.sniperflow.R
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

class JournalListFragment : Fragment() {
    private lateinit var adapter: JournalListAdapter

    override fun onCreateView(i: LayoutInflater, c: ViewGroup?, s: Bundle?) =
        i.inflate(R.layout.frag_journal_list, c, false)

    override fun onViewCreated(v: View, s: Bundle?) {
        val rv = v.findViewById<androidx.recyclerview.widget.RecyclerView>(R.id.recycler)
        adapter = JournalListAdapter()
        rv.adapter = adapter; rv.layoutManager = LinearLayoutManager(requireContext())

        val dao = (requireContext().applicationContext as App).db.journalDao()
        viewLifecycleOwner.lifecycleScope.launch {
            dao.observeAll().collectLatest { adapter.submitList(it) }
        }

        v.findViewById<com.google.android.material.floatingactionbutton.FloatingActionButton>(R.id.fab).setOnClickListener {
            NewJournalSheet().show(parentFragmentManager, "newJournal")
        }
    }
}










