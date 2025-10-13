package com.example.sniperflow.ui.journal

import android.content.Context
import com.example.sniperflow.data.journal.JournalEntity
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

object CsvExporter {
    fun export(ctx: Context, rows: List<JournalEntity>): File {
        val dir = ctx.getExternalFilesDir(android.os.Environment.DIRECTORY_DOCUMENTS) ?: ctx.filesDir
        val name = "journal-" + SimpleDateFormat("yyyyMMdd-HHmm", Locale.getDefault()).format(Date()) + ".csv"
        val f = File(dir, name)
        f.bufferedWriter().use { w ->
            w.appendLine("id,createdAt,userId,symbol,timeframe,direction,session,bias,entry,sl,tp,plannedRR,realizedRR,doLvl,pdh,pdl,durationSec,mae,mfe,heat,notes,tags,shots,synced")
            rows.forEach { e ->
                w.appendLine(listOf(
                    e.id,
                    e.createdAt,
                    e.userId,
                    e.symbol,
                    e.timeframe,
                    e.direction,
                    e.session,
                    e.bias,
                    e.entry,
                    e.sl,
                    e.tp,
                    e.plannedRR,
                    e.realizedRR,
                    e.doLvl,
                    e.pdh,
                    e.pdl,
                    e.durationSec,
                    e.mae,
                    e.mfe,
                    e.heat,
                    e.notes.replace('\n', ' ').replace(',', ';'),
                    e.tagsCsv,
                    e.shotUrisCsv,
                    e.synced
                ).joinToString(","))
            }
        }
        return f
    }
}


