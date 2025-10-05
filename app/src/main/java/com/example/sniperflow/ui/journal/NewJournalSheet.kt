package com.example.sniperflow.ui.journal

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.ToggleButton
import android.widget.Toast
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.fragment.app.DialogFragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.example.sniperflow.App
import com.example.sniperflow.R
import com.example.sniperflow.data.journal.JournalEntity
import com.example.sniperflow.data.journal.JournalSyncWorker
import kotlinx.coroutines.launch

class NewJournalSheet : DialogFragment() {

    private val picked = mutableListOf<Uri>()
    private lateinit var shotsAdapter: ShotsAdapter

    private val photoPicker =
        registerForActivityResult(ActivityResultContracts.PickMultipleVisualMedia(5)) { uris ->
            if (!uris.isNullOrEmpty()) { picked.setAll(uris); shotsAdapter.submitList(picked.toList()) }
        }
    private val openDocs =
        registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
            if (!uris.isNullOrEmpty()) {
                val flags = Intent.FLAG_GRANT_READ_URI_PERMISSION
                uris.forEach { requireContext().contentResolver.takePersistableUriPermission(it, flags) }
                picked.setAll(uris); shotsAdapter.submitList(picked.toList())
            }
        }

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, saved: Bundle?) =
        inflater.inflate(R.layout.sheet_new_journal, container, false)

    override fun onViewCreated(v: View, s: Bundle?) {
        val ctx = requireContext()
        val rv = v.findViewById<androidx.recyclerview.widget.RecyclerView>(R.id.rvShots)
        shotsAdapter = ShotsAdapter { idx -> picked.removeAt(idx); shotsAdapter.submitList(picked.toList()) }
        rv.adapter = shotsAdapter; rv.layoutManager = LinearLayoutManager(ctx, LinearLayoutManager.HORIZONTAL, false)

        v.findViewById<View>(R.id.btnAddShots).setOnClickListener {
            if (Build.VERSION.SDK_INT >= 33) {
                photoPicker.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
            } else openDocs.launch(arrayOf("image/*"))
        }

        v.findViewById<Button>(R.id.btnSave).setOnClickListener {
            val e = JournalEntity(
                userId = "anon",
                timeframe = "M5",
                direction = if (v.findViewById<ToggleButton>(R.id.chipBear).isChecked) "Short" else "Long",
                session = if (v.findViewById<ToggleButton>(R.id.chipLondon).isChecked) "London" else "New York",
                bias = if (v.findViewById<ToggleButton>(R.id.chipBear).isChecked) "Bear" else "Bull",
                entry = v.findViewById<EditText>(R.id.inEntry).text.toString().toDoubleOrNull(),
                sl = v.findViewById<EditText>(R.id.inSl).text.toString().toDoubleOrNull(),
                tp = v.findViewById<EditText>(R.id.inTp).text.toString().toDoubleOrNull(),
                plannedRR = calcRR(
                    v.findViewById<EditText>(R.id.inEntry).text.toString().toDoubleOrNull(),
                    v.findViewById<EditText>(R.id.inSl).text.toString().toDoubleOrNull(),
                    v.findViewById<EditText>(R.id.inTp).text.toString().toDoubleOrNull()
                ),
                doLvl = v.findViewById<EditText>(R.id.ctxDo).text.toString().toDoubleOrNull(),
                pdh = v.findViewById<EditText>(R.id.ctxPdh).text.toString().toDoubleOrNull(),
                pdl = v.findViewById<EditText>(R.id.ctxPdl).text.toString().toDoubleOrNull(),
                notes = v.findViewById<EditText>(R.id.inNotes).text.toString(),
                tagsCsv = buildList {
                    if (v.findViewById<CheckBox>(R.id.tagMss).isChecked) add("mss")
                    if (v.findViewById<CheckBox>(R.id.tagFvg).isChecked) add("fvg")
                    if (v.findViewById<CheckBox>(R.id.tagNews).isChecked) add("news")
                    if (v.findViewById<CheckBox>(R.id.tagOverride).isChecked) add("override")
                    if (v.findViewById<CheckBox>(R.id.tagTp).isChecked) add("TP")
                }.joinToString(","),
                shotUrisCsv = picked.joinToString(",") { it.toString() },
                synced = false
            )

            viewLifecycleOwner.lifecycleScope.launch {
                val dao = (ctx.applicationContext as App).db.journalDao()
                dao.insert(e)
                JournalSyncWorker.kickOnce(ctx)
                Toast.makeText(ctx, "Journal saved", Toast.LENGTH_SHORT).show()
                dismiss()
            }
        }
    }
}

private fun calcRR(entry: Double?, sl: Double?, tp: Double?): Double? {
    if (entry == null || sl == null || tp == null) return null
    val risk = kotlin.math.abs(entry - sl)
    val reward = kotlin.math.abs(tp - entry)
    return if (risk > 0) reward / risk else null
}
private fun <T> MutableList<T>.setAll(items: List<T>) { clear(); addAll(items) }


