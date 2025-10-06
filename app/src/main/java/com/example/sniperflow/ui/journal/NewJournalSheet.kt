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
import android.widget.AutoCompleteTextView
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
            val entryStr = v.findViewById<EditText>(R.id.inEntry).text?.toString() ?: ""
            val slStr = v.findViewById<EditText>(R.id.inSl).text?.toString() ?: ""
            val tpStr = v.findViewById<EditText>(R.id.inTp).text?.toString() ?: ""
            val doStr = v.findViewById<EditText>(R.id.ctxDo).text?.toString() ?: ""
            val pdhStr = v.findViewById<EditText>(R.id.ctxPdh).text?.toString() ?: ""
            val pdlStr = v.findViewById<EditText>(R.id.ctxPdl).text?.toString() ?: ""
            val notesStr = v.findViewById<EditText>(R.id.inNotes).text?.toString() ?: ""
            val hasTags = listOf(
                R.id.tagMss, R.id.tagFvg, R.id.tagNews, R.id.tagOverride, R.id.tagTp
            ).any { id -> v.findViewById<CheckBox>(id).isChecked }
            val hasAny = listOf(entryStr, slStr, tpStr, doStr, pdhStr, pdlStr).any { it.isNotBlank() } ||
                    notesStr.isNotBlank() || picked.isNotEmpty() || hasTags
            if (!hasAny) {
                Toast.makeText(ctx, "Please add details before saving", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            val entry = entryStr.toDoubleOrNull()
            val sl = slStr.toDoubleOrNull()
            val tp = tpStr.toDoubleOrNull()
            val e = JournalEntity(
                userId = "anon",
                timeframe = "M5",
                direction = if (v.findViewById<ToggleButton>(R.id.chipBear).isChecked) "Short" else "Long",
                session = if (v.findViewById<ToggleButton>(R.id.chipLondon).isChecked) "London" else "New York",
                bias = if (v.findViewById<ToggleButton>(R.id.chipBear).isChecked) "Bear" else "Bull",
                entry = entry,
                sl = sl,
                tp = tp,
                plannedRR = calcRR(entry, sl, tp),
                doLvl = doStr.toDoubleOrNull(),
                pdh = pdhStr.toDoubleOrNull(),
                pdl = pdlStr.toDoubleOrNull(),
                notes = notesStr,
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

        // Bind steppers (+/-) using epsilon from settings (fallback 0.1)
        val epsilon = runCatching { SettingsRepository(ctx).load().first }.getOrElse { 0.1 }
        fun adjust(id: Int, delta: Double) {
            val et = v.findViewById<EditText>(id)
            val cur = et.text?.toString()?.toDoubleOrNull() ?: 0.0
            val upd = cur + delta
            et.setText("%f".format(upd))
        }
        v.findViewById<View>(R.id.btnEntryMinus)?.setOnClickListener { adjust(R.id.inEntry, -epsilon) }
        v.findViewById<View>(R.id.btnEntryPlus)?.setOnClickListener { adjust(R.id.inEntry, +epsilon) }
        v.findViewById<View>(R.id.btnSlMinus)?.setOnClickListener { adjust(R.id.inSl, -epsilon) }
        v.findViewById<View>(R.id.btnSlPlus)?.setOnClickListener { adjust(R.id.inSl, +epsilon) }
        v.findViewById<View>(R.id.btnTpMinus)?.setOnClickListener { adjust(R.id.inTp, -epsilon) }
        v.findViewById<View>(R.id.btnTpPlus)?.setOnClickListener { adjust(R.id.inTp, +epsilon) }

        // Material toggle group for direction: mirror to existing chips
        val btnLong = v.findViewById<android.view.View>(R.id.btnLong)
        val btnShort = v.findViewById<android.view.View>(R.id.btnShort)
        btnLong?.setOnClickListener {
            v.findViewById<ToggleButton>(R.id.chipBull).isChecked = true
            v.findViewById<ToggleButton>(R.id.chipBear).isChecked = false
        }
        btnShort?.setOnClickListener {
            v.findViewById<ToggleButton>(R.id.chipBull).isChecked = false
            v.findViewById<ToggleButton>(R.id.chipBear).isChecked = true
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
                time.setText(String.format("%02d:%02d", h, m))
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
}

private fun calcRR(entry: Double?, sl: Double?, tp: Double?): Double? {
    if (entry == null || sl == null || tp == null) return null
    val risk = kotlin.math.abs(entry - sl)
    val reward = kotlin.math.abs(tp - entry)
    return if (risk > 0) reward / risk else null
}
private fun <T> MutableList<T>.setAll(items: List<T>) { clear(); addAll(items) }










