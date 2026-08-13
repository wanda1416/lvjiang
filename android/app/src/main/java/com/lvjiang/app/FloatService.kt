package com.lvjiang.app

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.IBinder
import android.provider.Settings
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import java.util.concurrent.Executors
import kotlin.math.abs

/**
 * FloatService — 悬浮小图标前台服务，同时是任务的唯一入口。
 *
 * 交互：
 * - 图标可拖动；单击展开任务面板，再单击收起；
 * - 面板空闲时列出全部可执行任务，点一项即开跑；
 * - 运行中面板只给「停止」，并实时显示状态行与最近日志；
 * - 图标颜色反映状态：空闲无色 / 运行中绿 / 失败红 / 已停止橙。
 *
 * 面板用代码搭而不是 XML：它只有「一行状态 + 一列按钮」，
 * 而条目是运行时才知道的（任务清单来自 Python 侧发现层），
 * XML 布局在这里省不掉任何 findViewById 之外的工作。
 */
class FloatService : Service() {

    private lateinit var windowManager: WindowManager
    private var floatView: ImageView? = null
    private var panelView: View? = null
    private var statusLine: TextView? = null
    private var logLine: TextView? = null
    private var actionArea: LinearLayout? = null

    private val executor = Executors.newSingleThreadExecutor()
    private val ui by lazy { Handler(mainLooper) }

    /** 上一次拿到的状态，用于判断是否需要重建面板按钮区（空闲↔运行切换时才重建） */
    private var lastState = STATE_IDLE

    private val poller = object : Runnable {
        override fun run() {
            refreshStatus()
            // 只在运行中持续轮询：终态之后状态不会自己变，再轮下去纯属耗电
            if (lastState == STATE_RUNNING) ui.postDelayed(this, POLL_INTERVAL_MS)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        isRunning = true
        startForeground(NOTIFICATION_ID, buildNotification("悬浮控制运行中"))
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager
        addFloatIcon()
        // 解释器初始化是秒级的，放在这里预热，等用户点开面板时任务清单已经能秒出
        executor.execute { PyBridge.ensureStarted(this) }
    }

    override fun onDestroy() {
        isRunning = false
        ui.removeCallbacks(poller)
        closePanel()
        floatView?.let { runCatching { windowManager.removeView(it) } }
        floatView = null
        executor.shutdown()
        super.onDestroy()
    }

    // ── 悬浮图标 ──────────────────────────────────────────

    private fun addFloatIcon() {
        val params = WindowManager.LayoutParams(
            ICON_SIZE_PX, ICON_SIZE_PX,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = 40
            y = 400
        }

        val icon = ImageView(this).apply {
            setImageResource(R.drawable.ic_float)
            alpha = 0.85f
        }

        // 拖动 + 单击判定（位移阈值区分）
        icon.setOnTouchListener(object : View.OnTouchListener {
            private var downX = 0f
            private var downY = 0f
            private var startX = 0
            private var startY = 0
            private var moved = false

            override fun onTouch(v: View, event: MotionEvent): Boolean {
                when (event.action) {
                    MotionEvent.ACTION_DOWN -> {
                        downX = event.rawX
                        downY = event.rawY
                        startX = params.x
                        startY = params.y
                        moved = false
                    }
                    MotionEvent.ACTION_MOVE -> {
                        val dx = event.rawX - downX
                        val dy = event.rawY - downY
                        if (moved || abs(dx) > TOUCH_SLOP || abs(dy) > TOUCH_SLOP) {
                            moved = true
                            params.x = startX + dx.toInt()
                            params.y = startY + dy.toInt()
                            windowManager.updateViewLayout(v, params)
                        }
                    }
                    MotionEvent.ACTION_UP -> {
                        if (!moved) togglePanel()
                    }
                }
                return true
            }
        })

        windowManager.addView(icon, params)
        floatView = icon
    }

    private fun tintIcon(state: String) {
        val color = when (state) {
            STATE_RUNNING -> COLOR_RUNNING
            STATE_FAILED -> COLOR_FAILED
            STATE_STOPPED -> COLOR_STOPPED
            STATE_DONE -> COLOR_DONE
            else -> 0
        }
        floatView?.let {
            if (color == 0) it.clearColorFilter() else it.setColorFilter(color)
            it.alpha = if (state == STATE_RUNNING) 1.0f else 0.85f
        }
    }

    // ── 任务面板 ──────────────────────────────────────────

    private fun togglePanel() {
        if (panelView != null) closePanel() else openPanel()
    }

    private fun closePanel() {
        panelView?.let { runCatching { windowManager.removeView(it) } }
        panelView = null
        statusLine = null
        logLine = null
        actionArea = null
    }

    private fun openPanel() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(12), dp(14), dp(12))
            background = GradientDrawable().apply {
                cornerRadius = dp(12).toFloat()
                setColor(0xF01E1E1E.toInt())
            }
        }

        val status = TextView(this).apply {
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
            text = "读取状态…"
        }
        root.addView(status)
        statusLine = status

        val log = TextView(this).apply {
            setTextColor(0xFFAAAAAA.toInt())
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            maxLines = LOG_TAIL_LINES
            setPadding(0, dp(4), 0, dp(6))
        }
        root.addView(log)
        logLine = log

        val actions = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT,
            )
        }
        root.addView(
            ScrollView(this).apply {
                addView(actions)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, dp(260),
                )
            }
        )
        actionArea = actions

        root.addView(smallButton("收起") { closePanel() })

        val params = WindowManager.LayoutParams(
            dp(240), WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            // 不要 FLAG_NOT_TOUCHABLE：面板里的按钮要能点。
            // 也不加 FLAG_NOT_FOCUSABLE 之外的输入相关 flag，避免抢走游戏的触摸。
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = dp(16)
            y = dp(120)
        }

        windowManager.addView(root, params)
        panelView = root

        // 面板刚建好时按钮区是空的，先按当前状态填一次，随后进入轮询
        lastState = ""
        refreshStatus()
    }

    /** 面板里所有按钮长一个样，抽出来省掉 5 处重复 */
    private fun smallButton(label: String, onClick: () -> Unit): Button =
        Button(this).apply {
            text = label
            isAllCaps = false
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
            minimumHeight = dp(38)
            background = GradientDrawable().apply {
                cornerRadius = dp(6).toFloat()
                setColor(0xFF3A3A3A.toInt())
            }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = dp(6) }
            setOnClickListener { onClick() }
        }

    /** 拉一次 Python 侧状态并刷新面板 / 图标 / 通知 */
    private fun refreshStatus() {
        executor.execute {
            val status = PyBridge.status(this)
            val state = status.optString("state", STATE_IDLE)
            val message = status.optString("message", "")
            val taskName = status.optString("task_name", "")
            val elapsed = status.optDouble("elapsed", 0.0)
            val stopping = status.optBoolean("stopping", false)
            val logs = status.optJSONArray("logs")
            val tail = buildString {
                if (logs != null) {
                    val from = maxOf(0, logs.length() - LOG_TAIL_LINES)
                    for (i in from until logs.length()) {
                        if (isNotEmpty()) append('\n')
                        append(logs.optString(i))
                    }
                }
            }

            ui.post {
                val header = when (state) {
                    STATE_RUNNING ->
                        "运行中：$taskName（${"%.0f".format(elapsed)}s）" +
                            if (stopping) "\n停止中…" else "\n$message"
                    STATE_IDLE -> "空闲：选择一个任务开始"
                    else -> message.ifEmpty { "空闲：选择一个任务开始" }
                }
                statusLine?.text = header
                logLine?.text = tail
                tintIcon(state)
                updateNotification(
                    if (state == STATE_RUNNING) "运行中：$taskName" else header.replace('\n', ' ')
                )
                if (state != lastState) {
                    lastState = state
                    rebuildActions(state)
                    if (state == STATE_RUNNING) {
                        ui.removeCallbacks(poller)
                        ui.postDelayed(poller, POLL_INTERVAL_MS)
                    }
                }
            }
        }
    }

    /** 空闲态填任务清单，运行态只留「停止」 */
    private fun rebuildActions(state: String) {
        val area = actionArea ?: return
        area.removeAllViews()

        if (state == STATE_RUNNING) {
            area.addView(smallButton("停止任务") {
                executor.execute {
                    val r = PyBridge.stopTask(this)
                    toast(r.optString("message", "已请求停止"))
                    ui.post { refreshStatus() }
                }
            })
            return
        }

        val loading = TextView(this).apply {
            setTextColor(0xFFAAAAAA.toInt())
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            text = "加载任务清单…"
        }
        area.addView(loading)

        executor.execute {
            val result = PyBridge.listTasks(this)
            val tasks = result.optJSONArray("tasks")
            ui.post {
                area.removeAllViews()
                if (!result.optBoolean("ok", false) || tasks == null || tasks.length() == 0) {
                    area.addView(TextView(this).apply {
                        setTextColor(0xFFFF8A80.toInt())
                        setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
                        text = result.optString("error").ifEmpty { "没有可执行任务" }
                    })
                    return@post
                }
                for (i in 0 until tasks.length()) {
                    val item = tasks.optJSONObject(i) ?: continue
                    val id = item.optString("id")
                    val name = item.optString("name").ifEmpty { id }
                    area.addView(smallButton(name) { launchTask(id, name) })
                }
            }
        }
    }

    private fun launchTask(taskId: String, taskName: String) {
        // 无障碍是截图与点击的唯一主通道，被系统关掉后所有动作都会静默失败。
        // 在这里拦一次并直接把用户送到开关页，比让任务跑起来再报一堆截图失败清楚得多。
        if (!A11yBridge.isReady()) {
            toast("无障碍服务未开启，正在打开设置页")
            openAccessibilitySettings()
            return
        }
        executor.execute {
            val r = PyBridge.startTask(this, taskId)
            val ok = r.optBoolean("ok", false)
            toast(r.optString("message", if (ok) "已启动：$taskName" else "启动失败"))
            report("task start $taskId -> ok=$ok ${r.optString("message")}")
            ui.post { refreshStatus() }
        }
    }

    private fun openAccessibilitySettings() {
        runCatching {
            startActivity(
                Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        }.onFailure { Log.w(TAG, "打开无障碍设置失败", it) }
    }

    // ── 通知与上报 ────────────────────────────────────────

    private fun buildNotification(text: String): Notification {
        val pi = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, App.CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_float)
            .setContentTitle("律匠")
            .setContentText(text)
            .setContentIntent(pi)
            .setOnlyAlertOnce(true)
            .setOngoing(true)
            .build()
    }

    private fun updateNotification(text: String) {
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        runCatching { nm.notify(NOTIFICATION_ID, buildNotification(text)) }
    }

    /**
     * 自验上报：vivo 默认过滤三方应用 info 级日志，故用 Log.e 保证 logcat 可见；
     * 同时落盘 filesDir/status.txt，adb run-as 可读，作为后续阶段的稳定自验通道。
     */
    private fun report(msg: String) {
        Log.e(TAG, msg)
        runCatching { java.io.File(filesDir, "status.txt").writeText(msg) }
    }

    private fun toast(msg: String) {
        ui.post { Toast.makeText(this, msg, Toast.LENGTH_SHORT).show() }
    }

    private fun dp(value: Int): Int =
        (value * resources.displayMetrics.density).toInt()

    companion object {
        /** 悬浮图标是否在运行：引导页据此决定主按钮是「启动」还是「就绪」。
         *  服务与 Activity 同进程，静态标志足够，不值得上 dumpsys/绑定查询 */
        @Volatile
        var isRunning = false
            private set

        private const val TAG = "FloatService"
        private const val NOTIFICATION_ID = 1
        private const val ICON_SIZE_PX = 140
        private const val TOUCH_SLOP = 12f

        /** 轮询间隔：任务是秒级节奏的，1s 足够跟上，也不至于把 Binder 打满 */
        private const val POLL_INTERVAL_MS = 1000L
        private const val LOG_TAIL_LINES = 4

        // 与 Python 侧 task_runner 的状态常量一一对应
        private const val STATE_IDLE = "idle"
        private const val STATE_RUNNING = "running"
        private const val STATE_DONE = "done"
        private const val STATE_FAILED = "failed"
        private const val STATE_STOPPED = "stopped"

        private const val COLOR_RUNNING = 0xFF4CAF50.toInt()
        private const val COLOR_FAILED = 0xFFF44336.toInt()
        private const val COLOR_STOPPED = 0xFFFF9800.toInt()
        private const val COLOR_DONE = 0xFF2196F3.toInt()
    }
}
