package com.lvjiang.app

import android.content.ComponentName
import android.content.ServiceConnection
import android.os.IBinder
import android.util.Log
import rikka.shizuku.Shizuku

/**
 * ShellBridge — Shizuku shell 通道客户端（进程内单例）。
 *
 * 职责：
 * - Shizuku 可用性检测与权限申请；
 * - 绑定 ShellService（UserService，运行在 shell uid 进程）；
 * - 对外提供同步 exec()：input tap / input swipe / screencap 等。
 *
 * 与 PC 端 src/lvjiang/core/android/input.py、adb_capture.py 的命令语义完全同源。
 */
object ShellBridge {

    private const val TAG = "ShellBridge"
    private const val PERMISSION_REQUEST_CODE = 9001

    @Volatile
    private var service: IShellService? = null

    private val userServiceArgs by lazy {
        Shizuku.UserServiceArgs(
            ComponentName(BuildConfig.APPLICATION_ID, ShellService::class.java.name)
        )
            .daemon(false)
            .processNameSuffix("shell")
            .debuggable(BuildConfig.DEBUG)
            .version(BuildConfig.VERSION_CODE)
    }

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, binder: IBinder?) {
            if (binder != null && binder.pingBinder()) {
                service = IShellService.Stub.asInterface(binder)
                Log.i(TAG, "ShellService connected")
            } else {
                Log.e(TAG, "ShellService binder invalid")
            }
        }

        override fun onServiceDisconnected(name: ComponentName) {
            service = null
            Log.w(TAG, "ShellService disconnected")
        }
    }

    /** Shizuku 是否在运行（手机上已激活） */
    fun isShizukuAlive(): Boolean = try {
        Shizuku.pingBinder()
    } catch (_: Throwable) {
        false
    }

    /** 是否已获得 Shizuku 授权 */
    fun hasPermission(): Boolean = try {
        isShizukuAlive() && Shizuku.checkSelfPermission() == android.content.pm.PackageManager.PERMISSION_GRANTED
    } catch (_: Throwable) {
        false
    }

    /** 发起授权申请（结果由 Shizuku 弹窗处理，通过 listener 回调） */
    fun requestPermission(onResult: (granted: Boolean) -> Unit) {
        if (hasPermission()) {
            onResult(true)
            return
        }
        val listener = object : Shizuku.OnRequestPermissionResultListener {
            override fun onRequestPermissionResult(requestCode: Int, grantResult: Int) {
                if (requestCode == PERMISSION_REQUEST_CODE) {
                    Shizuku.removeRequestPermissionResultListener(this)
                    onResult(grantResult == android.content.pm.PackageManager.PERMISSION_GRANTED)
                }
            }
        }
        Shizuku.addRequestPermissionResultListener(listener)
        Shizuku.requestPermission(PERMISSION_REQUEST_CODE)
    }

    /** 绑定 ShellService（已授权前提下），幂等 */
    fun ensureService() {
        if (service != null) return
        if (!hasPermission()) {
            Log.w(TAG, "ensureService: no permission")
            return
        }
        try {
            Shizuku.bindUserService(userServiceArgs, connection)
        } catch (e: Throwable) {
            Log.e(TAG, "bindUserService failed", e)
        }
    }

    /** 等待服务就绪（绑定是异步的） */
    fun awaitReady(timeoutMs: Long = 5000): Boolean {
        if (service != null) return true
        ensureService()
        val deadline = System.currentTimeMillis() + timeoutMs
        while (service == null && System.currentTimeMillis() < deadline) {
            Thread.sleep(50)
        }
        return service != null
    }

    /**
     * 同步执行 shell 命令，返回原始 stdout 字节流。
     * 调用方保证不在主线程调用（Binder 大数据 + 命令耗时）。
     */
    fun exec(vararg cmd: String): ByteArray {
        val svc = service ?: run {
            if (!awaitReady()) return "[stderr]ShellService not ready".toByteArray()
            service!!
        }
        return svc.exec(arrayOf(*cmd))
    }

    /** 文本命令便捷封装 */
    fun execText(vararg cmd: String): String = exec(*cmd).toString(Charsets.UTF_8).trim()

    /** 点击（语义对齐 PC 端 AdbInput.click_screen 最终下发的命令） */
    fun tap(x: Int, y: Int): String = execText("input", "tap", x.toString(), y.toString())

    /** 滑动/拖拽 */
    fun swipe(x1: Int, y1: Int, x2: Int, y2: Int, durationMs: Int): String =
        execText("input", "swipe", "$x1", "$y1", "$x2", "$y2", "$durationMs")

    /** 截屏 PNG 字节流（语义对齐 PC 端 AdbCapture 的 adb exec-out screencap -p） */
    fun screencapPng(): ByteArray = exec("screencap", "-p")

    fun unbind() {
        try {
            Shizuku.unbindUserService(userServiceArgs, connection, true)
        } catch (_: Throwable) {
        }
        service = null
    }
}
