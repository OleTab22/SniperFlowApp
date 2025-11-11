package com.example.sniperflow.notifications

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.content.pm.PackageManager
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.example.sniperflow.MainActivity
import com.example.sniperflow.data.user.UserProfileRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.util.Calendar

// FCM notification manager - handles all push notifications
// Supports quiet hours and user preferences
object FcmNotificationManager {
    private const val CHANNEL_PLAN = "sniperflow_plan"
    private const val CHANNEL_ALERTS = "sniperflow_alerts"
    private const val CHANNEL_ECON = "sniperflow_econ"
    private const val CHANNEL_NEWS = "sniperflow_news"
    private const val CHANNEL_SYSTEM = "sniperflow_system"
    
    fun initialize(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            
            listOf(
                NotificationChannel(CHANNEL_PLAN, "Plan Notifications", NotificationManager.IMPORTANCE_DEFAULT),
                NotificationChannel(CHANNEL_ALERTS, "Alerts", NotificationManager.IMPORTANCE_HIGH),
                NotificationChannel(CHANNEL_ECON, "Economic Events", NotificationManager.IMPORTANCE_DEFAULT),
                NotificationChannel(CHANNEL_NEWS, "News", NotificationManager.IMPORTANCE_LOW),
                NotificationChannel(CHANNEL_SYSTEM, "System", NotificationManager.IMPORTANCE_LOW)
            ).forEach { nm.createNotificationChannel(it) }
        }
    }
    
    suspend fun shouldSuppress(context: Context): Boolean {
        val repo = UserProfileRepository.create(context)
        val profile = repo.getProfile()
        
        if (!profile.quietHoursEnabled) return false
        
        val cal = Calendar.getInstance()
        val currentHour = cal.get(Calendar.HOUR_OF_DAY)
        val startHour = profile.quietHoursStart
        val endHour = profile.quietHoursEnd
        
        return when {
            startHour > endHour -> currentHour >= startHour || currentHour < endHour
            else -> currentHour >= startHour && currentHour < endHour
        }
    }
    
    @Suppress("unused")
    fun showNotification(
        context: Context,
        channel: String,
        title: String,
        body: String,
        type: NotificationType = NotificationType.ALERT
    ) {
        CoroutineScope(Dispatchers.IO).launch {
            if (shouldSuppress(context)) {
                return@launch // Skip if quiet hours
            }
            
            val profileRepo = UserProfileRepository.create(context)
            val profile = profileRepo.getProfile()
            
            // Check user preferences
            val enabled = when (type) {
                NotificationType.PLAN -> profile.notifyPlanReady
                NotificationType.GATE_PASS, NotificationType.GATE_BLOCKED -> profile.notifyGatePass || profile.notifyGateBlocked
                NotificationType.ECON -> profile.notifyEcon
                NotificationType.NEWS -> profile.notifyNews
                NotificationType.SYSTEM -> true
                NotificationType.ALERT -> true
            }
            
            if (!enabled) return@launch
            
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                val granted = ContextCompat.checkSelfPermission(
                    context,
                    Manifest.permission.POST_NOTIFICATIONS
                ) == PackageManager.PERMISSION_GRANTED
                if (!granted) {
                    return@launch
                }
            }
            
            val intent = Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
            }
            val pendingIntent = PendingIntent.getActivity(
                context, 0, intent,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
            )
            
            val notification = NotificationCompat.Builder(context, channel)
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle(title)
                .setContentText(body)
                .setContentIntent(pendingIntent)
                .setAutoCancel(true)
                .setPriority(
                    when (channel) {
                        CHANNEL_ALERTS -> NotificationCompat.PRIORITY_HIGH
                        else -> NotificationCompat.PRIORITY_DEFAULT
                    }
                )
                .build()
            
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            nm.notify(System.currentTimeMillis().toInt(), notification)
        }
    }
    
    enum class NotificationType {
        PLAN, GATE_PASS, GATE_BLOCKED, ECON, NEWS, SYSTEM, ALERT
    }
}

