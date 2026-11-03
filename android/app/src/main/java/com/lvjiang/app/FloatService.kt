package com.lvjiang.app

import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
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
import android.view.ContextThemeWrapper
import android.view.WindowManager
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import com.google.android.material.color.MaterialColors
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
 * - 图标颜色反映状态：运行中绿 / 失败红 / 已停止橙 / 完成蓝。
 *
 * 面板按状态分两套骨架（见 [PanelMode]），切换时只重建内容、不重建窗口：
 * - **未启动**（`idle`，以及 `done`/`failed`/`stopped` 的结果态）：任务名 + 执行用户
 *   + 「选择用户 / 选择任务」+ 启动；结束后追加一条结果横幅，日志按需展开。
 *   完全运行过后这里不再有空白日志区，面板高度按内容量。
 * - **运行**（`running`/`pausing`/`paused`/`stopping`，含等待人工确认）：状态条
 *   （状态 + 任务名 + 计时）+ 进度（批量才有 `processed/total` 与进度条）+ 操作行
 *   + 占满剩余高度的日志区。运行态不再显示「用户 / 任务」两行——任务已绑定，那两行
 *   既重复又白占 76dp 日志空间。
 *
 * 任务全集仍在二级选择面板中展示。
 */
class FloatService : Service() {

    private lateinit var windowManager: WindowManager
    private var floatView: ImageView? = null
    /** 悬浮图标的布局参数（拖动时就地更新 x/y），面板开展时据此贴近图标 */
    private var floatParams: WindowManager.LayoutParams? = null
    /** 吸附态：贴边收起为边缘细把手（仅内存运行态，不持久化） */
    private var collapsed = false
    private var collapseEdge = ""
    private var panelView: View? = null
    private var panelParams: WindowManager.LayoutParams? = null
    /** 当前骨架：未启动（含结束后的结果态）/ 运行（含暂停与待决策）。 */
    private var panelMode: PanelMode? = null
    /** 骨架内容容器：切换骨架时只重建它的子视图，窗口本身不重建。 */
    private var contentHost: LinearLayout? = null
    private var selectionArea: LinearLayout? = null
    private var showingUsers = false
    /** 日志只有一套实例，跨骨架复用（重建会清空 Editable、重放整批日志）。 */
    private var logLine: TextView? = null
    private var logScroll: ScrollView? = null
    /** 运行骨架里的「最新」：只在用户向上翻过日志时出现。 */
    private var followButton: Button? = null
    // 未启动骨架
    private var idleTitle: TextView? = null
    private var idleUser: TextView? = null
    private var idleStartButton: Button? = null
    private var idleResultBanner: TextView? = null
    private var idleLogToggle: Button? = null
    private var idleLogBox: LinearLayout? = null
    // 运行骨架
    private var runChip: TextView? = null
    private var runTitle: TextView? = null
    private var runTimer: TextView? = null
    private var runProgressLabel: TextView? = null
    private var runProgressBar: ProgressBar? = null
    private var runDetail: TextView? = null
    private var runInteraction: TextView? = null
    private var runActionHost: LinearLayout? = null
    /** 操作行的重建签名（状态 + 交互 id），避免每次轮询都重建按钮。 */
    private var lastActionSignature = ""
    /** 未启动骨架的内容签名：内容高度一变就要重量一次窗口高度，否则会被裁掉。 */
    private var idleLayoutSignature = ""
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

    /** 悬浮 UI 的 Material 3 主题上下文：解析主题色（跟随系统深浅色）。 */
    private val themedContext by lazy { ContextThemeWrapper(this, R.style.Theme_Lvjiang) }
    private fun color(attr: Int): Int = MaterialColors.getColor(themedContext, attr, 0)
    private fun resColor(id: Int): Int = ContextCompat.getColor(this, id)

    // 日志层级色：一律取主题语义色 / 深浅成对资源。早先写死的 #CCCCCC 是"深底浅字"
    // 配色，在浅色面板（#E4ECE7）上只有 1.33:1，等于看不见。
    private val logInfoColor by lazy { color(com.google.android.material.R.attr.colorOnSurface) }
    private val logStampColor by lazy {
        color(com.google.android.material.R.attr.colorOnSurfaceVariant)
    }
    private val logWarnColor by lazy { resColor(R.color.float_log_warn) }
    private val logErrorColor by lazy { resColor(R.color.float_log_error) }

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
            alpha = 1.0f
            elevation = dp(6).toFloat()
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
                        // 把手态不需要长按（展开面板依赖圆球位置），只留拖出/点击恢复
                        if (!collapsed) {
                            ui.postDelayed(arm, ViewConfiguration.getLongPressTimeout().toLong())
                        }
                    }
                    MotionEvent.ACTION_MOVE -> {
                        if (collapsed) {
                            val ddx = event.rawX - downX
                            val ddy = event.rawY - downY
                            val inward = when (collapseEdge) {
                                "left" -> ddx > touchSlop
                                "right" -> ddx < -touchSlop
                                "top" -> ddy > touchSlop
                                "bottom" -> ddy < -touchSlop
                                else -> false
                            }
                            if (inward) {
                                restoreBall()
                                downX = event.rawX
                                downY = event.rawY
                                startX = params.x
                                startY = params.y
                                moved = true
                            }
                            return true
                        }
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
                            !released -> Unit                    // 手势被系统打断，不派发动作
                            collapsed -> restoreBall()           // 把手原地松手：仅恢复圆球
                            dragged -> if (snapEnabled()) maybeSnap() else Unit
                            wasArmed -> togglePanel()            // 长按后原地松手才展开面板
                            else -> onIconTap()                  // 短按保持原有语义
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
        applyIconState(STATE_IDLE)
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
        if (collapsed) return  // 把手态保持中性把手视觉，不映射运行状态
        val icon = floatView ?: return
        val actionable = state in activeStates || (ready && selectedTaskId.isNotEmpty())
        val glyph = when {
            !actionable -> R.drawable.ic_float
            state == STATE_RUNNING || state == STATE_PAUSING || state == STATE_STOPPING ->
                R.drawable.ic_float_glyph_pause
            else -> R.drawable.ic_float_glyph_play
        }
        // 底色 + 符号色成对，跟随 Material 3 主题（深浅色自适应）：
        // 空闲低调灰、可执行柔和主色容器、运行实心主色，暂停/完成/失败各自语义色。
        val (bg, fg) = when {
            state == STATE_FAILED -> color(com.google.android.material.R.attr.colorError) to
                color(com.google.android.material.R.attr.colorOnError)
            state == STATE_DONE -> resColor(R.color.float_success_container) to
                resColor(R.color.float_on_success_container)
            state == STATE_PAUSING || state == STATE_PAUSED ||
                state == STATE_STOPPING || state == STATE_STOPPED ->
                resColor(R.color.float_paused_container) to resColor(R.color.float_on_paused_container)
            state == STATE_RUNNING -> color(com.google.android.material.R.attr.colorPrimary) to
                color(com.google.android.material.R.attr.colorOnPrimary)
            actionable -> color(com.google.android.material.R.attr.colorPrimaryContainer) to
                color(com.google.android.material.R.attr.colorOnPrimaryContainer)
            else -> color(com.google.android.material.R.attr.colorSurfaceContainerHighest) to
                color(com.google.android.material.R.attr.colorOnSurfaceVariant)
        }
        icon.background = InsetDrawable(
            GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(bg) },
            dp(2),
        )
        icon.setImageResource(glyph)
        icon.imageTintList = ColorStateList.valueOf(fg)
        icon.alpha = 1.0f
    }

    // ── 贴边吸附与恢复 ───────────────────────────────────

    private fun snapEnabled(): Boolean =
        getSharedPreferences("float_tasks", MODE_PRIVATE).getBoolean("snap_enabled", false)

    /** 拖动松手后若贴近屏幕边缘，收起为边缘细把手。 */
    private fun maybeSnap() {
        val p = floatParams ?: return
        val metrics = resources.displayMetrics
        val threshold = dp(16)
        val edge = when {
            p.x <= threshold -> "left"
            p.x + p.width >= metrics.widthPixels - threshold -> "right"
            p.y <= threshold -> "top"
            p.y + p.height >= metrics.heightPixels - threshold -> "bottom"
            else -> ""
        }
        if (edge.isNotEmpty()) collapseToEdge(edge)
    }

    private fun collapseToEdge(edge: String) {
        val p = floatParams ?: return
        val metrics = resources.displayMetrics
        val handleW = dp(6)
        val handleH = dp(56)
        val vertical = edge == "left" || edge == "right"
        val targetW = if (vertical) handleW else handleH
        val targetH = if (vertical) handleH else handleW
        val targetX = when (edge) {
            "left" -> 0
            "right" -> metrics.widthPixels - targetW
            else -> p.x.coerceIn(0, maxOf(0, metrics.widthPixels - targetW))
        }
        val targetY = when (edge) {
            "top" -> 0
            "bottom" -> metrics.heightPixels - targetH
            else -> p.y.coerceIn(0, maxOf(0, metrics.heightPixels - targetH))
        }
        animateFloat(p.x, p.y, p.width, p.height, targetX, targetY, targetW, targetH) {
            collapsed = true
            collapseEdge = edge
            setHandleVisual()
        }
    }

    /** 从把手恢复为圆球：贴边方向往内让出一点，避免一恢复又立刻吸附。拖出时即时跟随手指。 */
    private fun restoreBall() {
        val icon = floatView ?: return
        val p = floatParams ?: return
        val metrics = resources.displayMetrics
        val size = dp(42)
        val inset = dp(8)
        p.x = when (collapseEdge) {
            "left" -> inset
            "right" -> metrics.widthPixels - size - inset
            else -> p.x.coerceIn(0, maxOf(0, metrics.widthPixels - size))
        }
        p.y = when (collapseEdge) {
            "top" -> inset
            "bottom" -> metrics.heightPixels - size - inset
            else -> p.y.coerceIn(0, maxOf(0, metrics.heightPixels - size))
        }
        p.width = size
        p.height = size
        collapsed = false
        collapseEdge = ""
        windowManager.updateViewLayout(icon, p)
        applyIconState(lastState)
    }

    /** 把手视觉：贴边细胶囊，中性描边色，不映射任务状态。 */
    private fun setHandleVisual() {
        val icon = floatView ?: return
        icon.setImageResource(0)
        icon.imageTintList = null
        icon.background = GradientDrawable().apply {
            cornerRadius = dp(3).toFloat()
            setColor(color(com.google.android.material.R.attr.colorOutline))
        }
        icon.alpha = 0.85f
    }

    /** 位置与尺寸过渡动画（贴边收起用）。 */
    private fun animateFloat(
        fromX: Int, fromY: Int, fromW: Int, fromH: Int,
        toX: Int, toY: Int, toW: Int, toH: Int, onEnd: () -> Unit,
    ) {
        val icon = floatView ?: run { onEnd(); return }
        val p = floatParams ?: run { onEnd(); return }
        ValueAnimator.ofFloat(0f, 1f).apply {
            duration = 180L
            interpolator = android.view.animation.DecelerateInterpolator()
            addUpdateListener { va ->
                val t = va.animatedValue as Float
                p.x = (fromX + (toX - fromX) * t).toInt()
                p.y = (fromY + (toY - fromY) * t).toInt()
                p.width = (fromW + (toW - fromW) * t).toInt()
                p.height = (fromH + (toH - fromH) * t).toInt()
                runCatching { windowManager.updateViewLayout(icon, p) }
            }
            addListener(object : AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: android.animation.Animator) { onEnd() }
            })
            start()
        }
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
        panelParams = null
        panelMode = null
        contentHost = null
        selectionArea = null
        (logScroll?.parent as? ViewGroup)?.removeView(logScroll)
        logLine = null
        logScroll = null
        clearSkeletonRefs()
        showingUsers = false
        lastActionSignature = ""
        lastLogSeq = 0
        logGeneration = -1
        logLengths.clear()
    }

    /** 清空两套骨架的视图引用；骨架重建与面板关闭都要走一遍。 */
    private fun clearSkeletonRefs() {
        idleTitle = null
        idleUser = null
        idleStartButton = null
        idleResultBanner = null
        idleLogToggle = null
        idleLogBox = null
        runChip = null
        runTitle = null
        runTimer = null
        runProgressLabel = null
        runProgressBar = null
        runDetail = null
        runInteraction = null
        runActionHost = null
        followButton = null
        idleLayoutSignature = ""
    }

    private fun openPanel() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(14), dp(16), dp(14))
            background = GradientDrawable().apply {
                cornerRadius = dp(16).toFloat()
                setColor(color(com.google.android.material.R.attr.colorSurfaceContainerHigh))
                setStroke(dp(1), color(com.google.android.material.R.attr.colorOutlineVariant))
            }
            elevation = dp(8).toFloat()
        }

        val host = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(host, LinearLayout.LayoutParams(-1, 0, 1f))
        contentHost = host

        // 二级选择（用户/任务）继续复用同一块区域，切换时只交换可见性。
        val selection = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
        }
        root.addView(selection, LinearLayout.LayoutParams(-1, 0, 1f))
        selectionArea = selection

        logLine = buildLogView()
        logScroll = buildLogScroll(logLine!!)

        val panelW = minOf(dp(256), resources.displayMetrics.widthPixels - dp(58))
        val params = WindowManager.LayoutParams(
            panelW, WindowManager.LayoutParams.WRAP_CONTENT,
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
        panelParams = params

        // 先按上一次已知状态建骨架，避免轮询请求在途时面板空白一秒
        panelMode = null
        lastActionSignature = ""
        followLogs = true
        buildSkeleton(if (lastState in activeStates) PanelMode.RUN else PanelMode.IDLE)
        refreshStatus()
    }

    private fun buildLogView(): TextView = TextView(this).apply {
        setTextColor(logInfoColor)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, LOG_TEXT_SP)
        setLineSpacing(0f, LOG_LINE_SPACING)
        setText("", TextView.BufferType.EDITABLE)
    }

    private fun buildLogScroll(log: TextView): ScrollView = ScrollView(this).apply {
        addView(log)
        // 独立底色块：文字不再直接压在面板底色上，浅色模式下也有稳定的对比基准
        setPadding(dp(8), dp(6), dp(8), dp(6))
        background = GradientDrawable().apply {
            cornerRadius = dp(10).toFloat()
            setColor(color(com.google.android.material.R.attr.colorSurfaceContainerLow))
            setStroke(dp(1), color(com.google.android.material.R.attr.colorOutlineVariant))
        }
        var downY = 0f
        setOnTouchListener { _, event ->
            if (event.actionMasked == MotionEvent.ACTION_DOWN) downY = event.y
            if (event.actionMasked == MotionEvent.ACTION_MOVE &&
                abs(event.y - downY) > ViewConfiguration.get(this@FloatService).scaledTouchSlop) {
                followLogs = false
                followButton?.visibility = View.VISIBLE
            }
            false
        }
    }

    /**
     * 切换骨架：只重建 contentHost 的子视图并把日志视图重新挂进去，窗口本身
     * 保持不动，因此不会闪、也不会丢滚动位置与已累积的日志。
     */
    private fun buildSkeleton(mode: PanelMode) {
        val host = contentHost ?: return
        (logScroll?.parent as? ViewGroup)?.removeView(logScroll)
        host.removeAllViews()
        clearSkeletonRefs()
        // 运行骨架的窗口高度固定，内容用 weight 吃满；未启动骨架改成 WRAP_CONTENT，
        // 由 relayoutPanel() 量出真实高度再设给窗口（这样 idle 面板不再有空白区）。
        val lp = host.layoutParams as? LinearLayout.LayoutParams
        if (lp != null) {
            if (mode == PanelMode.RUN) {
                lp.height = 0
                lp.weight = 1f
            } else {
                lp.height = ViewGroup.LayoutParams.WRAP_CONTENT
                lp.weight = 0f
            }
            host.layoutParams = lp
        }
        when (mode) {
            PanelMode.IDLE -> buildIdleSkeleton(host)
            PanelMode.RUN -> buildRunSkeleton(host)
        }
        panelMode = mode
        relayoutPanel()
    }

    /** 未启动骨架：选人/选任务 + 启动，任务名与执行用户各占一行标题，不放空白日志区。 */
    private fun buildIdleSkeleton(host: LinearLayout) {
        val heading = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        idleTitle = TextView(this).apply {
            setTextColor(color(com.google.android.material.R.attr.colorOnSurface))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
            text = "未选择任务"
        }
        heading.addView(idleTitle, LinearLayout.LayoutParams(0, -2, 1f))
        idleUser = TextView(this).apply {
            setTextColor(color(com.google.android.material.R.attr.colorPrimary))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
            setPadding(dp(8), dp(2), dp(8), dp(2))
            background = GradientDrawable().apply {
                cornerRadius = dp(8).toFloat()
                setColor(color(com.google.android.material.R.attr.colorSurfaceContainerLow))
            }
            setOnClickListener { openUserSelection() }
        }
        heading.addView(idleUser, LinearLayout.LayoutParams(-2, -2).apply { marginStart = dp(6) })
        host.addView(heading)

        idleResultBanner = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            visibility = View.GONE
        }
        host.addView(idleResultBanner, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })

        val pickRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        pickRow.addView(smallButton("选择用户") { openUserSelection() },
            LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = dp(4) })
        pickRow.addView(smallButton("选择任务") { openTaskSelection() },
            LinearLayout.LayoutParams(0, -2, 1f))
        host.addView(pickRow, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })

        idleLogToggle = smallButton("查看本次日志") { toggleIdleLog() }.apply {
            visibility = View.GONE
        }
        host.addView(idleLogToggle, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(4) })

        idleLogBox = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
        }
        idleLogBox?.addView(logScroll, LinearLayout.LayoutParams(-1, dp(150)))
        host.addView(idleLogBox, LinearLayout.LayoutParams(-1, -2))

        idleStartButton = smallButton("启动任务", FloatButtonKind.FILLED) {
            launchTask(selectedTaskId, selectedTaskName)
        }.apply {
            minimumHeight = dp(40)
            minHeight = dp(40)
        }
        host.addView(idleStartButton, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(10) })
        // 「关闭悬浮窗」只在未启动时提供：它会 stopSelf()，运行中关掉会连带结束任务
        host.addView(smallButton("关闭悬浮窗", FloatButtonKind.TEXT) { closeOverlay() },
            LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(4) })
    }

    /** 运行骨架：状态条 + 进度 + 操作 + 占满剩余高度的日志；不再有用户/任务两行。 */
    private fun buildRunSkeleton(host: LinearLayout) {
        val statusRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        runChip = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            setPadding(dp(8), dp(3), dp(8), dp(3))
            text = "运行中"
        }
        statusRow.addView(runChip, LinearLayout.LayoutParams(-2, -2))
        runTitle = TextView(this).apply {
            setTextColor(color(com.google.android.material.R.attr.colorOnSurface))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
            setPadding(dp(6), 0, 0, 0)
        }
        statusRow.addView(runTitle, LinearLayout.LayoutParams(0, -2, 1f))
        runTimer = TextView(this).apply {
            setTextColor(color(com.google.android.material.R.attr.colorOnSurfaceVariant))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
        }
        statusRow.addView(runTimer, LinearLayout.LayoutParams(-2, -2).apply { marginStart = dp(6) })
        host.addView(statusRow)

        runProgressLabel = TextView(this).apply {
            setTextColor(color(com.google.android.material.R.attr.colorOnSurfaceVariant))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            visibility = View.GONE
        }
        host.addView(runProgressLabel, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })

        runProgressBar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100
            progress = 0
            progressTintList =
                ColorStateList.valueOf(color(com.google.android.material.R.attr.colorPrimary))
            progressBackgroundTintList =
                ColorStateList.valueOf(color(com.google.android.material.R.attr.colorSurfaceVariant))
            visibility = View.GONE
        }
        host.addView(runProgressBar, LinearLayout.LayoutParams(-1, dp(4)).apply { topMargin = dp(4) })

        runDetail = TextView(this).apply {
            setTextColor(color(com.google.android.material.R.attr.colorOnSurface))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            maxLines = 2
            ellipsize = android.text.TextUtils.TruncateAt.END
        }
        host.addView(runDetail, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(6) })

        runInteraction = TextView(this).apply {
            setTextColor(color(com.google.android.material.R.attr.colorOnSecondaryContainer))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
            setPadding(dp(10), dp(8), dp(10), dp(8))
            background = GradientDrawable().apply {
                cornerRadius = dp(10).toFloat()
                setColor(color(com.google.android.material.R.attr.colorSecondaryContainer))
            }
            visibility = View.GONE
        }
        host.addView(runInteraction, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })

        runActionHost = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        host.addView(runActionHost, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })

        val logHeader = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        logHeader.addView(TextView(this).apply {
            text = "日志"
            setTextColor(color(com.google.android.material.R.attr.colorOnSurfaceVariant))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
        }, LinearLayout.LayoutParams(0, -2, 1f))
        followButton = smallButton("最新") { jumpToLatest() }.apply {
            visibility = View.GONE
            minWidth = dp(44)
            minimumWidth = dp(44)
        }
        logHeader.addView(followButton, LinearLayout.LayoutParams(-2, -2))
        host.addView(logHeader, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(10) })

        // 运行骨架的窗口高度固定，日志用 weight=1 吃掉剩余空间
        host.addView(logScroll, LinearLayout.LayoutParams(-1, 0, 1f).apply { topMargin = dp(4) })

        host.addView(smallButton("收起") { closePanel() },
            LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })
    }

    private fun toggleIdleLog() {
        val box = idleLogBox ?: return
        val showing = box.visibility == View.VISIBLE
        box.visibility = if (showing) View.GONE else View.VISIBLE
        idleLogToggle?.text = if (showing) "查看本次日志" else "收起日志"
        if (!showing) logScroll?.post { logScroll?.fullScroll(View.FOCUS_DOWN) }
        relayoutPanel()
    }

    private fun jumpToLatest() {
        followLogs = true
        followButton?.visibility = View.GONE
        logScroll?.post { logScroll?.fullScroll(View.FOCUS_DOWN) }
    }

    /**
     * 重排窗口：运行骨架用固定上限高度，未启动骨架用实测高度（WRAP_CONTENT），
     * 两种情况下都按实际尺寸重新贴到图标旁边，避免此前"固定 260dp 估算"贴错位。
     */
    private fun relayoutPanel() {
        val root = panelView ?: return
        val params = panelParams ?: return
        val maxHeight = usableHeight() - dp(24)
        // 二级选择视图内部是 weight 撑开的列表，必须有确定高度；运行骨架同理。
        // 只有"未启动骨架且没在选东西"时才按内容量高度。
        val selectionVisible = selectionArea?.visibility == View.VISIBLE
        if (panelMode == PanelMode.RUN || selectionVisible) {
            params.height = minOf(dp(360), maxHeight)
        } else {
            root.measure(
                View.MeasureSpec.makeMeasureSpec(params.width, View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED))
            params.height = minOf(root.measuredHeight, maxHeight)
        }
        anchorPanelToIcon(params, params.width)
        runCatching { windowManager.updateViewLayout(root, params) }
    }

    /**
     * 把面板放到悬浮图标旁边（gravity 均为 TOP|START，故 x/y 直接用图标坐标系）。
     *
     * 横向：图标在左半屏→面板贴在图标右侧；在右半屏→贴左侧，避免越界。
     * 纵向：与图标顶端对齐向下展开；靠底时上提（WRAP_CONTENT 阶段还没有实测高度，
     * 按 0 处理，随后 relayoutPanel() 会带真实高度再贴一次）。
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
        val panelHApprox = params.height.coerceAtLeast(0)

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

    /** 面板按钮分级：FILLED=主动作（primary），TONAL=次动作（secondaryContainer），TEXT=危险（error）。 */
    private enum class FloatButtonKind { FILLED, TONAL, TEXT }

    /**
     * 面板只有两套骨架：未启动（含 done/failed/stopped 的结果态）与
     * 运行（running/pausing/paused/stopping，以及等待人工确认）。
     */
    private enum class PanelMode { IDLE, RUN }

    /** 结果横幅内容 + 需要套用的主题容器色。 */
    private class ResultBanner(val text: String, val backgroundAttr: Int, val foregroundAttr: Int)

    private fun smallButton(label: String, kind: FloatButtonKind = FloatButtonKind.TONAL, onClick: () -> Unit): Button =
        Button(this).apply {
            text = label
            isAllCaps = false
            isSingleLine = true
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            minimumHeight = dp(34)
            minHeight = dp(34)
            setPadding(dp(12), 0, dp(12), 0)
            elevation = 0f
            background = GradientDrawable().apply {
                cornerRadius = dp(12).toFloat()
                setColor(when (kind) {
                    FloatButtonKind.FILLED -> color(com.google.android.material.R.attr.colorPrimary)
                    FloatButtonKind.TONAL -> color(com.google.android.material.R.attr.colorSecondaryContainer)
                    FloatButtonKind.TEXT -> Color.TRANSPARENT
                })
            }
            setTextColor(when (kind) {
                FloatButtonKind.FILLED -> color(com.google.android.material.R.attr.colorOnPrimary)
                FloatButtonKind.TONAL -> color(com.google.android.material.R.attr.colorOnSecondaryContainer)
                FloatButtonKind.TEXT -> color(com.google.android.material.R.attr.colorError)
            })
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = dp(4) }
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
                val sync = status.optJSONObject("sync")
                ready = sync?.optBoolean("ready") == true
                if (state in activeStates) {
                    selectedTaskId = status.optString("task_id")
                    selectedTaskName = taskName
                    closeTaskSelection()
                }
                val nextInteraction = status.optJSONObject("interaction")?.optString("id").orEmpty()
                if (state != lastState || nextInteraction != interactionId) {
                    lastActionSignature = ""
                }
                interactionId = nextInteraction
                lastState = state

                // 只有状态跨过"运行/未运行"边界时才换骨架，暂停↔运行只改内容
                val wanted = if (state in activeStates) PanelMode.RUN else PanelMode.IDLE
                if (panelView != null && panelMode != wanted) buildSkeleton(wanted)

                bindStatus(state, message, taskName, elapsed, status)
                appendLogs(status)
                applyIconState(state)
                updateNotification(notificationText(state, taskName, message, elapsed))

                val stamp = sync?.optString("synced_at", "preset") ?: ""
                if (ready && stamp != syncStamp && state !in activeStates) {
                    syncStamp = stamp
                    loadTasks()
                }
            }
        }
    }

    private fun bindStatus(state: String, message: String, taskName: String,
                           elapsed: Double, status: JSONObject) {
        when (panelMode) {
            PanelMode.RUN -> bindRunPanel(state, message, taskName, elapsed, status)
            PanelMode.IDLE -> bindIdlePanel(state, status)
            null -> {}
        }
    }

    /** 未启动骨架：任务名 + 执行用户 + 结果横幅 + 启动按钮。 */
    private fun bindIdlePanel(state: String, status: JSONObject) {
        val user = SettingsUi.userLabel(
            status.optJSONObject("sync")?.optString("execution_username").orEmpty())
        idleUser?.text = when {
            !ready -> "预置配置未就绪"
            user.isNotEmpty() -> user
            else -> "未指定用户"
        }
        idleUser?.isEnabled = ready
        applySelectionToUi()
        val banner = resultBanner(state, status)
        idleResultBanner?.apply {
            if (banner == null) {
                visibility = View.GONE
            } else {
                visibility = View.VISIBLE
                text = banner.text
                setTextColor(color(banner.foregroundAttr))
                background = GradientDrawable().apply {
                    cornerRadius = dp(10).toFloat()
                    setColor(color(banner.backgroundAttr))
                }
                setPadding(dp(10), dp(6), dp(10), dp(6))
            }
        }
        idleLogToggle?.visibility = if (hasLog()) View.VISIBLE else View.GONE
        // 未启动骨架的窗口高度是按内容量出来的：横幅 / 日志入口 / 文案一变就重量一次，
        // 否则新冒出来的内容会被上一轮算好的高度裁掉。
        val layoutSignature = listOf(
            banner?.text.orEmpty(),
            idleTitle?.text?.toString().orEmpty(),
            idleUser?.text?.toString().orEmpty(),
            (idleResultBanner?.visibility ?: View.GONE).toString(),
            (idleLogToggle?.visibility ?: View.GONE).toString(),
            (idleLogBox?.visibility ?: View.GONE).toString(),
        ).joinToString("|")
        if (layoutSignature != idleLayoutSignature) {
            idleLayoutSignature = layoutSignature
            relayoutPanel()
        }
    }

    /** 运行骨架：状态条 + 进度 + 操作行（日志由 appendLogs 增量追加）。 */
    private fun bindRunPanel(state: String, message: String, taskName: String,
                             elapsed: Double, status: JSONObject) {
        val chipText = when (state) {
            STATE_PAUSED -> "已暂停"
            STATE_PAUSING -> "正在暂停"
            STATE_STOPPING -> "正在停止"
            else -> "运行中"
        }
        val (chipBg, chipFg) = when (state) {
            STATE_PAUSED, STATE_PAUSING ->
                resColor(R.color.float_paused_container) to resColor(R.color.float_on_paused_container)
            STATE_STOPPING ->
                color(com.google.android.material.R.attr.colorSurfaceVariant) to
                    color(com.google.android.material.R.attr.colorOnSurfaceVariant)
            else ->
                color(com.google.android.material.R.attr.colorSecondaryContainer) to
                    color(com.google.android.material.R.attr.colorOnSecondaryContainer)
        }
        // 轮询每秒一次，状态没变就不重建背景 drawable（省掉每秒一份垃圾）
        if (runChip?.tag != chipText) {
            runChip?.apply {
                tag = chipText
                text = chipText
                setTextColor(chipFg)
                background = GradientDrawable().apply {
                    cornerRadius = dp(8).toFloat()
                    setColor(chipBg)
                }
            }
        }
        runTitle?.text = taskName.ifEmpty { selectedTaskName }.ifEmpty { "任务" }
        runTimer?.text = formatElapsed(elapsed)

        // 批量才有确定的 total；单任务没有"第几项"这回事，只显示当前步骤
        val batch = status.optJSONObject("batch")
        val total = batch?.optInt("total", 0) ?: 0
        val processed = batch?.optInt("processed", 0) ?: 0
        val rounds = batch?.optInt("rounds", 1) ?: 1
        val round = batch?.optInt("round", 0) ?: 0
        if (total > 0) {
            runProgressLabel?.apply {
                visibility = View.VISIBLE
                text = buildString {
                    append("已处理 ").append(processed).append(" / ").append(total).append(" 项")
                    if (rounds > 1 && round in 1..rounds) {
                        append(" · 第 ").append(round).append(" / ").append(rounds).append(" 轮")
                    }
                }
            }
            runProgressBar?.apply {
                visibility = View.VISIBLE
                max = total
                progress = processed.coerceIn(0, total)
            }
            val entry = batch?.optString("current_entry").orEmpty()
            val currentTask = batch?.optString("current_task").orEmpty()
            runDetail?.text = listOf(entry, currentTask)
                .filter { it.isNotEmpty() }
                .joinToString(" · ")
                .ifEmpty { detailMessage(message) }
        } else {
            runProgressLabel?.visibility = View.GONE
            runProgressBar?.visibility = View.GONE
            runDetail?.text = detailMessage(message)
        }

        val interactionMessage =
            status.optJSONObject("interaction")?.optString("message").orEmpty()
        runInteraction?.apply {
            if (interactionMessage.isEmpty()) {
                visibility = View.GONE
            } else {
                visibility = View.VISIBLE
                text = interactionMessage
            }
        }

        val signature = "$state|$interactionId"
        if (signature != lastActionSignature) {
            lastActionSignature = signature
            rebuildRunActions(state, interactionId.isNotEmpty())
        }
    }

    /** 顶部仅放启动或安全控制；任务全集仅在二级选择中展示。 */
    private fun rebuildRunActions(state: String, awaitingAnswer: Boolean) {
        val area = runActionHost ?: return
        area.removeAllViews()
        val subject = if (selectedTaskId.startsWith("batch:")) "批量" else "任务"
        if (awaitingAnswer && state != STATE_STOPPING) {
            val answerRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            answerRow.addView(smallButton("确认", FloatButtonKind.FILLED) { answerInteraction(true) },
                LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = dp(4) })
            answerRow.addView(smallButton("拒绝", FloatButtonKind.TONAL) { answerInteraction(false) },
                LinearLayout.LayoutParams(0, -2, 1f))
            area.addView(answerRow)
            // 等待回答时「结束」也要留着：停止本来就是"不回答"的另一种合法出口
            area.addView(smallButton("结束$subject", FloatButtonKind.TEXT) { requestStop() },
                LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(4) })
            return
        }
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        row.addView(
            smallButton(if (state == STATE_PAUSED) "继续$subject" else "暂停$subject",
                FloatButtonKind.FILLED) { pauseResume(pause = state != STATE_PAUSED) }
                .apply { isEnabled = state == STATE_RUNNING || state == STATE_PAUSED },
            LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = dp(4) })
        row.addView(smallButton("结束$subject", FloatButtonKind.TEXT) { requestStop() }
            .apply { isEnabled = state != STATE_STOPPING },
            LinearLayout.LayoutParams(0, -2, 1f))
        area.addView(row)
    }

    private fun answerInteraction(accepted: Boolean) {
        val requestId = interactionId
        if (requestId.isEmpty()) return
        executor.execute {
            val result = PyBridge.respondInteraction(this, requestId, accepted)
            toast(result.optString("message"))
            ui.post { refreshStatus() }
        }
    }

    private fun requestStop() {
        executor.execute {
            val r = PyBridge.stopTask(this)
            toast(r.optString("message", "已请求停止"))
            ui.post { refreshStatus() }
        }
    }

    /** 把当前选择同步到未启动骨架（任务名、启动按钮文案与可用性）。 */
    private fun applySelectionToUi() {
        idleTitle?.text = selectedTaskName.ifEmpty { "未选择任务" }
        idleStartButton?.apply {
            text = if (selectedTaskId.startsWith("batch:")) "启动批量" else "启动任务"
            isEnabled = ready && selectedTaskId.isNotEmpty()
        }
    }

    private fun hasLog(): Boolean = (logLine?.length() ?: 0) > 0

    /** ``done``/``failed``/``stopped`` 的结果横幅：任务名之后一行说清结果与用时。 */
    private fun resultBanner(state: String, status: JSONObject): ResultBanner? {
        val label = when (state) {
            STATE_DONE -> "已完成"
            STATE_FAILED -> "失败"
            STATE_STOPPED -> "已停止"
            else -> return null
        }
        val elapsed = status.optDouble("elapsed", 0.0)
        val summary = status.optJSONObject("result")?.let { summaryOf(it) }
            ?.ifEmpty { null } ?: status.optString("message")
        val text = buildString {
            append(label)
            if (elapsed > 0) append(" · 用时 ").append(formatElapsed(elapsed))
            firstLine(summary).takeIf { it.isNotEmpty() }?.let { append(" · ").append(it) }
        }
        val failed = state == STATE_FAILED
        return ResultBanner(
            text,
            if (failed) com.google.android.material.R.attr.colorErrorContainer
            else com.google.android.material.R.attr.colorSecondaryContainer,
            if (failed) com.google.android.material.R.attr.colorOnErrorContainer
            else com.google.android.material.R.attr.colorOnSecondaryContainer,
        )
    }

    private fun summaryOf(result: JSONObject): String =
        listOf("summary", "message", "error")
            .mapNotNull { key -> result.optString(key).takeIf { it.isNotBlank() } }
            .joinToString(" ")

    private fun firstLine(text: String): String =
        text.lineSequence().firstOrNull { it.isNotBlank() }?.trim()?.take(80) ?: ""

    /** 状态里那些"执行中"之类的空话不值得占一行。 */
    private fun detailMessage(message: String): String {
        val trimmed = message.trim()
        val noisy = setOf("执行中", "运行中", "正在启动", "已暂停", "正在暂停", "正在停止")
        if (trimmed.isEmpty() || trimmed in noisy) return ""
        return trimmed.lineSequence().first().take(120)
    }

    private fun formatElapsed(seconds: Double): String {
        val total = seconds.toLong().coerceAtLeast(0)
        val h = total / 3600
        val m = (total % 3600) / 60
        val s = total % 60
        return if (h > 0) "%d:%02d:%02d".format(h, m, s) else "%02d:%02d".format(m, s)
    }

    private fun notificationText(state: String, taskName: String, message: String,
                                 elapsed: Double): String = when (state) {
        STATE_RUNNING -> "运行中（${formatElapsed(elapsed)}）：$taskName"
        STATE_PAUSING -> "正在暂停：$taskName"
        STATE_PAUSED -> "已暂停：$taskName"
        STATE_STOPPING -> "正在停止：$taskName"
        STATE_IDLE -> if (ready) "待命：${selectedTaskName.ifEmpty { "未选择任务" }}"
        else "预置配置未就绪"
        else -> message.ifEmpty { "空闲" }.replace('\n', ' ')
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
            followButton?.visibility = View.GONE
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
            val lineColor = when (level) {
                "WARNING" -> logWarnColor
                "ERROR", "CRITICAL" -> logErrorColor
                else -> logInfoColor
            }
            for (content in record.optString("text").lines().takeLast(LOG_VISIBLE_LINES)) {
                val line = SpannableString(content.take(2000) + "\n")
                // 时间戳弱化、正文用层级色：整行同色时"哪一行是什么时候的"要重新读一遍
                val stampEnd = TIMESTAMP_RE.find(content)?.range?.last?.plus(1) ?: 0
                if (stampEnd > 0) {
                    line.setSpan(ForegroundColorSpan(logStampColor), 0, stampEnd,
                        Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                }
                line.setSpan(ForegroundColorSpan(lineColor), stampEnd, line.length,
                    Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
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
        followButton?.visibility = if (followLogs) View.GONE else View.VISIBLE
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
                    selectedTaskId = ""
                    selectedTaskName = ""
                    applySelectionToUi()
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
                applySelectionToUi()
                if (selectionArea?.visibility == View.VISIBLE && !showingUsers) renderTaskSelection()
            }
        }
    }

    private fun openTaskSelection() {
        if (!ready || lastState in activeStates) return
        showingUsers = false
        contentHost?.visibility = View.GONE
        selectionArea?.visibility = View.VISIBLE
        relayoutPanel()
        renderTaskSelection()
        loadTasks()
    }

    private fun openUserSelection() {
        if (!ready || lastState in activeStates) return
        showingUsers = true
        contentHost?.visibility = View.GONE
        selectionArea?.visibility = View.VISIBLE
        relayoutPanel()
        selectionArea?.removeAllViews()
        selectionArea?.addView(TextView(this).apply { text = "读取用户…"; setTextColor(color(com.google.android.material.R.attr.colorOnSurface)) })
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
                    setTextColor(color(com.google.android.material.R.attr.colorOnSurface))
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
        area.addView(TextView(this).apply { text = "选择任务"; setTextColor(color(com.google.android.material.R.attr.colorOnSurface)) })
        val choices = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        for ((id, name) in tasks) {
            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            row.addView(smallButton(if (id == selectedTaskId) "✓ $name" else name) {
                if (lastState in activeStates) return@smallButton
                selectedTaskId = id
                selectedTaskName = name
                preparedBatchGroup = ""; preparedBatchToken = ""
                getSharedPreferences("float_tasks", MODE_PRIVATE).edit().putString("selected", id).putString("selected_name", name).apply()
                applySelectionToUi()
                closeTaskSelection()
            }, LinearLayout.LayoutParams(0, -2, 1f).apply { topMargin = dp(4) })
            row.addView(taskMoreButton(id))
            choices.addView(row)
        }
        area.addView(ScrollView(this).apply { addView(choices) }, LinearLayout.LayoutParams(-1, 0, 1f))
        area.addView(smallButton("返回") { closeTaskSelection() })
    }

    /**
     * 任务行尾的「更多」入口：点 ⋮ 弹菜单，操作针对该行任务，不要求先选中。
     * 比齿轮图标轻，也为后续挂更多行内操作留口。
     */
    private fun taskMoreButton(taskId: String): Button =
        smallButton("⋮") {}.apply {
            minWidth = dp(36)
            minimumWidth = dp(36)
            setPadding(dp(10), 0, dp(10), 0)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = dp(4); marginStart = dp(4) }
            setOnClickListener { anchor ->
                val menu = PopupMenu(this@FloatService, anchor)
                menu.menu.add("设置任务参数")
                menu.setOnMenuItemClickListener {
                    startActivity(Intent(this@FloatService, TaskSettingsActivity::class.java).apply {
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        putExtra("task_id", taskId)
                    })
                    closePanel()
                    true
                }
                menu.show()
            }
        }

    private fun closeTaskSelection() {
        showingUsers = false
        selectionArea?.visibility = View.GONE
        contentHost?.visibility = View.VISIBLE
        relayoutPanel()
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
                    service.applySelectionToUi()
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
                    service.applySelectionToUi()
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
        /** 日志字号与行距：12sp 无行距在游戏画面上扫读成本太高 */
        private const val LOG_TEXT_SP = 13f
        private const val LOG_LINE_SPACING = 1.15f
        /** Python 侧 log() 的前缀是 "HH:MM:SS "，用它把时间戳单独弱化 */
        private val TIMESTAMP_RE = Regex("^\\d{2}:\\d{2}:\\d{2} ")

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

        /** 单击直接启动/暂停后的节流窗口：状态轮询最长 1s 才确认，不节流会被连点两次 */
        private const val ICON_TAP_THROTTLE_MS = 800L
    }
}
