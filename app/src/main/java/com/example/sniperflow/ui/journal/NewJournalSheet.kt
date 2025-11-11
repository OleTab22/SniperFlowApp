package com.example.sniperflow.ui.journal

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import android.widget.AutoCompleteTextView
import com.google.android.material.chip.Chip
import android.app.TimePickerDialog
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.fragment.app.DialogFragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.example.sniperflow.App
import com.example.sniperflow.R
import com.example.sniperflow.data.journal.JournalEntity
import com.example.sniperflow.data.journal.JournalSyncWorker
import com.example.sniperflow.settings.SettingsRepository
import kotlinx.coroutines.launch
import java.util.Locale

class NewJournalSheet : DialogFragment() {

    private val picked = mutableListOf<Uri>()
    private lateinit var shotsAdapter: ShotsAdapter

    private val photoPicker =
        registerForActivityResult(ActivityResultContracts.PickMultipleVisualMedia(5)) { uris ->
            if (uris.isNotEmpty()) { picked.setAll(uris); shotsAdapter.submitList(picked.toList()) }
        }
    private val openDocs =
        registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
            if (uris.isNotEmpty()) {
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

        // If launched for edit, prefill
        val editId = arguments?.getInt(ARG_EDIT_ID)?.takeIf { it > 0 }
        if (editId != null) {
            viewLifecycleOwner.lifecycleScope.launch {
                val dao = (ctx.applicationContext as App).db.journalDao()
                val cur = dao.get(editId) ?: return@launch
                prefillFromEntity(v, cur)
                v.findViewById<android.widget.TextView?>(R.id.title)?.text = getString(R.string.edit_journal)
                v.findViewById<Button>(R.id.btnSave).text = getString(android.R.string.ok)
            }
        }

        v.findViewById<Button>(R.id.btnSave).setOnClickListener {
            val built = buildEntityFromInputs(v)
            if (built == null) {
                Toast.makeText(ctx, getString(R.string.add_details_before_saving), Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            viewLifecycleOwner.lifecycleScope.launch {
                val dao = (ctx.applicationContext as App).db.journalDao()
                if (editId == null) {
                    dao.insert(built)
                    Toast.makeText(ctx, getString(R.string.journal_saved), Toast.LENGTH_SHORT).show()
                } else {
                    val existing = dao.get(editId)
                    if (existing != null) {
                        val updated = built.copy(id = existing.id, createdAt = existing.createdAt, synced = false)
                        dao.update(updated)
                        Toast.makeText(ctx, getString(R.string.journal_updated), Toast.LENGTH_SHORT).show()
                    }
                }
                JournalSyncWorker.kickOnce(ctx)
                dismiss()
            }
        }

        // Bind steppers (+/-) using epsilon from settings (fallback 0.1)
        val epsilon = runCatching { SettingsRepository(ctx).load().first }.getOrElse { 0.1 }
        fun adjust(id: Int, delta: Double) {
            val et = v.findViewById<EditText>(id)
            val cur = et.text?.toString()?.toDoubleOrNull() ?: 0.0
            val upd = cur + delta
            et.setText(String.format(Locale.US, "%f", upd))
        }
        v.findViewById<View>(R.id.btnEntryMinus)?.setOnClickListener { adjust(R.id.inEntry, -epsilon) }
        v.findViewById<View>(R.id.btnEntryPlus)?.setOnClickListener { adjust(R.id.inEntry, +epsilon) }
        v.findViewById<View>(R.id.btnSlMinus)?.setOnClickListener { adjust(R.id.inSl, -epsilon) }
        v.findViewById<View>(R.id.btnSlPlus)?.setOnClickListener { adjust(R.id.inSl, +epsilon) }
        v.findViewById<View>(R.id.btnTpMinus)?.setOnClickListener { adjust(R.id.inTp, -epsilon) }
        v.findViewById<View>(R.id.btnTpPlus)?.setOnClickListener { adjust(R.id.inTp, +epsilon) }

        // Material toggle group for direction: mirror to existing chips
        val btnLong = v.findViewById<View>(R.id.btnLong)
        val btnShort = v.findViewById<View>(R.id.btnShort)
        val chipBull = v.findViewById<Chip>(R.id.chipBull)
        val chipBear = v.findViewById<Chip>(R.id.chipBear)
        btnLong?.setOnClickListener {
            chipBull?.isChecked = true
            chipBear?.isChecked = false
        }
        btnShort?.setOnClickListener {
            chipBull?.isChecked = false
            chipBear?.isChecked = true
        }

        // Timeframe dropdown
        val tf = v.findViewById<AutoCompleteTextView>(R.id.inTimeframe)
        tf?.setAdapter(android.widget.ArrayAdapter(ctx, android.R.layout.simple_list_item_1,
            listOf("M1","M5","M15","M30","H1","H4","D1")))
        tf?.setOnItemClickListener { _, _, _, _ -> }

        // Time picker
        val time = v.findViewById<com.google.android.material.textfield.TextInputEditText>(R.id.inTime)
        time?.setOnClickListener {
            val cal = java.util.Calendar.getInstance()
            val dlg = TimePickerDialog(ctx, { _, h, m ->
                time.setText(String.format(Locale.getDefault(), "%02d:%02d", h, m))
            }, cal.get(java.util.Calendar.HOUR_OF_DAY), cal.get(java.util.Calendar.MINUTE), true)
            dlg.show()
        }
    }

    override fun onStart() {
        super.onStart()
        // Make the dialog use full screen width for better usability
        val w = dialog?.window ?: return
        w.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
    }

    private fun prefillFromEntity(v: View, e: JournalEntity) {
        v.findViewById<Chip>(R.id.chipBull)?.isChecked = e.bias.equals("Bull", true)
        v.findViewById<Chip>(R.id.chipBear)?.isChecked = e.bias.equals("Bear", true)
        v.findViewById<Chip>(R.id.chipLondon)?.isChecked = e.session.equals("London", true)
        v.findViewById<Chip>(R.id.chipNY)?.isChecked = e.session.equals("New York", true)
        v.findViewById<EditText>(R.id.inEntry).setText(e.entry?.toString() ?: "")
        v.findViewById<EditText>(R.id.inSl).setText(e.sl?.toString() ?: "")
        v.findViewById<EditText>(R.id.inTp).setText(e.tp?.toString() ?: "")
        v.findViewById<EditText>(R.id.ctxDo).setText(e.doLvl?.toString() ?: "")
        v.findViewById<EditText>(R.id.ctxPdh).setText(e.pdh?.toString() ?: "")
        v.findViewById<EditText>(R.id.ctxPdl).setText(e.pdl?.toString() ?: "")
        v.findViewById<EditText>(R.id.inNotes).setText(e.notes)
        v.findViewById<AutoCompleteTextView?>(R.id.inTimeframe)?.setText(e.timeframe, false)
        // Tags
        v.findViewById<Chip>(R.id.tagMss)?.isChecked = e.tagsCsv.contains("mss")
        v.findViewById<Chip>(R.id.tagFvg)?.isChecked = e.tagsCsv.contains("fvg")
        v.findViewById<Chip>(R.id.tagNews)?.isChecked = e.tagsCsv.contains("news")
        v.findViewById<Chip>(R.id.tagOverride)?.isChecked = e.tagsCsv.contains("override")
        v.findViewById<Chip>(R.id.tagTp)?.isChecked = e.tagsCsv.contains("TP")
        // Shots
        val uris = e.shotUrisCsv.split(",").filter { it.isNotBlank() }.map(Uri::parse)
        picked.setAll(uris)
        shotsAdapter.submitList(picked.toList())
    }

    private fun buildEntityFromInputs(v: View): JournalEntity? {
        val entryStr = v.findViewById<EditText>(R.id.inEntry).text?.toString() ?: ""
        val slStr = v.findViewById<EditText>(R.id.inSl).text?.toString() ?: ""
        val tpStr = v.findViewById<EditText>(R.id.inTp).text?.toString() ?: ""
        val doStr = v.findViewById<EditText>(R.id.ctxDo).text?.toString() ?: ""
        val pdhStr = v.findViewById<EditText>(R.id.ctxPdh).text?.toString() ?: ""
        val pdlStr = v.findViewById<EditText>(R.id.ctxPdl).text?.toString() ?: ""
        val notesStr = v.findViewById<EditText>(R.id.inNotes).text?.toString() ?: ""
        val hasTags = listOf(
            R.id.tagMss, R.id.tagFvg, R.id.tagNews, R.id.tagOverride, R.id.tagTp
        ).any { id -> v.findViewById<Chip>(id)?.isChecked == true }
        val hasAny = listOf(entryStr, slStr, tpStr, doStr, pdhStr, pdlStr).any { it.isNotBlank() } ||
                notesStr.isNotBlank() || picked.isNotEmpty() || hasTags
        if (!hasAny) return null

        val entry = entryStr.toDoubleOrNull()
        val sl = slStr.toDoubleOrNull()
        val tp = tpStr.toDoubleOrNull()
        val timeframeSel = v.findViewById<AutoCompleteTextView?>(R.id.inTimeframe)?.text?.toString()?.ifBlank { null }
        val timeframe = timeframeSel ?: "M5"
        val chipBear = v.findViewById<Chip>(R.id.chipBear)
        val chipLondon = v.findViewById<Chip>(R.id.chipLondon)
        return JournalEntity(
            userId = "anon",
            timeframe = timeframe,
            direction = if (chipBear?.isChecked == true) "Short" else "Long",
            session = if (chipLondon?.isChecked == true) "London" else "New York",
            bias = if (chipBear?.isChecked == true) "Bear" else "Bull",
            entry = entry,
            sl = sl,
            tp = tp,
            plannedRR = calcRR(entry, sl, tp),
            doLvl = doStr.toDoubleOrNull(),
            pdh = pdhStr.toDoubleOrNull(),
            pdl = pdlStr.toDoubleOrNull(),
            notes = notesStr,
            tagsCsv = buildList {
                if (v.findViewById<Chip>(R.id.tagMss)?.isChecked == true) add("mss")
                if (v.findViewById<Chip>(R.id.tagFvg)?.isChecked == true) add("fvg")
                if (v.findViewById<Chip>(R.id.tagNews)?.isChecked == true) add("news")
                if (v.findViewById<Chip>(R.id.tagOverride)?.isChecked == true) add("override")
                if (v.findViewById<Chip>(R.id.tagTp)?.isChecked == true) add("TP")
            }.joinToString(","),
            shotUrisCsv = picked.joinToString(",") { it.toString() },
            synced = false
        )
    }

    companion object {
        private const val ARG_EDIT_ID = "edit_id"
        fun forEdit(id: Int): NewJournalSheet = NewJournalSheet().apply {
            arguments = Bundle().apply { putInt(ARG_EDIT_ID, id) }
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










