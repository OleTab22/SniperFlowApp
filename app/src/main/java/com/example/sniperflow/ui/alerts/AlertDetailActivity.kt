package com.example.sniperflow.ui.alerts

import android.content.Intent
import android.os.Bundle
import android.widget.TextView
import com.example.sniperflow.R
import com.example.sniperflow.chart.ChartActivity
import com.example.sniperflow.ui.journal.NewJournalSheet
import com.example.sniperflow.util.LocaleAwareActivity
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.button.MaterialButton
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup

// Alert detail screen - shows gate receipts and driver weights
// Has "View on Chart" and "Journal Now" buttons
class AlertDetailActivity : LocaleAwareActivity() {
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_alert_detail)
        
        // Setup toolbar
        val toolbar = findViewById<MaterialToolbar>(R.id.toolbar)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        toolbar.setNavigationOnClickListener { finish() }
        
        val alertId = intent.getStringExtra("alert_id")
        val alertTitle = intent.getStringExtra("alert_title")
        val alertTs = intent.getLongExtra("alert_ts", 0L)
        val doPrice = intent.getDoubleExtra("do_price", 0.0).takeIf { it > 0 }
        val pdh = intent.getDoubleExtra("pdh", 0.0).takeIf { it > 0 }
        val pdl = intent.getDoubleExtra("pdl", 0.0).takeIf { it > 0 }
        
        findViewById<TextView>(R.id.tvAlertTitle)?.text = alertTitle ?: "Alert"
        
        // Show gate receipts
        val receiptsJson = intent.getStringExtra("gate_receipts_json")
        val receiptsGroup = findViewById<ChipGroup>(R.id.chipGroupReceipts)
        if (!receiptsJson.isNullOrBlank()) {
            try {
                val json = org.json.JSONArray(receiptsJson)
                for (i in 0 until json.length()) {
                    val r = json.getJSONObject(i)
                    val chip = Chip(this)
                    val name = r.getString("name")
                    val passed = r.getBoolean("passed")
                    val reason = r.optString("reason", "")
                    chip.text = getString(
                        R.string.alert_receipt_chip_fmt,
                        name,
                        if (passed) getString(R.string.alert_receipt_passed) else getString(R.string.alert_receipt_failed)
                    )
                    chip.chipBackgroundColor = getColorStateList(
                        if (passed) R.color.colorPositive else R.color.colorNegative
                    )
                    if (reason.isNotBlank()) {
                        chip.setOnClickListener {
                            android.app.AlertDialog.Builder(this)
                                .setTitle(name)
                                .setMessage(reason)
                                .setPositiveButton(android.R.string.ok, null)
                                .show()
                        }
                    }
                    receiptsGroup?.addView(chip)
                }
            } catch (_: Exception) {
                // Fallback to simple status
                val actionable = intent.getBooleanExtra("actionable", false)
                val chip = Chip(this)
                chip.text = if (actionable) {
                    getString(R.string.alert_receipts_all_passed)
                } else {
                    getString(R.string.alert_receipts_blocked)
                }
                chip.chipBackgroundColor = getColorStateList(
                    if (actionable) R.color.colorPositive else R.color.colorNegative
                )
                receiptsGroup?.addView(chip)
            }
        } else {
            // Fallback if no JSON provided
            val actionable = intent.getBooleanExtra("actionable", false)
            val chip = Chip(this)
            chip.text = if (actionable) {
                getString(R.string.alert_receipts_all_passed)
            } else {
                getString(R.string.alert_receipts_blocked)
            }
            chip.chipBackgroundColor = getColorStateList(
                if (actionable) R.color.colorPositive else R.color.colorNegative
            )
            receiptsGroup?.addView(chip)
        }
        
        // Show driver weights
        val driversJson = intent.getStringExtra("driver_weights_json")
        val driversGroup = findViewById<ChipGroup>(R.id.chipGroupDrivers)
        if (!driversJson.isNullOrBlank()) {
            try {
                val json = org.json.JSONArray(driversJson)
                for (i in 0 until json.length()) {
                    val d = json.getJSONObject(i)
                    val name = d.optString("name", d.optString("key", ""))
                    val weight = d.optDouble("weight", d.optDouble("contribution", 0.0))
                    if (name.isNotBlank() && weight > 0) {
                        val chip = Chip(this)
                        val pct = (weight * 100).toInt()
                        chip.text = getString(R.string.alert_driver_weight_fmt, name, pct)
                        chip.chipBackgroundColor = getColorStateList(R.color.colorPrimary)
                        driversGroup?.addView(chip)
                    }
                }
            } catch (_: Exception) {
                // Fallback to individual extras
                val dxyWeight = intent.getDoubleExtra("driver_dxy_weight", 0.0)
                val realWeight = intent.getDoubleExtra("driver_real_weight", 0.0)
                val vixWeight = intent.getDoubleExtra("driver_vix_weight", 0.0)
                listOf(
                    "DXY" to dxyWeight,
                    "Real Yields" to realWeight,
                    "VIX" to vixWeight
                ).filter { it.second > 0 }.forEach { (name, weight) ->
                    val chip = Chip(this)
                    val pct = (weight * 100).toInt()
                    chip.text = getString(R.string.alert_driver_weight_fmt, name, pct)
                    chip.chipBackgroundColor = getColorStateList(R.color.colorPrimary)
                    driversGroup?.addView(chip)
                }
            }
        } else {
            // Use individual extras as fallback
            val dxyWeight = intent.getDoubleExtra("driver_dxy_weight", 0.0)
            val realWeight = intent.getDoubleExtra("driver_real_weight", 0.0)
            val vixWeight = intent.getDoubleExtra("driver_vix_weight", 0.0)
            listOf(
                "DXY" to dxyWeight,
                "Real Yields" to realWeight,
                "VIX" to vixWeight
            ).filter { it.second > 0 }.forEach { (name, weight) ->
                val chip = Chip(this)
                val pct = (weight * 100).toInt()
                chip.text = getString(R.string.alert_driver_weight_fmt, name, pct)
                chip.chipBackgroundColor = getColorStateList(R.color.colorPrimary)
                driversGroup?.addView(chip)
            }
        }
        
        // View on Chart - deep link with timestamp
        findViewById<MaterialButton>(R.id.btnViewChart)?.setOnClickListener {
            val intent = Intent(this, ChartActivity::class.java)
            intent.putExtra("timestamp", alertTs)
            intent.putExtra("do_price", doPrice)
            intent.putExtra("pdh", pdh)
            intent.putExtra("pdl", pdl)
            startActivity(intent)
        }
        
        // Journal Now - opens journal sheet with alert context pre-filled
        findViewById<MaterialButton>(R.id.btnJournalNow)?.setOnClickListener {
            val sheet = NewJournalSheet().apply {
                arguments = Bundle().apply {
                    putString("alert_id", alertId)
                    putString("alert_title", alertTitle)
                    putDouble("do_price", doPrice ?: 0.0)
                    putDouble("pdh", pdh ?: 0.0)
                    putDouble("pdl", pdl ?: 0.0)
                }
            }
            sheet.show(supportFragmentManager, "journal_sheet")
        }
    }
}

// Using Intent extras instead of Parcelable for simplicity

