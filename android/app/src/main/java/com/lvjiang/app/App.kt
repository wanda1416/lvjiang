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
 * 解压策略：用 versionCode 做 stamp，每次升级 APK 后首次启动清空目录再全量重解，
 * 使设备上的 config/system 与 APK 内容完全一致；同一版本重复启动跳过。
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
        // 屏幕映射（截图坐标 → 输入坐标）需要 Context 读标定文件与屏幕尺寸
        ScreenMap.init(this)
        AgentServer.appContext = this
        // PC 端代理通道：进程在（无障碍已绑定 / 悬浮服务在跑 / 用户打开了主页）就监听，
        // 由 PC 经 adb forward 连入使用无障碍截图与手势，见 AgentServer
        AgentServer.start()
    }

    /**
     * 把 assets/config/system 解压到 filesDir/lvjiang/。
     *
     * 只在 APK 升级后首次启动时执行（versionCode 变化）；同版本重复启动跳过。
     *
     * 先整目录删掉再解压：只覆盖同名文件的话，上游删除或移动过的文件会永远留在
     * 设备上。`.wf` 挪个目录就会在用户侧变成两份同 id 脚本（一份生效一份幽灵），
     * 场景、布局、参照图被删除后同样会继续以旧内容加载。
     *
     * 删的只是 config/system —— 用户自己的改动落在 config/local（无 .git 即用户
     * 模式），不在这个目录里。解压中途失败时不写 stamp，下次启动整轮重来。
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
            if (systemTarget.exists() && !systemTarget.deleteRecursively()) {
                // 删不干净就继续铺新的：残留文件仍是老问题，但总好过没有配置
                Log.w(TAG, "旧系统配置未能完全删除，继续解压覆盖")
            }
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
