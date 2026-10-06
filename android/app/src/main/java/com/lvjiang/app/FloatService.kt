package com.lvjiang.app

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.IBinder
import android.text.Editable
import android.text.SpannableString
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.provider.Settings
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.ViewConfiguration
import android.view.WindowManager
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import java.util.ArrayDeque
import java.util.concurrent.Executors
import kotlin.math.abs

/**
 * FloatService — 悬浮小图标前台服务，同时是任务的唯一入口。
 *
 * 交互：
 * - 图标可拖动；单击展开任务面板，再单击收起；
 * - 面板空闲时选择任务后启动；
 * - 运行中可暂停/结束，暂停后可继续/结束，并显示最近日志；
 * - 图标颜色反映状态：空闲无色 / 运行中绿 / 失败红 / 已停止橙。
 *
 * 主面板只显示当前选择与增量日志；任务全集在二级选择面板中展示。
 */
class FloatService : Service() {

    private lateinit var windowManager: WindowManager
    private var floatView: ImageView? = null
    /** 悬浮图标的布局参数（拖动时就地更新 x/y），面板开展时据此贴近图标 */
    private var floatParams: WindowManager.LayoutParams? = null
    private var panelView: View? = null
    private var statusLine: TextView? = null
    private var userLine: TextView? = null
    private var taskLine: TextView? = null
    private var selectButton: Button? = null
    private var logLine: TextView? = null
    private var logScroll: ScrollView? = null
    private var latestButton: Button? = null
    private var actionArea: LinearLayout? = null
    private var mainArea: LinearLayout? = null
    private var selectionArea: LinearLayout? = null
    private var selectedTaskId = ""
    private var selectedTaskName = ""
    private var tasks: List<Pair<String, String>> = emptyList()
    private var synced = false
    private var syncStamp = ""
    private var statusPending = false
    private var followLogs = true
    private var lastLogSeq = 0L
    private var logGeneration = -1L
    private val logLengths = ArrayDeque<Int>()

    private val executor = Executors.newSingleThreadExecutor()
    private val ui by lazy { Handler(mainLooper) }

    /** 上一次拿到的状态，用于判断是否需要重建面板按钮区（空闲↔运行切换时才重建） */
    private var lastState = STATE_IDLE

    private val poller = object : Runnable {
        override fun run() {
            refreshStatus()
            // 同步或远程启动也会改变面板；悬浮服务存活时持续刷新。
            if (isRunning) ui.postDelayed(this, POLL_INTERVAL_MS)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        isRunning = true
        startForeground(NOTIFICATION_ID, buildNotification("悬浮控制运行中"))
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager
        addFloatIcon()
        // 解释器初始化是秒级的，放在这里预热，等用户点开面板时任务清单已经能秒出
        executor.execute { PyBridge.ensureStarted(this) }
        ui.post(poller)
    }

    override fun onDestroy() {
        instance = null
        isRunning = false
        ui.removeCallbacks(poller)
        closePanel()
        floatView?.let { runCatching { windowManager.removeView(it) } }
        floatView = null
        executor.execute { PyBridge.stopTask(this) }
        executor.shutdown()
        super.onDestroy()
    }

    // ── 悬浮图标 ──────────────────────────────────────────

    private fun addFloatIcon() {
        val iconSize = dp(42)
        val params = WindowManager.LayoutParams(
            iconSize, iconSize,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = dp(12)
            y = dp(120)
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
            private val touchSlop =
                ViewConfiguration.get(this@FloatService).scaledTouchSlop.toFloat()

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
                        if (moved || abs(dx) > touchSlop || abs(dy) > touchSlop) {
                            moved = true
                            val metrics = resources.displayMetrics
                            params.x = (startX + dx.toInt()).coerceIn(
                                0, maxOf(0, metrics.widthPixels - params.width),
                            )
                            params.y = (startY + dy.toInt()).coerceIn(
                                0, maxOf(0, metrics.heightPixels - params.height),
                            )
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
        floatParams = params
        applyIconVisibility()
    }

    private fun applyIconVisibility() {
        floatView?.visibility =
            if (iconHidden || pcConnected) View.INVISIBLE else View.VISIBLE
    }

    private fun tintIcon(state: String) {
        val color = when (state) {
            STATE_RUNNING -> COLOR_RUNNING
            STATE_PAUSING, STATE_PAUSED, STATE_STOPPING -> COLOR_STOPPED
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
        userLine = null
        taskLine = null
        selectButton = null
        logLine = null
        logScroll = null
        latestButton = null
        actionArea = null
        mainArea = null
        selectionArea = null
        lastLogSeq = 0
        logGeneration = -1
        logLengths.clear()
    }

    private fun openPanel() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(10), dp(8), dp(10), dp(8))
            background = GradientDrawable().apply {
                cornerRadius = dp(12).toFloat()
                setColor(0xF01E1E1E.toInt())
            }
        }

        val main = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(main, LinearLayout.LayoutParams(-1, 0, 1f))
        mainArea = main
        val actions = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        main.addView(actions)
        actionArea = actions
        val user = TextView(this).apply {
            setTextColor(0xFF90CAF9.toInt())
            text = "用户：读取中…"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            setPadding(0, dp(6), 0, dp(4))
        }
        main.addView(user)
        userLine = user
        val task = TextView(this).apply {
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
        }
        main.addView(task)
        taskLine = task
        val select = smallButton("选择任务") { openTaskSelection() }
        main.addView(select)
        selectButton = select
        val status = TextView(this).apply {
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            text = "读取状态…"
        }
        main.addView(status)
        statusLine = status

        val log = TextView(this).apply {
            setTextColor(0xFFAAAAAA.toInt())
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 10f)
            setText("", TextView.BufferType.EDITABLE)
            setPadding(0, dp(3), 0, dp(4))
        }
        val scroll = ScrollView(this).apply {
            addView(log)
            var downY = 0f
            setOnTouchListener { _, event ->
                if (event.actionMasked == MotionEvent.ACTION_DOWN) downY = event.y
                if (event.actionMasked == MotionEvent.ACTION_MOVE &&
                    abs(event.y - downY) > ViewConfiguration.get(this@FloatService).scaledTouchSlop) {
                    followLogs = false
                    latestButton?.visibility = View.VISIBLE
                }
                false
            }
        }
        main.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        logScroll = scroll
        logLine = log
        followLogs = true
        val latest = smallButton("回到最新") {
            followLogs = true
            latestButton?.visibility = View.GONE
            logScroll?.post { logScroll?.fullScroll(View.FOCUS_DOWN) }
        }.apply { visibility = View.GONE }
        main.addView(latest)
        latestButton = latest
        val selection = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
        }
        root.addView(selection, LinearLayout.LayoutParams(-1, 0, 1f))
        selectionArea = selection

        root.addView(smallButton("收起") { closePanel() })

        val panelW = minOf(dp(220), resources.displayMetrics.widthPixels - dp(58))
        val panelH = minOf(dp(260), usableHeight() - dp(24))
        val params = WindowManager.LayoutParams(
            panelW, panelH,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            // 不要 FLAG_NOT_TOUCHABLE：面板里的按钮要能点。
            // 也不加 FLAG_NOT_FOCUSABLE 之外的输入相关 flag，避免抢走游戏的触摸。
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            anchorPanelToIcon(this, panelW)
        }

        windowManager.addView(root, params)
        panelView = root

        // 面板刚建好时按钮区是空的，先按当前状态填一次，随后进入轮询
        lastState = ""
        refreshStatus()
    }

    /**
     * 把面板放到悬浮图标旁边（gravity 均为 TOP|START，故 x/y 直接用图标坐标系）。
     *
     * 横向：图标在左半屏→面板贴在图标右侧；在右半屏→贴左侧，避免越界。
     * 纵向：与图标顶端对齐向下展开；靠底时上提（面板高度 WRAP_CONTENT，用估计上限做 clamp）。
     * 拿不到图标参数时退回原来的固定位置。
     */
    private fun anchorPanelToIcon(params: WindowManager.LayoutParams, panelW: Int) {
        val icon = floatParams
        if (icon == null) {
            params.x = dp(16)
            params.y = dp(120)
            return
        }
        val metrics = resources.displayMetrics
        val screenW = metrics.widthPixels
        val screenH = usableHeight()
        val gap = dp(4)
        val panelHApprox = params.height

        val iconW = floatView?.width?.takeIf { it > 0 } ?: dp(42)
        val toRight = icon.x + iconW / 2 < screenW / 2
        val rawX = if (toRight) icon.x + iconW + gap else icon.x - panelW - gap
        params.x = rawX.coerceIn(gap, maxOf(gap, screenW - panelW - gap))
        params.y = icon.y.coerceIn(gap, maxOf(gap, screenH - panelHApprox))
    }

    private fun usableHeight(): Int {
        val bounds = Rect()
        floatView?.getWindowVisibleDisplayFrame(bounds)
        return bounds.height().takeIf { it > 0 } ?: resources.displayMetrics.heightPixels
    }

    /** 面板里所有按钮长一个样，抽出来省掉 5 处重复 */
    private fun smallButton(label: String, onClick: () -> Unit): Button =
        Button(this).apply {
            text = label
            isAllCaps = false
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            // Button 默认内边距很占高度，显式压到最小才能真正把行高砍下来；
            // minimumHeight 也一并调小，让行高由文字本身决定。
            minimumHeight = dp(15)
            minHeight = dp(15)
            setPadding(dp(12), dp(2), dp(12), dp(2))
            background = GradientDrawable().apply {
                cornerRadius = dp(6).toFloat()
                setColor(0xFF3A3A3A.toInt())
            }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = dp(3) }
            setOnClickListener { onClick() }
        }

    /** 拉一次 Python 侧状态并刷新面板 / 图标 / 通知 */
    private fun refreshStatus() {
        if (statusPending || !isRunning) return
        statusPending = true
        executor.execute {
            val status = PyBridge.status(this)
            val state = status.optString("state", STATE_IDLE)
            val message = status.optString("message", "")
            val taskName = status.optString("task_name", "")
            val elapsed = status.optDouble("elapsed", 0.0)
            ui.post {
                statusPending = false
                val header = when (state) {
                    STATE_RUNNING, STATE_PAUSING, STATE_PAUSED, STATE_STOPPING ->
                        "运行中（${"%.0f".format(elapsed)}s）" +
                            when (state) {
                                STATE_PAUSED -> "\n已暂停 · $message"
                                STATE_PAUSING -> "\n正在暂停 · $message"
                                STATE_STOPPING -> "\n正在结束…"
                                else -> "\n$message"
                            }
                    STATE_IDLE -> "空闲"
                    else -> message.ifEmpty { "空闲：选择一个任务开始" }
                }
                val sync = status.optJSONObject("sync")
                synced = sync?.optBoolean("synced") == true
                userLine?.text = if (synced) "用户：${sync?.optString("username")}" else "尚未从 PC 同步配置"
                statusLine?.text = header
                if (state in activeStates) {
                    selectedTaskId = status.optString("task_id")
                    selectedTaskName = taskName
                    closeTaskSelection()
                }
                taskLine?.text = "任务：${selectedTaskName.ifEmpty { "未选择" }}"
                selectButton?.isEnabled = synced && state !in activeStates
                appendLogs(status)
                tintIcon(state)
                updateNotification(
                    if (state in activeStates) "${if (state == STATE_PAUSED) "已暂停" else "运行中"}：$taskName" else header.replace('\n', ' ')
                )
                if (state != lastState) {
                    lastState = state
                    rebuildActions(state)
                }
                val stamp = sync?.optString("synced_at", "") ?: ""
                if (synced && stamp != syncStamp && state !in activeStates) {
                    syncStamp = stamp
                    loadTasks()
                }
            }
        }
    }

    private fun appendLogs(status: JSONObject) {
        val text = logLine?.text as? Editable ?: return
        val generation = status.optLong("log_generation")
        if (generation != logGeneration) {
            text.clear()
            logLengths.clear()
            lastLogSeq = 0
            logGeneration = generation
            followLogs = true
            latestButton?.visibility = View.GONE
        }
        val records = status.optJSONArray("log_records") ?: return
        var changed = false
        for (i in 0 until records.length()) {
            val record = records.optJSONObject(i) ?: continue
            val seq = record.optLong("seq")
            if (seq <= lastLogSeq) continue
            lastLogSeq = seq
            val level = record.optString("level", "INFO")
            if (level == "DEBUG" || level == "TRACE") continue
            val color = when (level) {
                "WARNING" -> 0xFFFFCC80.toInt()
                "ERROR", "CRITICAL" -> 0xFFFF8A80.toInt()
                else -> 0xFFCCCCCC.toInt()
            }
            for (content in record.optString("text").lines().takeLast(LOG_VISIBLE_LINES)) {
                val line = SpannableString(content.take(2000) + "\n")
                line.setSpan(ForegroundColorSpan(color), 0, line.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                text.append(line)
                logLengths.addLast(line.length)
            }
            changed = true
        }
        while (logLengths.size > LOG_VISIBLE_LINES || text.length > 32000) {
            val count = logLengths.removeFirst()
            val scroll = logScroll
            val oldHeight = logLine?.height ?: 0
            val oldY = scroll?.scrollY ?: 0
            text.delete(0, count)
            if (!followLogs) scroll?.post {
                scroll.scrollTo(0, maxOf(0, oldY - (oldHeight - (logLine?.height ?: 0))))
            }
        }
        if (changed && followLogs) logScroll?.post { logScroll?.fullScroll(View.FOCUS_DOWN) }
    }

    /** 顶部仅放启动或安全控制；任务全集仅在二级选择中展示。 */
    private fun rebuildActions(state: String) {
        val area = actionArea ?: return
        area.removeAllViews()

        if (state in activeStates) {
            if (state == STATE_RUNNING || state == STATE_PAUSED) {
                area.addView(smallButton(if (state == STATE_PAUSED) "继续任务" else "暂停任务") {
                    executor.execute {
                        val r = if (state == STATE_PAUSED) PyBridge.resumeTask(this) else PyBridge.pauseTask(this)
                        toast(r.optString("message"))
                        ui.post { refreshStatus() }
                    }
                })
            }
            area.addView(smallButton("结束任务") {
                executor.execute {
                    val r = PyBridge.stopTask(this)
                    toast(r.optString("message", "已请求停止"))
                    ui.post { refreshStatus() }
                }
            }.apply { isEnabled = state != STATE_STOPPING })
            return
        }

        area.addView(smallButton("启动任务") { launchTask(selectedTaskId, selectedTaskName) }
            .apply { isEnabled = synced && selectedTaskId.isNotEmpty() })
    }

    private fun loadTasks() {
        executor.execute {
            val result = PyBridge.listTasks(this)
            val tasks = result.optJSONArray("tasks")
            ui.post {
                if (!isRunning) return@post
                if (!result.optBoolean("ok", false) || tasks == null || tasks.length() == 0) {
                    this@FloatService.tasks = emptyList()
                    statusLine?.text = result.optString("error").ifEmpty { "没有可执行任务" }
                    selectedTaskId = ""
                    selectedTaskName = ""
                    taskLine?.text = "任务：未选择"
                    if (lastState !in activeStates) rebuildActions(lastState)
                    return@post
                }
                this@FloatService.tasks = (0 until tasks.length()).mapNotNull { i ->
                    val item = tasks.optJSONObject(i) ?: return@mapNotNull null
                    val id = item.optString("id")
                    val name = item.optString("name").ifEmpty { id }
                    id to name
                }
                if (lastState in activeStates) return@post
                val previous = selectedTaskId.ifEmpty { getSharedPreferences("float_tasks", MODE_PRIVATE).getString("selected", "") ?: "" }
                val selected = this@FloatService.tasks.firstOrNull { it.first == previous }
                selectedTaskId = selected?.first ?: ""
                selectedTaskName = selected?.second ?: ""
                taskLine?.text = "任务：${selectedTaskName.ifEmpty { "未选择" }}"
                rebuildActions(lastState)
                if (selectionArea?.visibility == View.VISIBLE) renderTaskSelection()
            }
        }
    }

    private fun openTaskSelection() {
        if (!synced || lastState in activeStates) return
        mainArea?.visibility = View.GONE
        selectionArea?.visibility = View.VISIBLE
        renderTaskSelection()
        loadTasks()
    }

    private fun renderTaskSelection() {
        val area = selectionArea ?: return
        area.removeAllViews()
        area.addView(TextView(this).apply { text = "选择任务"; setTextColor(Color.WHITE) })
        val choices = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        for ((id, name) in tasks) {
            choices.addView(smallButton(if (id == selectedTaskId) "✓ $name" else name) {
                if (lastState in activeStates) return@smallButton
                selectedTaskId = id
                selectedTaskName = name
                getSharedPreferences("float_tasks", MODE_PRIVATE).edit().putString("selected", id).apply()
                taskLine?.text = "任务：$name"
                closeTaskSelection()
                rebuildActions(lastState)
            })
        }
        area.addView(ScrollView(this).apply { addView(choices) }, LinearLayout.LayoutParams(-1, 0, 1f))
        area.addView(smallButton("返回") { closeTaskSelection() })
    }

    private fun closeTaskSelection() {
        selectionArea?.visibility = View.GONE
        mainArea?.visibility = View.VISIBLE
    }

    private fun launchTask(taskId: String, taskName: String) {
        // 手机独立任务固定使用无障碍截图与点击。在这里拦一次并直接把用户送到开关页，
        // 比让任务启动后再报一串截图失败更清楚；PC 代理另可使用 Shizuku 通道。
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
            ui.post {
                // 启动成功后收起，避免遮挡游戏；失败时保留面板供查看原因。
                if (ok) {
                    closePanel()
                    lastState = STATE_IDLE
                }
                refreshStatus()
            }
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

        @Volatile
        private var instance: FloatService? = null

        /** 悬浮图标当前是否被临时隐藏（截图/标定期间）。服务重建时据此保持隐藏态 */
        @Volatile
        var iconHidden = false
            private set

        /** 仅 PC 实际控制期间隐藏悬浮 UI；同步和状态连接不隐藏。 */
        @Volatile
        private var pcConnected = false

        /**
         * 动态显隐悬浮图标（主线程执行）。截图 / 屏幕标定前藏起来，否则图标会被截进画面、
         * 用户可能把它当成地标去点。返回悬浮服务是否在运行（没在跑本来就没图标，返回 false）。
         */
        fun setIconHidden(hidden: Boolean): Boolean {
            iconHidden = hidden
            val svc = instance ?: return false
            svc.ui.post {
                svc.applyIconVisibility()
                if (hidden) svc.closePanel()
            }
            return true
        }

        fun setPcConnected(connected: Boolean) {
            pcConnected = connected
            val svc = instance ?: return
            svc.ui.post {
                svc.applyIconVisibility()
                if (connected) svc.closePanel()
            }
        }

        private const val TAG = "FloatService"
        private const val NOTIFICATION_ID = 1
        /** 轮询间隔：任务是秒级节奏的，1s 足够跟上，也不至于把 Binder 打满 */
        private const val POLL_INTERVAL_MS = 1000L
        private const val LOG_VISIBLE_LINES = 20

        // 与 Python 侧 task_runner 的状态常量一一对应
        private const val STATE_IDLE = "idle"
        private const val STATE_RUNNING = "running"
        private const val STATE_PAUSING = "pausing"
        private const val STATE_PAUSED = "paused"
        private const val STATE_STOPPING = "stopping"
        private val activeStates = setOf(STATE_RUNNING, STATE_PAUSING, STATE_PAUSED, STATE_STOPPING)
        private const val STATE_DONE = "done"
        private const val STATE_FAILED = "failed"
        private const val STATE_STOPPED = "stopped"

        private const val COLOR_RUNNING = 0xFF4CAF50.toInt()
        private const val COLOR_FAILED = 0xFFF44336.toInt()
        private const val COLOR_STOPPED = 0xFFFF9800.toInt()
        private const val COLOR_DONE = 0xFF2196F3.toInt()
    }
}
