package com.lvjiang.app

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.InsetDrawable
import android.os.Handler
import android.os.Looper
import android.os.IBinder
import android.os.SystemClock
import android.text.Editable
import android.text.SpannableString
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.provider.Settings
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.HapticFeedbackConstants
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
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit
import kotlin.math.abs

/**
 * FloatService — 悬浮小图标前台服务，同时是任务的唯一入口。
 *
 * 交互：
 * - 图标是「播放/暂停」双态按钮：未准备任务时保持 logo，点击只展开面板；有任务后
 *   显示 ▶，点击直接启动（暂停后为继续）；运行中显示 ⏸，点击直接暂停；
 * - 长按后原地松手才展开面板，一直按住不放不触发；拖动只移动位置，不受长按时长限制；
 * - 面板空闲时选择任务后启动；
 * - 运行中可暂停/结束，暂停后可继续/结束，并显示最近日志；
 * - 图标颜色反映状态：运行中绿 / 失败红 / 已停止橙 / 完成蓝。
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
    /** 选择用户按钮；当前选择展示在右侧的 userLine 文本上。 */
    private var userButton: Button? = null
    /** 选择任务按钮；当前选择展示在右侧的 taskLine 文本上。 */
    private var taskButton: Button? = null
    /** 当前用户值（选择用户按钮右侧的纯文本）。 */
    private var userLine: TextView? = null
    /** 当前任务值（选择任务按钮右侧的纯文本）。 */
    private var taskLine: TextView? = null
    private var showingUsers = false
    private var logLine: TextView? = null
    private var logScroll: ScrollView? = null
    private var latestButton: Button? = null
    private var actionArea: LinearLayout? = null
    private var mainArea: LinearLayout? = null
    private var selectionArea: LinearLayout? = null
    private var selectedTaskId = ""
    private var selectedTaskName = ""
    private var preparedBatchGroup = ""
    private var preparedBatchToken = ""
    private var tasks: List<Pair<String, String>> = emptyList()
    private var ready = false
    private var syncStamp = ""
    private var statusPending = false
    private var followLogs = true
    private var lastLogSeq = 0L
    private var logGeneration = -1L
    private val logLengths = ArrayDeque<Int>()

    private val executor = Executors.newSingleThreadExecutor()
    private val ui by lazy { Handler(mainLooper) }

    /** 上一次拿到的状态，用于判断是否需要重建面板按钮区（空闲↔运行切换时才重建） */
    private var interactionId = ""
    private var lastState = STATE_IDLE

    /** 图标单击触发启动/暂停的时间戳，用于节流——轮询最长 1s 才确认，不节流会被连点两次 */
    private var lastIconActionAt = 0L

    private val poller = object : Runnable {
        override fun run() {
            refreshStatus()
            // 同步或远程启动也会改变面板；悬浮服务存活时持续刷新。
            if (isRunning) ui.postDelayed(this, POLL_INTERVAL_MS)
        }
    }

    override fun onConfigurationChanged(newConfig: android.content.res.Configuration) {
        super.onConfigurationChanged(newConfig)
        ui.post {
            val (w, h) = ScreenMap.screenSize()
            floatParams?.let { params ->
                params.x = params.x.coerceIn(0, maxOf(0, w - params.width))
                params.y = params.y.coerceIn(0, maxOf(0, h - params.height))
                floatView?.let { windowManager.updateViewLayout(it, params) }
            }
            if (panelView != null) {
                closePanel()
                openPanel()
            }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager
        try {
            startForeground(NOTIFICATION_ID, buildNotification("悬浮控制运行中"))
            addFloatIcon()
            isRunning = true
            finishStart(null)
        } catch (e: Exception) {
            Log.e(TAG, "悬浮控制初始化失败", e)
            finishStart("悬浮控制初始化失败：${e.message}")
            stopSelf()
            return
        }
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

        // 服务刚起来时先按上次选择恢复，图标第一帧就能判断「点下去会不会直接开跑」，
        // 不必等首次同步完成的 loadTasks。
        getSharedPreferences("float_tasks", MODE_PRIVATE).getString("selected", "").orEmpty().let {
            if (it.isNotEmpty()) {
                selectedTaskId = it
                selectedTaskName = getSharedPreferences("float_tasks", MODE_PRIVATE)
                    .getString("selected_name", "").orEmpty()
            }
        }

        val icon = ImageView(this).apply {
            setImageResource(R.drawable.ic_float)
            alpha = 0.85f
        }

        // 手势只有三种去向，且都看「抬起时」的状态，不看计时器什么时候响：
        // 移动过 = 拖动图标；没移动且按住够久 = 展开面板；没移动且短按 = 执行图标动作。
        // 拖动不受长按时长限制，长按后继续拖动也仍然是拖动。
        icon.setOnTouchListener(object : View.OnTouchListener {
            private var downX = 0f
            private var downY = 0f
            private var startX = 0
            private var startY = 0

            /** 已按拖动阈值移动过：本次手势是拖动，抬起时不再触发任何动作 */
            private var moved = false

            /** 长按计时器已到点：抬起时展开面板，只要一直按住就什么都不会发生 */
            private var armed = false
            private val touchSlop =
                ViewConfiguration.get(this@FloatService).scaledTouchSlop.toFloat()

            private val arm = Runnable {
                if (moved) return@Runnable
                armed = true
                // 悬浮窗不可聚焦，没有别的反馈渠道，触感是唯一的「松手会打开面板」提示
                runCatching {
                    icon.performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
                }
            }

            override fun onTouch(v: View, event: MotionEvent): Boolean {
                when (event.actionMasked) {
                    MotionEvent.ACTION_DOWN -> {
                        downX = event.rawX
                        downY = event.rawY
                        startX = params.x
                        startY = params.y
                        moved = false
                        armed = false
                        ui.postDelayed(arm, ViewConfiguration.getLongPressTimeout().toLong())
                    }
                    MotionEvent.ACTION_MOVE -> {
                        val dx = event.rawX - downX
                        val dy = event.rawY - downY
                        if (!moved && (abs(dx) > touchSlop || abs(dy) > touchSlop)) {
                            // 一旦移动过就锁成拖动，之后回到原位也不算点击或长按
                            moved = true
                        }
                        if (moved) {
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
                    MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                        ui.removeCallbacks(arm)
                        val released = event.actionMasked == MotionEvent.ACTION_UP
                        val wasArmed = armed
                        val dragged = moved
                        moved = false
                        armed = false
                        when {
                            !released -> Unit          // 手势被系统打断，不派发动作
                            dragged -> Unit            // 拖动只改位置
                            wasArmed -> togglePanel()  // 长按后原地松手才展开面板
                            else -> onIconTap()        // 短按保持原有语义
                        }
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

    /**
     * 图标三态：没有可执行任务时保持原 logo（点击只展开面板），有任务时 ▶（点击启动/
     * 继续），运行中 ⏸（点击暂停）。圆底色继续反映运行状态，符号只表达「点下去会发生
     * 什么」——所以两个层必须分开上色，不能再用 setColorFilter 把符号一起染掉。
     */
    private fun applyIconState(state: String) {
        val icon = floatView ?: return
        val actionable = state in activeStates || (ready && selectedTaskId.isNotEmpty())
        val glyph = when {
            !actionable -> 0
            state == STATE_RUNNING || state == STATE_PAUSING || state == STATE_STOPPING ->
                R.drawable.ic_float_glyph_pause
            else -> R.drawable.ic_float_glyph_play
        }
        val color = when {
            !actionable -> 0
            state == STATE_RUNNING -> COLOR_RUNNING
            state == STATE_FAILED -> COLOR_FAILED
            state == STATE_DONE -> COLOR_DONE
            state == STATE_PAUSING || state == STATE_PAUSED ||
                state == STATE_STOPPING || state == STATE_STOPPED -> COLOR_STOPPED
            else -> COLOR_IDLE
        }
        if (glyph == 0) {
            icon.background = null
            icon.setImageResource(R.drawable.ic_float)
        } else {
            // 圆底用代码画，2dp 内缩让外径与原 logo 的 44/48 保持一致
            icon.background = InsetDrawable(
                GradientDrawable().apply {
                    shape = GradientDrawable.OVAL
                    setColor(color)
                },
                dp(2),
            )
            icon.setImageResource(glyph)
        }
        icon.alpha = if (state == STATE_RUNNING) 1.0f else 0.85f
    }

    // ── 任务面板 ──────────────────────────────────────────

    private fun togglePanel() {
        if (panelView != null) closePanel() else openPanel()
    }

    /**
     * 图标单击：面板开着时先收起（沿用原来的开关语义，避免在面板上用单击误启动任务），
     * 否则按图标当前符号直接执行；没有可执行任务时退回展开面板，不静默失败。
     */
    private fun onIconTap() {
        if (panelView != null) {
            closePanel()
            return
        }
        val state = lastState
        // 过渡态不重复发请求，否则只会拿到一串「正在暂停/正在停止」的回包
        if (state == STATE_PAUSING || state == STATE_STOPPING) return
        // 等待人工确认时不能拿「暂停」代替回答，把决策交回面板
        if (state in activeStates && interactionId.isNotEmpty()) {
            openPanel()
            return
        }
        if (!ready) {
            openPanel()
            toast("预置配置未就绪，请稍后重试")
            return
        }
        if (selectedTaskId.isEmpty()) {
            openPanel()
            toast("请先选择任务")
            return
        }
        val now = SystemClock.elapsedRealtime()
        if (now - lastIconActionAt < ICON_TAP_THROTTLE_MS) return
        lastIconActionAt = now
        when (state) {
            STATE_RUNNING -> pauseResume(pause = true)
            STATE_PAUSED -> pauseResume(pause = false)
            else -> launchTask(selectedTaskId, selectedTaskName)
        }
    }

    /** 暂停/继续：先乐观切换图标，避免 1s 轮询窗口内看起来没反应而被连点 */
    private fun pauseResume(pause: Boolean) {
        applyIconState(if (pause) STATE_PAUSED else STATE_RUNNING)
        executor.execute {
            val r = if (pause) PyBridge.pauseTask(this) else PyBridge.resumeTask(this)
            toast(r.optString("message"))
            ui.post { refreshStatus() }
        }
    }

    private fun closePanel() {
        panelView?.let { runCatching { windowManager.removeView(it) } }
        panelView = null
        statusLine = null
        userButton = null
        taskButton = null
        userLine = null
        taskLine = null
        showingUsers = false
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
        val userRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        val user = smallButton("选择用户") { openUserSelection() }
        userRow.addView(user, LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = dp(4) })
        userButton = user
        val userValue = TextView(this).apply {
            setTextColor(0xFF90CAF9.toInt())
            text = "读取中…"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
            gravity = Gravity.CENTER_VERTICAL
        }
        userRow.addView(userValue, LinearLayout.LayoutParams(0, -2, 1f))
        userLine = userValue
        main.addView(userRow)
        val taskRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        val task = smallButton("选择任务") { openTaskSelection() }
        taskRow.addView(task, LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = dp(4) })
        taskButton = task
        val taskValue = TextView(this).apply {
            setTextColor(Color.WHITE)
            text = "未选择"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
            gravity = Gravity.CENTER_VERTICAL
        }
        taskRow.addView(taskValue, LinearLayout.LayoutParams(0, -2, 1f))
        taskLine = taskValue
        main.addView(taskRow)
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
                val stateTitle = when (state) {
                    STATE_PAUSED -> "已暂停"
                    STATE_PAUSING -> "正在暂停"
                    STATE_STOPPING -> "正在停止"
                    else -> "运行中"
                }
                val detail = message.trim().takeUnless {
                    it.isEmpty() || it == stateTitle || state == STATE_RUNNING && it in setOf("执行中", "运行中")
                }
                val header = when (state) {
                    STATE_RUNNING, STATE_PAUSING, STATE_PAUSED, STATE_STOPPING ->
                        "$stateTitle（${"%.0f".format(elapsed)}s）" + (detail?.let { "\n$it" } ?: "")
                    STATE_IDLE -> "空闲"
                    else -> message.ifEmpty { "空闲：选择一个任务开始" }
                }
                val sync = status.optJSONObject("sync")
                ready = sync?.optBoolean("ready") == true
                userLine?.text = if (ready) SettingsUi.userLabel(sync?.optString("execution_username").orEmpty()) else "预置配置未就绪"
                statusLine?.text = header
                if (state in activeStates) {
                    selectedTaskId = status.optString("task_id")
                    selectedTaskName = taskName
                    closeTaskSelection()
                }
                taskLine?.text = selectedTaskName.ifEmpty { "未选择" }
                if (status.optJSONObject("batch")?.optString("group_id").orEmpty().isNotEmpty() && state in activeStates) {
                    userLine?.text = "单元：${status.optJSONObject("batch")?.optString("current_entry").orEmpty()}"
                }
                userButton?.isEnabled = ready && state !in activeStates
                taskButton?.isEnabled = ready && state !in activeStates
                appendLogs(status)
                applyIconState(state)
                updateNotification(
                    if (state in activeStates) "${if (state == STATE_PAUSED) "已暂停" else "运行中"}：$taskName" else header.replace('\n', ' ')
                )
                val nextInteraction = status.optJSONObject("interaction")?.optString("id").orEmpty()
                if (state != lastState || nextInteraction != interactionId) {
                    interactionId = nextInteraction
                    lastState = state
                    rebuildActions(state)
                }
                val stamp = sync?.optString("synced_at", "preset") ?: ""
                if (ready && stamp != syncStamp && state !in activeStates) {
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
        while (logLengths.size > LOG_VISIBLE_LINES) {
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
            val subject = if (selectedTaskId.startsWith("batch:")) "批量" else "任务"
            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            row.addView(smallButton("结束$subject") {
                executor.execute {
                    val r = PyBridge.stopTask(this)
                    toast(r.optString("message", "已请求停止"))
                    ui.post { refreshStatus() }
                }
            }.apply { isEnabled = state != STATE_STOPPING },
                LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = dp(4) })
            if (interactionId.isNotEmpty() && state != STATE_STOPPING) {
                val requestId = interactionId
                for ((label, accepted) in listOf("确认" to true, "拒绝" to false)) {
                    row.addView(smallButton(label) {
                        executor.execute {
                            val result = PyBridge.respondInteraction(this, requestId, accepted)
                            toast(result.optString("message"))
                            ui.post { refreshStatus() }
                        }
                    }, LinearLayout.LayoutParams(0, -2, 1f))
                }
                area.addView(row)
                return
            }
            row.addView(smallButton(if (state == STATE_PAUSED) "继续$subject" else "暂停$subject") {
                    executor.execute {
                        val r = if (state == STATE_PAUSED) PyBridge.resumeTask(this) else PyBridge.pauseTask(this)
                        toast(r.optString("message"))
                        ui.post { refreshStatus() }
                    }
                }.apply { isEnabled = state == STATE_RUNNING || state == STATE_PAUSED },
                LinearLayout.LayoutParams(0, -2, 1f))
            area.addView(row)
            return
        }

        val idle = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        idle.addView(
            smallButton(if (selectedTaskId.startsWith("batch:")) "启动批量" else "启动任务") {
                launchTask(selectedTaskId, selectedTaskName)
            }.apply { isEnabled = ready && selectedTaskId.isNotEmpty() },
            LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = dp(4) })
        idle.addView(
            smallButton("关闭悬浮窗") { closeOverlay() },
            LinearLayout.LayoutParams(0, -2, 1f))
        area.addView(idle)
    }

    /** 关闭当前面板与悬浮图标，并结束前台服务。 */
    private fun closeOverlay() {
        closePanel()
        floatView?.let { runCatching { windowManager.removeView(it) } }
        floatView = null
        stopSelf()
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
                    taskLine?.text = "未选择"
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
                if (preparedBatchGroup.isNotEmpty()) return@post
                val previous = selectedTaskId.ifEmpty { getSharedPreferences("float_tasks", MODE_PRIVATE).getString("selected", "") ?: "" }
                val selected = this@FloatService.tasks.firstOrNull { it.first == previous }
                selectedTaskId = selected?.first ?: ""
                selectedTaskName = selected?.second ?: ""
                getSharedPreferences("float_tasks", MODE_PRIVATE).edit()
                    .putString("selected_name", selectedTaskName).apply()
                taskLine?.text = selectedTaskName.ifEmpty { "未选择" }
                rebuildActions(lastState)
                if (selectionArea?.visibility == View.VISIBLE && !showingUsers) renderTaskSelection()
            }
        }
    }

    private fun openTaskSelection() {
        if (!ready || lastState in activeStates) return
        showingUsers = false
        mainArea?.visibility = View.GONE
        selectionArea?.visibility = View.VISIBLE
        renderTaskSelection()
        loadTasks()
    }

    private fun openUserSelection() {
        if (!ready || lastState in activeStates) return
        showingUsers = true
        mainArea?.visibility = View.GONE
        selectionArea?.visibility = View.VISIBLE
        selectionArea?.removeAllViews()
        selectionArea?.addView(TextView(this).apply { text = "读取用户…"; setTextColor(Color.WHITE) })
        selectionArea?.addView(smallButton("返回") { closeTaskSelection() })
        executor.execute {
            val result = PyBridge.listUsers(this)
            ui.post {
                if (!isRunning || !showingUsers || selectionArea?.visibility != View.VISIBLE) return@post
                if (lastState in activeStates) { closeTaskSelection(); return@post }
                val area = selectionArea ?: return@post
                area.removeAllViews()
                val users = result.optJSONArray("users")
                val selected = result.optString("selected")
                area.addView(TextView(this).apply {
                    text = if (result.optBoolean("ok") && users != null && users.length() > 0)
                        "选择执行用户（不切换游戏角色）" else result.optString("message", "没有可用用户")
                    setTextColor(Color.WHITE)
                })
                val choices = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
                if (users != null) for (index in 0 until users.length()) {
                    val username = users.optString(index)
                    val label = SettingsUi.userLabel(username)
                    choices.addView(smallButton(if (username == selected) "✓ $label" else label) {
                        if (lastState in activeStates) return@smallButton
                        choices.isEnabled = false
                        for (child in 0 until choices.childCount) choices.getChildAt(child).isEnabled = false
                        executor.execute {
                            val response = PyBridge.selectUser(this, username)
                            toast(response.optString("message"))
                            ui.post {
                                if (response.optBoolean("ok")) closeTaskSelection()
                                else for (child in 0 until choices.childCount) choices.getChildAt(child).isEnabled = true
                                refreshStatus()
                            }
                        }
                    })
                }
                area.addView(ScrollView(this).apply { addView(choices) }, LinearLayout.LayoutParams(-1, 0, 1f))
                area.addView(smallButton("返回") { closeTaskSelection() })
            }
        }
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
                preparedBatchGroup = ""; preparedBatchToken = ""
                getSharedPreferences("float_tasks", MODE_PRIVATE).edit().putString("selected", id).putString("selected_name", name).apply()
                taskLine?.text = name
                closeTaskSelection()
                rebuildActions(lastState)
            })
        }
        area.addView(ScrollView(this).apply { addView(choices) }, LinearLayout.LayoutParams(-1, 0, 1f))
        area.addView(smallButton("设置任务参数") {
            startActivity(Intent(this, TaskSettingsActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                putExtra("task_id", selectedTaskId)
            })
            closePanel()
        })
        area.addView(smallButton("返回") { closeTaskSelection() })
    }

    private fun closeTaskSelection() {
        showingUsers = false
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
            val r = if (taskId.startsWith("batch:")) PyBridge.startBatch(this, preparedBatchGroup, preparedBatchToken) else PyBridge.startTask(this, taskId)
            val ok = r.optBoolean("ok", false)
            toast(r.optString("message", if (ok) "已启动：$taskName" else "启动失败"))
            report("task start $taskId -> ok=$ok ${r.optString("message")}")
            ui.post {
                // 启动成功后收起，避免遮挡游戏；失败时保留面板供查看原因。
                if (ok) {
                    closePanel()
                    lastState = STATE_IDLE
                    // 立刻切成暂停符号，别让用户在轮询确认前以为没点上
                    applyIconState(STATE_RUNNING)
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
        fun selectedTaskLabel(context: Context): String = instance?.selectedTaskName
            ?: context.getSharedPreferences("float_tasks", Context.MODE_PRIVATE).getString("selected_name", "").orEmpty()

        private var starting: CompletableFuture<String?>? = null
        private var startupIcon: View? = null
        private var startupWindowManager: WindowManager? = null

        /** 只准备入口，用户回到游戏后仍须显式点击启动。 */
        fun prepareBatch(context: Context, groupId: String): JSONObject = synchronized(AgentServer.executionLock) {
            if (AgentServer.pcControlsDevice() || PyBridge.status(context).optBoolean("busy")) {
                return@synchronized JSONObject().put("ok", false).put("message", "已有任务在执行，请先结束")
            }
            val result = PyBridge.prepareBatch(context, groupId)
            if (!result.optBoolean("ok")) return@synchronized result
            ensureStarted(context)?.let { return@synchronized JSONObject().put("ok", false).put("message", it) }
            Handler(Looper.getMainLooper()).post {
                instance?.let { service ->
                    service.preparedBatchGroup = result.optString("group_id")
                    service.preparedBatchToken = result.optString("token")
                    service.selectedTaskId = "batch:" + result.optString("group_id")
                    service.selectedTaskName = result.optString("name")
                    service.taskLine?.text = service.selectedTaskName
                    service.lastState = ""
                    service.refreshStatus()
                }
            }
            result
        }

        fun prepareTask(context: Context, taskId: String, taskName: String, username: String): JSONObject =
            synchronized(AgentServer.executionLock) {
                if (AgentServer.pcControlsDevice() || PyBridge.status(context).optBoolean("busy")) {
                    return@synchronized JSONObject().put("ok", false).put("message", "已有任务在执行，请先结束")
                }
                ensureStarted(context)?.let {
                    return@synchronized JSONObject().put("ok", false).put("message", it)
                }
                val selected = PyBridge.selectUser(context, username)
                if (!selected.optBoolean("ok")) return@synchronized selected
                val service = instance ?: return@synchronized JSONObject().put("ok", false).put("message", "悬浮控制尚未就绪")
                service.ui.post {
                    service.preparedBatchGroup = ""
                    service.preparedBatchToken = ""
                    service.selectedTaskId = taskId
                    service.selectedTaskName = taskName
                    service.getSharedPreferences("float_tasks", MODE_PRIVATE).edit().putString("selected", taskId).putString("selected_name", taskName).apply()
                    service.taskLine?.text = taskName
                    service.rebuildActions(service.lastState)
                }
                JSONObject().put("ok", true).put("message", "已准备，请回到对应游戏角色，点击悬浮图标启动任务")
            }

        /** RPC 工作线程调用；只在图标与前台服务都就绪后允许启动任务。 */
        fun ensureStarted(context: Context): String? {
            check(Looper.myLooper() != Looper.getMainLooper())
            if (!Settings.canDrawOverlays(context)) return "请先在手机授予律匠悬浮窗权限"
            if (isRunning) {
                setIconHidden(false)
                instance?.let { svc -> svc.ui.post { svc.closePanel() } }
                return null
            }
            val result = CompletableFuture<String?>()
            val main = Handler(Looper.getMainLooper())
            main.post {
                if (result.isDone) return@post
                if (isRunning) {
                    result.complete(null)
                    return@post
                }
                starting = result
                iconHidden = false
                try {
                    // Android 15 的后台 FGS 豁免要求已有可见覆盖层，先显示启动图标。
                    val manager = context.getSystemService(WINDOW_SERVICE) as WindowManager
                    val size = (42 * context.resources.displayMetrics.density).toInt()
                    val icon = ImageView(context).apply { setImageResource(R.drawable.ic_float) }
                    val params = WindowManager.LayoutParams(size, size,
                        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
                        PixelFormat.TRANSLUCENT).apply {
                        gravity = Gravity.TOP or Gravity.START
                        x = (12 * context.resources.displayMetrics.density).toInt()
                        y = (120 * context.resources.displayMetrics.density).toInt()
                    }
                    startupIcon = icon
                    startupWindowManager = manager
                    manager.addView(icon, params)
                    icon.viewTreeObserver.addOnDrawListener(object : android.view.ViewTreeObserver.OnDrawListener {
                        private var requested = false
                        override fun onDraw() {
                            if (requested) return
                            requested = true
                            icon.post {
                                icon.viewTreeObserver.removeOnDrawListener(this)
                                if (result.isDone) return@post
                                try {
                                    context.startForegroundService(Intent(context, FloatService::class.java))
                                } catch (e: Exception) {
                                    Log.e(TAG, "自动开启悬浮控制失败", e)
                                    finishStart("系统拒绝自动开启悬浮控制，请在手机首页开启后重试：${e.message}")
                                }
                            }
                        }
                    })
                } catch (e: Exception) {
                    finishStart("自动开启悬浮控制失败：${e.message}")
                }
            }
            return try {
                result.get(8, TimeUnit.SECONDS)
            } catch (e: Exception) {
                result.complete("悬浮控制启动超时，请在手机首页开启后重试")
                main.post {
                    if (starting === result) {
                        finishStart("悬浮控制启动超时")
                        context.stopService(Intent(context, FloatService::class.java))
                    }
                }
                "悬浮控制启动超时，请在手机首页开启后重试"
            }
        }

        /** 只由主线程调用，移除临时图标并完成 RPC 等待。 */
        private fun finishStart(error: String?) {
            startupIcon?.let { view -> runCatching { startupWindowManager?.removeView(view) } }
            startupIcon = null
            startupWindowManager = null
            starting?.complete(error)
            starting = null
        }

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
        private const val LOG_VISIBLE_LINES = 100

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

        /** 图标圆底默认色，与 ic_float.xml 的圆底保持一致 */
        private const val COLOR_IDLE = 0xCC3B4A6B.toInt()

        /** 单击直接启动/暂停后的节流窗口：状态轮询最长 1s 才确认，不节流会被连点两次 */
        private const val ICON_TAP_THROTTLE_MS = 800L
    }
}
