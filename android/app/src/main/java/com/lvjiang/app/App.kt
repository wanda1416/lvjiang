package com.lvjiang.app

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.util.Log
import java.io.File
import java.io.FileOutputStream

/**
 * App — 应用入口：通知渠道注册 + 系统配置解压。
 * Shizuku Binder 由 ShizukuProvider 自动接收，无需手动初始化。
 *
 * 系统配置（config/system 下的场景/工作流 YAML/布局/参照图）由 Gradle Sync
 * 任务打进 APK assets，onCreate 时解压到 filesDir/lvjiang/config/system——
 * Python 侧 constants.PROJECT_ROOT 在安卓端指向 filesDir/lvjiang，两条路径在此汇合。
 * 用户修改落 config/local（无 .git → 用户模式），不会被升级解压覆盖。
 *
 * 解压策略：用 versionCode 做 stamp，每次升级 APK 后首次启动全量重解；
 * 同一版本重复启动不重复写（跳过已存在目录）。
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
        syncSystemConfig()
    }

    /**
     * 把 assets/config/system 解压到 filesDir/lvjiang/。
     *
     * 只在 APK 升级后首次启动时执行（versionCode 变化）；同版本重复启动跳过。
     * 不删旧文件再写——直接覆盖同名，残留的旧文件不影响（Python 侧按名字找）。
     */
    private fun syncSystemConfig() {
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        val lastVersion = prefs.getInt(KEY_VERSION, 0)
        val currentVersion = try {
            packageManager.getPackageInfo(packageName, 0).let {
                if (android.os.Build.VERSION.SDK_INT >= 28) it.longVersionCode.toInt() else it.versionCode
            }
        } catch (_: Exception) { 0 }

        val systemTarget = File(filesDir, "lvjiang/config/system")
        if (lastVersion == currentVersion && systemTarget.isDirectory) {
            return  // 同版本且目录在，跳过
        }

        try {
            systemTarget.mkdirs()
            copyAssetDir("config/system", systemTarget)
            prefs.edit().putInt(KEY_VERSION, currentVersion).apply()
            Log.i(TAG, "系统配置已解压到 ${systemTarget.absolutePath}（version=$currentVersion）")
        } catch (e: Exception) {
            Log.e(TAG, "配置解压失败", e)
        }
    }

    /** 递归解压 assets 子目录到目标文件夹 */
    private fun copyAssetDir(assetPath: String, targetDir: File) {
        val entries = assets.list(assetPath) ?: run {
            // 叶子文件：直接拷贝
            copyAssetFile(assetPath, File(targetDir, assetPath.substringAfterLast("/")))
            return
        }
        if (entries.isEmpty()) {
            // 空目录也创建（保持结构完整）
            targetDir.mkdirs()
            return
        }
        targetDir.mkdirs()
        for (entry in entries) {
            val childPath = "$assetPath/$entry"
            val children = assets.list(childPath)
            if (children != null && children.isNotEmpty()) {
                copyAssetDir(childPath, File(targetDir, entry))
            } else {
                copyAssetFile(childPath, File(targetDir, entry))
            }
        }
    }

    private fun copyAssetFile(assetPath: String, targetFile: File) {
        assets.open(assetPath).use { input ->
            FileOutputStream(targetFile).use { output ->
                input.copyTo(output)
            }
        }
    }

    companion object {
        const val CHANNEL_ID = "lvjiang_service"
        private const val TAG = "App"
        private const val PREFS = "lvjiang_meta"
        private const val KEY_VERSION = "system_config_version"
    }
}
