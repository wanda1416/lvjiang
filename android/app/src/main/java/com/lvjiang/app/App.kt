package com.lvjiang.app

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager

/** 所有业务配置由 PC 下发；应用启动、升级不改动手机配置和数据库。 */
class App : Application() {
    override fun onCreate() {
        super.onCreate()
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "律匠运行状态", NotificationManager.IMPORTANCE_LOW)
                .apply { description = "悬浮控制与任务运行的前台服务通知" }
        )
        RuntimeDiagnostics.initialize(this)
        ScreenMap.init(this)
        AgentServer.appContext = this
        AgentServer.start()
    }

    companion object {
        const val CHANNEL_ID = "lvjiang_service"
    }
}
