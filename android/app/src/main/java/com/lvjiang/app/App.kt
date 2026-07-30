package com.lvjiang.app

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager

/**
 * App — 应用入口：通知渠道注册。
 * Shizuku Binder 由 ShizukuProvider 自动接收，无需手动初始化。
 */
class App : Application() {

    override fun onCreate() {
        super.onCreate()
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                "律匠运行状态",
                NotificationManager.IMPORTANCE_LOW,
            ).apply { description = "悬浮控制与任务运行的前台服务通知" }
        )
    }

    companion object {
        const val CHANNEL_ID = "lvjiang_service"
    }
}
