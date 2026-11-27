package com.lvjiang.app

import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import java.io.File
import java.util.concurrent.Executors

/**
 * 应用内 APK 后台下载与自动安装管理器。
 *
 * 使用系统 [DownloadManager] 在后台下载更新包（通知栏展示实时进度，切后台不断线），
 * 下载完成后自动通过 [FileProvider] 拉起系统安装器；若尚未授予「安装未知应用」权限，
 * 引导用户前往系统设置授权并在返回前台后自动继续安装。
 */
object ApkUpdater {

    private const val TAG = "ApkUpdater"
    private const val APK_MIME = "application/vnd.android.package-archive"

    /** 断点状态：应用被杀后重新进前台还能续做「校验 + 安装」。 */
    private const val PREFS_NAME = "apk_update"
    private const val KEY_DOWNLOAD_ID = "download_id"
    private const val KEY_VERSION = "version"
    private const val KEY_FILE = "file"

    private val mainHandler = Handler(Looper.getMainLooper())
    private val lock = Any()
    /** 校验要算哈希并可能联网取 SHA256SUMS，放后台线程做，避免卡 UI。 */
    private val verifyExecutor = Executors.newSingleThreadExecutor()

    private var activeDownloadId: Long = -1L
    private var activeVersion: String = ""
    private var activeReceiver: BroadcastReceiver? = null
    private var pendingInstallFile: File? = null

    fun downloadAndInstall(
        context: Context,
        apkUrl: String,
        apkName: String,
        version: String,
        onMessage: (String) -> Unit,
    ) {
        val trimmedUrl = apkUrl.trim()
        if (!trimmedUrl.startsWith("https://") && !trimmedUrl.startsWith("http://")) {
            onMessage("更新包下载地址无效，请前往发布页手动下载")
            return
        }
        val appContext = context.applicationContext
        val downloadManager = appContext.getSystemService(Context.DOWNLOAD_SERVICE) as? DownloadManager
        if (downloadManager == null) {
            onMessage("系统下载服务不可用，请前往发布页手动下载")
            return
        }

        val safeName = apkName.trim()
            .substringAfterLast('/')
            .substringAfterLast('\\')
            .ifEmpty { "lvjiang-v$version.apk" }
        val baseDir = appContext.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
        if (baseDir == null) {
            onMessage("存储目录不可用，无法下载更新包")
            return
        }
        val updatesDir = File(baseDir, "updates").apply { mkdirs() }
        val targetFile = File(updatesDir, safeName)

        synchronized(lock) {
            if (activeDownloadId != -1L && activeVersion == version) {
                onMessage("正在后台下载 v$version，请留意通知栏进度")
                return
            }
            cleanupReceiverLocked(appContext)
            if (targetFile.exists()) {
                runCatching { targetFile.delete() }
            }

            val request = DownloadManager.Request(Uri.parse(trimmedUrl)).apply {
                setTitle("律匠 v$version")
                setDescription("正在后台下载更新包…")
                setMimeType(APK_MIME)
                setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                setAllowedOverMetered(true)
                setAllowedOverRoaming(true)
                setDestinationInExternalFilesDir(appContext, Environment.DIRECTORY_DOWNLOADS, "updates/$safeName")
            }

            val downloadId = try {
                downloadManager.enqueue(request)
            } catch (e: Throwable) {
                Log.e(TAG, "入队下载失败: $trimmedUrl", e)
                onMessage("启动下载失败：${e.message ?: "请稍后重试"}")
                return
            }

            activeDownloadId = downloadId
            activeVersion = version
            rememberDownload(appContext, downloadId, version, targetFile)

            val receiver = object : BroadcastReceiver() {
                override fun onReceive(ctx: Context, intent: Intent) {
                    if (intent.action != DownloadManager.ACTION_DOWNLOAD_COMPLETE) return
                    val finishedId = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1L)
                    if (finishedId != downloadId) return
                    handleDownloadComplete(appContext, downloadManager, downloadId, targetFile, version)
                }
            }
            activeReceiver = receiver
            ContextCompat.registerReceiver(
                appContext,
                receiver,
                IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE),
                ContextCompat.RECEIVER_EXPORTED,
            )
        }

        onMessage("已开始后台下载 v$version，完成后将自动拉起安装")
    }

    /**
     * 下载在应用被杀之后才完成的情况：重新进前台时按断点记录补做「校验 + 安装」。
     *
     * 运行时广播只在进程活着时送达，断点记录是这条路径的唯一兜底。
     */
    fun resumeInterruptedInstall(context: Context) {
        val appContext = context.applicationContext
        val store = prefs(appContext)
        val downloadId = store.getLong(KEY_DOWNLOAD_ID, -1L)
        if (downloadId <= 0L) return
        val version = store.getString(KEY_VERSION, "").orEmpty()
        val file = File(store.getString(KEY_FILE, "").orEmpty())
        val downloadManager = appContext.getSystemService(Context.DOWNLOAD_SERVICE) as? DownloadManager
        if (downloadManager == null || !file.isAbsolute) {
            forgetDownload(appContext)
            return
        }

        var status = DownloadManager.STATUS_FAILED
        var reason = 0
        runCatching {
            downloadManager.query(DownloadManager.Query().setFilterById(downloadId))?.use { cursor ->
                if (cursor.moveToFirst()) {
                    status = cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))
                    reason = cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_REASON))
                }
            }
        }

        when {
            status == DownloadManager.STATUS_SUCCESSFUL && file.isFile -> {
                forgetDownload(appContext)
                showToast(appContext, "v$version 下载完成，正在校验安装包…")
                verifyThenInstall(appContext, file, version)
            }
            // 下载失败或记录已失效：清掉断点，避免每次进前台都空跑
            status == DownloadManager.STATUS_FAILED -> {
                Log.w(TAG, "续装失败: 下载未成功 status=$status reason=$reason")
                forgetDownload(appContext)
            }
            // 仍在下载/排队：等下载完成后的广播，或下次进前台再查
            else -> Unit
        }
    }

    /** 当前是否处于漫游网络。只用于下载前提示，不改变是否允许下载。 */
    fun isRoaming(context: Context): Boolean {
        val manager = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            ?: return false
        val network = manager.activeNetwork ?: return false
        val capabilities = manager.getNetworkCapabilities(network) ?: return false
        if (!capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) return false
        return !capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_ROAMING)
    }

    /**
     * 安装前校验：（后台线程）本地 SHA-256 + 官方 `SHA256SUMS.txt` 比对，
     * 校验和不匹配就删包拒装；取不到官方校验和只提示、不拦着装。
     */
    private fun verifyThenInstall(appContext: Context, apkFile: File, version: String) {
        verifyExecutor.execute {
            val result = runCatching {
                PyBridge.verifyApk(appContext, apkFile.absolutePath, version)
            }.onFailure { Log.w(TAG, "校验安装包失败: ${apkFile.absolutePath}", it) }.getOrNull()
            mainHandler.post {
                if (result == null || !result.optBoolean("ok")) {
                    val reason = result?.optString("message")?.takeIf { it.isNotBlank() }
                        ?: "更新包校验失败"
                    showToast(appContext, "$reason，请重新下载")
                    apkFile.delete()
                    return@post
                }
                val verified = if (result.isNull("verified")) null else result.optBoolean("verified")
                val message = result.optString("message")
                if (verified == false) {
                    showToast(appContext, message.ifBlank { "更新包校验和不匹配，已放弃安装" })
                    apkFile.delete()
                    return@post
                }
                if (message.isNotBlank()) showToast(appContext, message)
                installApk(appContext, apkFile)
            }
        }
    }

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    private fun rememberDownload(context: Context, downloadId: Long, version: String, file: File) {
        prefs(context).edit()
            .putLong(KEY_DOWNLOAD_ID, downloadId)
            .putString(KEY_VERSION, version)
            .putString(KEY_FILE, file.absolutePath)
            .apply()
    }

    private fun forgetDownload(context: Context) {
        prefs(context).edit()
            .remove(KEY_DOWNLOAD_ID)
            .remove(KEY_VERSION)
            .remove(KEY_FILE)
            .apply()
    }

    /** 用户在系统设置授予「安装未知应用」权限返回前台时，自动续接安装。 */
    fun resumePendingInstallIfAllowed(context: Context) {
        val file = synchronized(lock) { pendingInstallFile } ?: return
        val appContext = context.applicationContext
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            !appContext.packageManager.canRequestPackageInstalls()
        ) {
            return
        }
        synchronized(lock) { pendingInstallFile = null }
        if (file.isFile) {
            launchInstaller(appContext, file)
        }
    }

    private fun handleDownloadComplete(
        appContext: Context,
        downloadManager: DownloadManager,
        downloadId: Long,
        targetFile: File,
        version: String,
    ) {
        synchronized(lock) {
            if (activeDownloadId == downloadId) {
                activeDownloadId = -1L
                activeVersion = ""
            }
            cleanupReceiverLocked(appContext)
        }

        val query = DownloadManager.Query().setFilterById(downloadId)
        var status = DownloadManager.STATUS_FAILED
        var reason = 0
        runCatching {
            downloadManager.query(query)?.use { cursor ->
                if (cursor.moveToFirst()) {
                    status = cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))
                    reason = cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_REASON))
                }
            }
        }

        if (status != DownloadManager.STATUS_SUCCESSFUL || !targetFile.isFile || targetFile.length() <= 0L) {
            Log.w(TAG, "APK 下载未成功: status=$status reason=$reason file=${targetFile.absolutePath}")
            forgetDownload(appContext)
            showToast(appContext, "更新包下载失败（错误码 $reason），请重试或前往发布页下载")
            return
        }

        forgetDownload(appContext)
        showToast(appContext, "v$version 下载完成，正在校验安装包…")
        verifyThenInstall(appContext, targetFile, version)
    }

    private fun installApk(appContext: Context, apkFile: File) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            !appContext.packageManager.canRequestPackageInstalls()
        ) {
            synchronized(lock) { pendingInstallFile = apkFile }
            showToast(appContext, "请允许「安装未知应用」权限，返回后将自动继续安装")
            val settingsIntent = Intent(
                Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:${appContext.packageName}"),
            ).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            runCatching { appContext.startActivity(settingsIntent) }
                .onFailure {
                    Log.e(TAG, "无法打开安装未知应用设置页", it)
                    showToast(appContext, "请手动在系统设置中开启本应用的「安装未知应用」权限")
                }
            return
        }
        launchInstaller(appContext, apkFile)
    }

    private fun launchInstaller(appContext: Context, apkFile: File) {
        runCatching {
            val contentUri = FileProvider.getUriForFile(
                appContext,
                "${appContext.packageName}.fileprovider",
                apkFile,
            )
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(contentUri, APK_MIME)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            appContext.startActivity(intent)
        }.onFailure {
            Log.e(TAG, "拉起 APK 安装器失败: ${apkFile.absolutePath}", it)
            showToast(appContext, "无法自动打开安装器：${it.message ?: "请手动安装下载的 APK"}")
        }
    }

    private fun cleanupReceiverLocked(appContext: Context) {
        activeReceiver?.let { receiver ->
            runCatching { appContext.unregisterReceiver(receiver) }
        }
        activeReceiver = null
    }

    private fun showToast(appContext: Context, message: String) {
        mainHandler.post {
            Toast.makeText(appContext, message, Toast.LENGTH_LONG).show()
        }
    }
}
