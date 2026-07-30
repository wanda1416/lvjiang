package com.lvjiang.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.util.concurrent.Executors

/**
 * MainActivity — 权限引导与自检页。
 *
 * Phase 0 功能：
 * 1. 权限状态一览：悬浮窗 / Shizuku / 通知；
 * 2. 一键申请各项权限；
 * 3. 启动/停止悬浮图标服务；
 * 4. Python 运行时冒烟测试（Chaquopy）；
 * 5. Shizuku 通道自检（id 命令 → 应输出 uid=2000(shell)）。
 *
 * 另外接受一个由 adb 触发的自检入口，用于不依赖手点按钮的验证：
 *   adb shell am start -n com.lvjiang.app/.MainActivity --es selftest ocr
 * 报告逐行写入 logcat 的 LvjiangSelfTest 标签。
 */
class MainActivity : AppCompatActivity() {

    private lateinit var statusText: TextView
    private val executor = Executors.newSingleThreadExecutor()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        statusText = findViewById(R.id.status_text)

        findViewById<Button>(R.id.btn_overlay).setOnClickListener {
            if (!Settings.canDrawOverlays(this)) {
                startActivity(
                    Intent(
                        Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                        Uri.parse("package:$packageName"),
                    )
                )
            } else {
                toast("悬浮窗权限已授予")
            }
        }

        findViewById<Button>(R.id.btn_shizuku).setOnClickListener {
            when {
                !ShellBridge.isShizukuAlive() -> toast("Shizuku 未运行：请先安装并激活 Shizuku")
                ShellBridge.hasPermission() -> {
                    toast("Shizuku 已授权")
                    ShellBridge.ensureService()
                }
                else -> ShellBridge.requestPermission { granted ->
                    runOnUiThread {
                        toast(if (granted) "Shizuku 授权成功" else "Shizuku 授权被拒绝")
                        if (granted) ShellBridge.ensureService()
                        refreshStatus()
                    }
                }
            }
        }

        findViewById<Button>(R.id.btn_notification).setOnClickListener {
            requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 100)
        }

        findViewById<Button>(R.id.btn_start_float).setOnClickListener {
            if (!Settings.canDrawOverlays(this)) {
                toast("请先授予悬浮窗权限")
                return@setOnClickListener
            }
            startForegroundService(Intent(this, FloatService::class.java))
        }

        findViewById<Button>(R.id.btn_stop_float).setOnClickListener {
            stopService(Intent(this, FloatService::class.java))
        }

        findViewById<Button>(R.id.btn_test_shell).setOnClickListener {
            executor.execute {
                val result = if (ShellBridge.awaitReady()) {
                    ShellBridge.execText("id")
                } else {
                    "ShellService 未就绪（检查 Shizuku 授权）"
                }
                runOnUiThread { statusText.text = "shell 自检：$result" }
            }
        }

        findViewById<Button>(R.id.btn_test_python).setOnClickListener {
            executor.execute {
                val result = try {
                    if (!Python.isStarted()) Python.start(AndroidPlatform(this))
                    Python.getInstance().getModule("hello").callAttr("smoke_test").toString()
                } catch (e: Throwable) {
                    "Python 启动失败：$e"
                }
                runOnUiThread { statusText.text = "Python 自检：$result" }
            }
        }

        // 三通道闭环自检的被点目标：只做一件事——把状态行换成一个点击前不存在的文案。
        // 于是「点击确实落地了」这件事可以由下一张截图 + OCR 自己读出来，不需要人眼确认。
        findViewById<Button>(R.id.btn_test_click).setOnClickListener {
            statusText.text = getString(R.string.status_click_ok)
        }

        handleSelfTest(intent)
    }

    // launchMode 是默认的 standard，重复 am start 通常会走 onCreate；
    // 但若系统复用了实例则只有这里会被调到，两处都接上才不会出现「命令没反应」。
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleSelfTest(intent)
    }

    /** 响应 `--es selftest <target>`，在后台线程跑 Python 自检并把报告落盘 */
    private fun handleSelfTest(intent: Intent?) {
        val target = intent?.getStringExtra("selftest") ?: return
        // 与 Python 侧 smoke._log_path() 同一个文件（HOME 就是 filesDir）
        val logFile = java.io.File(filesDir, SELFTEST_LOG)
        executor.execute {
            val report = try {
                if (!Python.isStarted()) Python.start(AndroidPlatform(this))
                Python.getInstance()
                    .getModule("lvjiang.core.ondevice.smoke")
                    .callAttr("run", target)
                    .toString()
            } catch (e: Throwable) {
                // Python 侧自己会捕获各步异常；能走到这里说明连模块都没导入成功，
                // 这种情况 Python 一个字都写不出来，报告和哨兵都只能由这里补上
                val text = "自检未能启动：\n" + Log.getStackTraceString(e)
                runCatching { logFile.appendText(text + "\n" + SELFTEST_END + "\n") }
                text.lineSequence().forEach { Log.i(SELFTEST_TAG, it) }
                text
            }
            // 报告正文与哨兵行都由 Python 侧逐行实时写同一个文件（卡住时也能看到进度），
            // 这里不再重复写，只把完整报告回填到界面上
            runOnUiThread { statusText.text = report }
        }
    }

    override fun onResume() {
        super.onResume()
        // 自检期间不刷状态：刷一下就会把「点击已生效」或报告正文覆掉，
        // 而闭环自检正是靠读屏幕上的这些字来判定的
        if (intent?.getStringExtra("selftest") == null) {
            refreshStatus()
        }
    }

    private fun refreshStatus() {
        val overlay = if (Settings.canDrawOverlays(this)) "已授予" else "未授予"
        val shizuku = when {
            !ShellBridge.isShizukuAlive() -> "未运行"
            ShellBridge.hasPermission() -> "已授权"
            else -> "未授权"
        }
        statusText.text = "悬浮窗：$overlay\nShizuku：$shizuku"
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    companion object {
        private const val SELFTEST_TAG = "LvjiangSelfTest"

        /** 报告文件名：这台设备的 logcat 会滤掉普通应用的日志，文件才是可靠通道 */
        private const val SELFTEST_LOG = "selftest.log"

        /** 哨兵行：adb 侧靠它判断报告已输出完整，不必靠等固定秒数 */
        private const val SELFTEST_END = "=== SELFTEST END ==="
    }
}
