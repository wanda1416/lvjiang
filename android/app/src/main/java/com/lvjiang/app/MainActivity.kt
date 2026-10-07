package com.lvjiang.app

import android.content.Intent
import android.content.ClipData
import android.content.ClipboardManager
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.view.View
import android.view.Menu
import android.view.MenuItem
import android.widget.Button
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import androidx.activity.OnBackPressedCallback
import java.util.concurrent.Executors

/**
 * MainActivity — 悬浮控制首页，菜单进入权限、高级功能与诊断。
 *
 * 权限状态卡（普通用户可见面）：明确显示辅助与 PC 连接状态。PC 设备端手势
 * 只要求无障碍（或高级用户使用 Shizuku），不把手机独立任务所需的悬浮窗
 * 误报为前置条件；悬浮窗与通知都在功能区按需授权。
 *
 * 功能区：独立任务参数配置入口（TaskSettingsActivity，不依赖任何权限）+
 * 悬浮图标启停合一按钮（文案随 FloatService.isRunning 切换）。
 *
 * 二级页面只是临时导航状态，不写任务或同步配置。
 *
 * 另外接受一个由 adb 触发的自检入口，用于不依赖手点按钮的验证：
 *   adb shell am start -n com.lvjiang.app/.MainActivity --es selftest ocr
 * 自检模式显示诊断页面，保留点击回显目标、状态行与报告回读。
 */
class MainActivity : AppCompatActivity() {

    private lateinit var statusText: TextView
    private enum class Page { HOME, PERMISSIONS, ADVANCED, DIAGNOSTICS }
    private var page = Page.HOME
    private lateinit var backToHome: OnBackPressedCallback
    private var runtimeReport = "尚未检查运行环境"
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
        setSupportActionBar(findViewById<Toolbar>(R.id.main_toolbar))
        backToHome = object : OnBackPressedCallback(false) {
            override fun handleOnBackPressed() { showPage(Page.HOME) }
        }
        onBackPressedDispatcher.addCallback(this, backToHome)

        statusText = findViewById(R.id.status_text)
        findViewById<Button>(R.id.btn_home_permissions).setOnClickListener { showPage(Page.PERMISSIONS) }
        findViewById<Button>(R.id.btn_copy_report).isEnabled = false

        // 诊断与首页操作：仅点击时执行，导航不会触发自检。
        findViewById<Button>(R.id.btn_runtime_check).setOnClickListener { checkRuntime() }
        findViewById<Button>(R.id.btn_copy_report).setOnClickListener {
            (getSystemService(CLIPBOARD_SERVICE) as ClipboardManager)
                .setPrimaryClip(ClipData.newPlainText("律匠诊断", runtimeReport))
            toast("诊断报告已复制，请分享前核对本地路径等私人信息")
        }
        findViewById<Button>(R.id.btn_float_toggle).setOnClickListener { onFloatToggle() }
        // 从高级功能进入标定，重新截图前需先切到游戏。
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

        // 三通道闭环自检的被点目标：只做一件事——把状态行换成一个点击前不存在的文案。
        // 于是「点击确实落地了」这件事可以由下一张截图 + OCR 自己读出来，不需要人眼确认。
        findViewById<Button>(R.id.btn_test_click).setOnClickListener {
            statusText.text = getString(R.string.status_click_ok)
        }

        val restored = savedInstanceState?.getString("main_page")
        showPage(Page.entries.firstOrNull { it.name == restored } ?: Page.HOME)
        handleSelfTest(intent)
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, MENU_TASK_SETTINGS, 0, "任务设置")
        menu.add(0, MENU_PERMISSIONS, 0, R.string.menu_permissions)
        menu.add(0, MENU_ADVANCED, 1, R.string.menu_advanced)
        menu.add(0, MENU_DIAGNOSTICS, 2, R.string.menu_diagnostics)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        when (item.itemId) {
            MENU_TASK_SETTINGS -> startActivity(Intent(this, TaskSettingsActivity::class.java))
            android.R.id.home -> showPage(Page.HOME)
            MENU_PERMISSIONS -> showPage(Page.PERMISSIONS)
            MENU_ADVANCED -> showPage(Page.ADVANCED)
            MENU_DIAGNOSTICS -> showPage(Page.DIAGNOSTICS)
            else -> return super.onOptionsItemSelected(item)
        }
        return true
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString("main_page", page.name)
        super.onSaveInstanceState(outState)
    }

    private fun showPage(next: Page) {
        page = next
        findViewById<View>(R.id.features_section).visibility = if (next == Page.HOME) View.VISIBLE else View.GONE
        findViewById<View>(R.id.guide_card).visibility = if (next == Page.PERMISSIONS) View.VISIBLE else View.GONE
        findViewById<View>(R.id.advanced_panel).visibility =
            if (next == Page.ADVANCED || next == Page.DIAGNOSTICS) View.VISIBLE else View.GONE
        findViewById<View>(R.id.advanced_tools).visibility = if (next == Page.ADVANCED) View.VISIBLE else View.GONE
        findViewById<View>(R.id.diagnostics_panel).visibility = if (next == Page.DIAGNOSTICS) View.VISIBLE else View.GONE
        supportActionBar?.setTitle(when (next) {
            Page.HOME -> R.string.app_name
            Page.PERMISSIONS -> R.string.menu_permissions
            Page.ADVANCED -> R.string.menu_advanced
            Page.DIAGNOSTICS -> R.string.menu_diagnostics
        })
        supportActionBar?.setDisplayHomeAsUpEnabled(next != Page.HOME)
        backToHome.isEnabled = next != Page.HOME
        findViewById<ScrollView>(R.id.main_scroll).scrollTo(0, 0)
    }

    // ── 权限与首页状态 ──────────────────────────────────────

    private fun refreshGuide() {
        val a11y = A11yBridge.isReady()
        val overlay = Settings.canDrawOverlays(this)
        val notif = notificationGranted()

        findViewById<TextView>(R.id.check_a11y).text =
            "${mark(a11y)} 无障碍（本机截图与任务输入必需）"
        findViewById<TextView>(R.id.check_pc_connection).text = if (AgentServer.isPcConnected()) {
            "✅ PC 已连接（${AgentServer.activeConnectionCount()}）"
        } else {
            "PC 未连接（已同步任务仍可离线执行）"
        }
        findViewById<TextView>(R.id.check_overlay).text =
            "${mark(overlay)} 悬浮窗（仅手机独立运行任务需要）"
        findViewById<TextView>(R.id.check_notification).text =
            "${mark(notif)} 通知（建议，缺了只是看不到运行状态通知）"

        findViewById<TextView>(R.id.home_permission_hint).text = when {
            !a11y -> "任务运行需要开启无障碍服务，可在权限设置中开启。"
            !overlay -> "启动悬浮图标需要悬浮窗权限，点击启动即可前往授权。"
            FloatService.isRunning -> "悬浮图标已启动，切到游戏后点击图标选择任务。"
            else -> "权限已就绪，启动悬浮图标即可在游戏中控制任务。"
        }
        findViewById<Button>(R.id.btn_home_permissions).visibility =
            if (!a11y || !overlay) View.VISIBLE else View.GONE

        // 悬浮启停合一按钮：文案随运行状态切换
        findViewById<Button>(R.id.btn_float_toggle).text = getString(
            if (FloatService.isRunning) R.string.btn_stop_float
            else R.string.btn_start_float
        )
    }

    /** 功能区悬浮图标启停：未运行时启动（需悬浮窗权限），运行中停止 */
    private fun onFloatToggle() {
        if (FloatService.isRunning) {
            executor.execute {
                PyBridge.stopTask(this)
                runOnUiThread { stopService(Intent(this, FloatService::class.java)) }
            }
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

    private fun notificationGranted(): Boolean =
        android.os.Build.VERSION.SDK_INT < 33 ||
            checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED

    private fun mark(ok: Boolean) = if (ok) "✅" else "⬜"

    /** ADB 自检使用同一诊断页，仍保留报告和点击回显控件。 */
    private fun enterSelfTestLayout() {
        ui.removeCallbacks(statusPoller)
        showPage(Page.DIAGNOSTICS)
    }

    // launchMode 是默认的 standard，重复 am start 通常会走 onCreate；
    // 但若系统复用了实例则只有这里会被调到，两处都接上才不会出现「命令没反应」。
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (isSelfTest()) {
            handleSelfTest(intent)
        } else {
            showPage(Page.HOME)
            refreshGuide()
            refreshStatus()
            ui.removeCallbacks(statusPoller)
            ui.postDelayed(statusPoller, STATUS_POLL_INTERVAL_MS)
        }
    }

    /** 响应 `--es selftest <target>`，在后台线程跑 Python 自检并把报告落盘 */
    private fun handleSelfTest(intent: Intent?) {
        if (intent?.getStringExtra("selftest") == null) return
        enterSelfTestLayout()
        val logFile = java.io.File(filesDir, SELFTEST_LOG)
        executor.execute {
            val report = try {
                PyBridge.ensureStarted(this)?.let { throw IllegalStateException(it) }
                val result = PyBridge.checkRuntime(this)
                val text = result.toString(2)
                logFile.writeText(text + "\n" + SELFTEST_END + "\n")
                text.lineSequence().forEach { Log.i(SELFTEST_TAG, it) }
                text
            } catch (e: Throwable) {
                // Python 侧自己会捕获各步异常；能走到这里说明连模块都没导入成功，
                // 这种情况 Python 一个字都写不出来，报告和哨兵都只能由这里补上
                val text = "自检未能启动：\n" + Log.getStackTraceString(e)
                runCatching { logFile.appendText(text + "\n" + SELFTEST_END + "\n") }
                text.lineSequence().forEach { Log.i(SELFTEST_TAG, it) }
                text
            }
            // 完成后输出结构化报告和结束标记，供 release APK 的 adb 验收读取。
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
        val a11y = if (A11yBridge.isReady()) "已开启" else "未开启（本机任务输入必需）"
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
        if (page == Page.ADVANCED) {
            statusText.text = "PC：$pc\n辅助：$a11y\n悬浮窗：$overlay\nShizuku：$shizuku（可选）"
        }
        executor.execute {
            val status = PyBridge.status(this)
            val sync = status.optJSONObject("sync")
            val text = if (sync?.optBoolean("synced") == true) {
                "最近同步：${sync.optString("synced_at")}\n执行用户：${sync.optString("execution_username", sync.optString("username"))}\n当前布局：${sync.optString("layout")}\n手机结果暂不回传，下次同步会覆盖手机 DB。"
            } else {
                status.optString("message").ifEmpty { "尚未从 PC 同步任务配置" }
            }
            ui.post { findViewById<TextView>(R.id.offline_status).text = text }
        }
    }

    private fun checkRuntime() {
        val button = findViewById<Button>(R.id.btn_runtime_check)
        val runtimeStatus = findViewById<TextView>(R.id.runtime_status)
        button.isEnabled = false
        runtimeStatus.text = "正在检查依赖、插件、引擎与 OCR…"
        executor.execute {
            val result = PyBridge.checkRuntime(this)
            runtimeReport = result.toString(2)
            ui.post {
                runtimeStatus.text = result.optString("message")
                button.isEnabled = true
                findViewById<Button>(R.id.btn_copy_report).isEnabled = true
            }
        }
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    companion object {
        private const val SELFTEST_TAG = "LvjiangSelfTest"

        /** 报告文件名：这台设备的 logcat 会滤掉普通应用的日志，文件才是可靠通道 */
        private const val SELFTEST_LOG = "selftest.log"

        /** 哨兵行：adb 侧靠它判断报告已输出完整，不必靠等固定秒数 */
        private const val SELFTEST_END = "=== SELFTEST END ==="
        private const val STATUS_POLL_INTERVAL_MS = 1000L
        private const val MENU_PERMISSIONS = 1
        private const val MENU_ADVANCED = 2
        private const val MENU_DIAGNOSTICS = 3
        private const val MENU_TASK_SETTINGS = 4
    }
}
