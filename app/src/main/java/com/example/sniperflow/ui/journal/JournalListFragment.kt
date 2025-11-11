package com.example.sniperflow.ui.journal

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.example.sniperflow.App
import android.view.ViewGroup
import com.example.sniperflow.R
import com.example.sniperflow.ui.UiState
import com.example.sniperflow.ui.bindState
import com.google.android.material.floatingactionbutton.ExtendedFloatingActionButton
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

class JournalListFragment : Fragment() {
    private lateinit var adapter: JournalListAdapter

    override fun onCreateView(i: LayoutInflater, c: ViewGroup?, s: Bundle?) =
        i.inflate(R.layout.frag_journal_list, c, false)

    override fun onViewCreated(v: View, s: Bundle?) {
        val rv = v.findViewById<RecyclerView>(R.id.recycler)
        adapter = JournalListAdapter { e ->
            // Long-press to edit
            NewJournalSheet.forEdit(e.id).show(parentFragmentManager, "editJournal")
        }
        rv.adapter = adapter; rv.layoutManager = LinearLayoutManager(requireContext())

        val dao = (requireContext().applicationContext as App).db.journalDao()
        val stateRoot = v.findViewById<ViewGroup>(R.id.stateRoot)
        stateRoot?.bindState(UiState(loading = true))
        viewLifecycleOwner.lifecycleScope.launch {
            dao.observeAll().collectLatest {
                adapter.submitList(it)
                stateRoot?.bindState(UiState(loading = false, empty = it.isEmpty()))
            }
        }

        v.findViewById<ExtendedFloatingActionButton>(R.id.fab)?.setOnClickListener {
            NewJournalSheet().show(parentFragmentManager, "newJournal")
        }

        // Swipe to delete
        val swipe = object : androidx.recyclerview.widget.ItemTouchHelper.SimpleCallback(0,
            androidx.recyclerview.widget.ItemTouchHelper.LEFT or androidx.recyclerview.widget.ItemTouchHelper.RIGHT) {
            override fun onMove(
                recyclerView: RecyclerView,
                viewHolder: RecyclerView.ViewHolder,
                target: RecyclerView.ViewHolder
            ): Boolean = false

            override fun onSwiped(viewHolder: RecyclerView.ViewHolder, direction: Int) {
                val pos = viewHolder.bindingAdapterPosition
                val item = adapter.currentList.getOrNull(pos) ?: return
                viewLifecycleOwner.lifecycleScope.launch {
                    val dao = (requireContext().applicationContext as App).db.journalDao()
                    dao.deleteById(item.id)
                    // best-effort server delete
                    runCatching {
                        val api = com.example.sniperflow.network.RetrofitModule.api(com.example.sniperflow.BuildConfig.BASE_URL)
                        api.deleteJournal(item.id)
                    }
                }
            }
        }
        androidx.recyclerview.widget.ItemTouchHelper(swipe).attachToRecyclerView(rv)
    }
}










