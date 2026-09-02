package com.lvjiang.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.view.View
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.Python
import java.util.concurrent.Executors

/**
 * MainActivity — 主页：权限状态卡 + 功能区 + 开发者自检。
 *
 * 权限状态卡（普通用户可见面）：明确显示辅助与 PC 连接状态。PC 设备端手势
 * 只要求无障碍（或高级用户使用 Shizuku），不把手机独立任务所需的悬浮窗
 * 误报为前置条件；悬浮窗与通知都在功能区按需授权。
 *
 * 功能区：调律参数配置入口（TuningConfigActivity，不依赖任何权限）+
 * 悬浮图标启停合一按钮（文案随 FloatService.isRunning 切换）。
 *
 * 高级面板（默认折叠）：Phase 0 以来的开发者自检按钮全部在这里。
 *
 * 另外接受一个由 adb 触发的自检入口，用于不依赖手点按钮的验证：
 *   adb shell am start -n com.lvjiang.app/.MainActivity --es selftest ocr
 * 自检模式下隐藏权限卡与功能区、展开高级面板：布局回到与实机验证过的
 * 旧版一致，e2e 闭环的 OCR 目标（「自检：点击回显」按钮与状态行）不受干扰。
 */
class MainActivity : AppCompatActivity() {

    private lateinit var statusText: TextView
    private val executor = Executors.newSingleThreadExecutor()
    private val ui = Handler(Looper.getMainLooper())
    private val statusPoller = object : Runnable {
        override fun run() {
            if (!isSelfTest()) {
                refreshGuide()
                refreshStatus()
                ui.postDelayed(this, STATUS_POLL_INTERVAL_MS)
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        statusText = findViewById(R.id.status_text)

        findViewById<TextView>(R.id.advanced_toggle).setOnClickListener { toggleAdvanced() }
        findViewById<Button>(R.id.btn_next).setOnClickListener { onNextStep() }

        // 功能区：配置页不依赖任何权限，随时可进；悬浮启停合一按钮
        findViewById<Button>(R.id.btn_tuning_config).setOnClickListener {
            startActivity(Intent(this, TuningConfigActivity::class.java))
        }
        findViewById<Button>(R.id.btn_float_toggle).setOnClickListener { onFloatToggle() }
        // 屏幕标定：从主页进来游戏不在底下，页内再按「重新截图」前请先切到游戏大厅，
        // 常规入口是悬浮面板里的「屏幕标定」（游戏在前台时直接拍）
        findViewById<Button>(R.id.btn_calib).setOnClickListener {
            startActivity(Intent(this, CalibActivity::class.java))
        }

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

        findViewById<Button>(R.id.btn_a11y).setOnClickListener {
            if (A11yBridge.isReady()) {
                toast("无障碍服务已连接")
                refreshStatus()
            } else {
                // 系统不允许应用自己打开无障碍开关，只能把用户送到设置页。
                // 这也是运行期唯一的恢复手段：服务被系统清掉后没有自动重连的 API。
                toast("请在列表中找到「律匠自动操作」并开启")
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
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
                    PyBridge.ensureStarted(this)?.let { throw IllegalStateException(it) }
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

    // ── 首次启动引导 ──────────────────────────────────────

    /** 当前引导进度：PC 手势只把辅助通道作为硬门槛。 */
    private enum class GuideStep { A11Y, READY }

    private fun currentStep(): GuideStep = when {
        !A11yBridge.isReady() -> GuideStep.A11Y
        else -> GuideStep.READY
    }

    private fun refreshGuide() {
        val a11y = A11yBridge.isReady()
        val overlay = Settings.canDrawOverlays(this)
        val notif = notificationGranted()

        findViewById<TextView>(R.id.check_a11y).text =
            "${mark(a11y)} 辅助已开启（PC 端手势必需）"
        findViewById<TextView>(R.id.check_pc_connection).text = if (AgentServer.isPcConnected()) {
            "✅ PC 已连接（${AgentServer.activeConnectionCount()}）"
        } else {
            "⬜ PC 未连接（等待 ADB 连接）"
        }
        findViewById<TextView>(R.id.check_overlay).text =
            "${mark(overlay)} 悬浮窗（仅手机独立运行任务需要）"
        findViewById<TextView>(R.id.check_notification).text =
            "${mark(notif)} 通知（建议，缺了只是看不到运行状态通知）"

        val btn = findViewById<Button>(R.id.btn_next)
        val hint = findViewById<TextView>(R.id.guide_hint)
        when (currentStep()) {
            GuideStep.A11Y -> {
                btn.text = getString(R.string.guide_step_a11y)
                hint.text = getString(R.string.guide_hint_a11y)
            }
            GuideStep.READY -> {
                btn.text = getString(R.string.guide_all_ready)
                hint.text = getString(R.string.guide_hint_ready)
            }
        }

        // 悬浮启停合一按钮：文案随运行状态切换
        findViewById<Button>(R.id.btn_float_toggle).text = getString(
            if (FloatService.isRunning) R.string.btn_stop_float
            else R.string.btn_start_float
        )
    }

    /** 功能区悬浮图标启停：未运行时启动（需悬浮窗权限），运行中停止 */
    private fun onFloatToggle() {
        if (FloatService.isRunning) {
            stopService(Intent(this, FloatService::class.java))
        } else {
            if (!Settings.canDrawOverlays(this)) {
                toast("手机独立运行任务需要悬浮窗权限")
                startActivity(
                    Intent(
                        Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                        Uri.parse("package:$packageName"),
                    )
                )
                return
            }
            startForegroundService(Intent(this, FloatService::class.java))
        }
        // isRunning 要等服务生命周期回调跑完才翻转，稍等一拍再刷新
        statusText.postDelayed({ refreshGuide() }, 500)
    }

    private fun onNextStep() {
        when (currentStep()) {
            GuideStep.A11Y -> {
                toast("请在列表里找到「律匠自动操作」并开启")
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            }
            GuideStep.READY -> toast(
                if (AgentServer.isPcConnected()) "辅助已开启，PC 已连接"
                else "辅助已开启，正在等待 PC 通过 ADB 连接"
            )
        }
    }

    private fun notificationGranted(): Boolean =
        android.os.Build.VERSION.SDK_INT < 33 ||
            checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED

    private fun mark(ok: Boolean) = if (ok) "✅" else "⬜"

    private fun toggleAdvanced() {
        val panel = findViewById<View>(R.id.advanced_panel)
        val expanded = panel.visibility == View.VISIBLE
        panel.visibility = if (expanded) View.GONE else View.VISIBLE
        findViewById<TextView>(R.id.advanced_toggle).text =
            getString(if (expanded) R.string.advanced_collapsed else R.string.advanced_expanded)
    }

    /** 自检模式：隐藏权限卡与功能区、展开高级面板，让布局回到实机验证过的旧版形态 */
    private fun enterSelfTestLayout() {
        for (id in intArrayOf(R.id.guide_card, R.id.features_section)) {
            findViewById<View>(id).visibility = View.GONE
        }
        findViewById<View>(R.id.advanced_panel).visibility = View.VISIBLE
        findViewById<TextView>(R.id.advanced_toggle).text =
            getString(R.string.advanced_expanded)
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
        enterSelfTestLayout()
        // 与 Python 侧 smoke._log_path() 同一个文件（HOME 就是 filesDir）
        val logFile = java.io.File(filesDir, SELFTEST_LOG)
        executor.execute {
            val report = try {
                PyBridge.ensureStarted(this)?.let { throw IllegalStateException(it) }
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
        if (!isSelfTest()) {
            refreshGuide()
            refreshStatus()
            ui.removeCallbacks(statusPoller)
            ui.postDelayed(statusPoller, STATUS_POLL_INTERVAL_MS)
        }
    }

    override fun onPause() {
        ui.removeCallbacks(statusPoller)
        super.onPause()
    }

    private fun isSelfTest(): Boolean = intent?.getStringExtra("selftest") != null

    private fun refreshStatus() {
        val overlay = if (Settings.canDrawOverlays(this)) "已授予" else "未授予"
        val a11y = if (A11yBridge.isReady()) "已开启" else "未开启（PC 手势必需）"
        val shizuku = when {
            !ShellBridge.isShizukuAlive() -> "未运行"
            ShellBridge.hasPermission() -> "已授权"
            else -> "未授权"
        }
        val pc = if (AgentServer.isPcConnected()) {
            "已连接（${AgentServer.activeConnectionCount()}）· ${AgentServer.lastCommandSummary()}"
        } else {
            "未连接"
        }
        statusText.text =
            "PC：$pc\n辅助：$a11y\n悬浮窗：$overlay（手机独立运行可选）\nShizuku：$shizuku（可选）"
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    companion object {
        private const val SELFTEST_TAG = "LvjiangSelfTest"

        /** 报告文件名：这台设备的 logcat 会滤掉普通应用的日志，文件才是可靠通道 */
        private const val SELFTEST_LOG = "selftest.log"

        /** 哨兵行：adb 侧靠它判断报告已输出完整，不必靠等固定秒数 */
        private const val SELFTEST_END = "=== SELFTEST END ==="
        private const val STATUS_POLL_INTERVAL_MS = 1000L
    }
}
