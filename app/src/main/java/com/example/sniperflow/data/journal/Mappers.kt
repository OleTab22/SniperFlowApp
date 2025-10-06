package com.example.sniperflow.data.journal

import com.example.sniperflow.network.JournalReq

fun JournalEntity.toReq(): JournalReq = JournalReq(
    user_id = userId,
    alert_id = "MANUAL",
    notes = notes,
    direction = direction,
    timeframe = timeframe,
    entry = entry,
    sl = sl,
    tp = tp,
    planned_rr = plannedRR,
    tags = tagsCsv.split(',').filter { it.isNotBlank() },
    session = session,
    bias = bias,
    doLvl = doLvl,
    pdh = pdh,
    pdl = pdl
)










