package com.example.sniperflow.domain.metrics

import java.util.TimeZone

object UserTimezone {
    @Volatile
    var tzId: String = "Africa/Johannesburg"

    fun timeZone(): TimeZone = TimeZone.getTimeZone(tzId)
}


