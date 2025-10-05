package com.example.sniperflow.data.journal

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "journal")
data class JournalEntity(
    @PrimaryKey(autoGenerate = true) val id: Int = 0,
    val userId: String,
    val symbol: String = "XAUUSD",
    val timeframe: String,
    val direction: String,
    val session: String,
    val bias: String,
    val entry: Double?,
    val sl: Double?,
    val tp: Double?,
    val plannedRR: Double?,
    val realizedRR: Double? = null,
    val doLvl: Double?,
    val pdh: Double?,
    val pdl: Double?,
    val durationSec: Int? = null,
    val mae: Double? = null,
    val mfe: Double? = null,
    val heat: String? = null,
    val notes: String = "",
    val tagsCsv: String = "",
    val shotUrisCsv: String = "",
    val synced: Boolean = false,
    val createdAt: Long = System.currentTimeMillis()
)


