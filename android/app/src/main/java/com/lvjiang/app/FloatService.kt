package com.lvjiang.app

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.graphics.PixelFormat
import android.os.IBinder
import android.util.Log
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.ImageView
import android.widget.Toast
import java.util.concurrent.Executors
import kotlin.math.abs

/**
 * FloatService — 悬浮小图标前台服务。
 *
 * Phase 0 行为：
 * - 前台服务（specialUse）+ 常驻通知；
 * - 悬浮图标可拖动；
 * - 单击图标 → 通过 ShellBridge 在屏幕中心注入一次 input tap（通道验证）。
 *
 * 后续阶段：单击展开任务菜单（启动/停止调律），状态色反映运行状态。
 */
class FloatService : Service() {

    private lateinit var windowManager: WindowManager
    private var floatView: ImageView? = null
    private val executor = Executors.newSingleThreadExecutor()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        startForeground(NOTIFICATION_ID, buildNotification())
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager
        addFloatIcon()
    }

    override fun onDestroy() {
        floatView?.let { windowManager.removeView(it) }
        floatView = null
        executor.shutdown()
        super.onDestroy()
    }

    private fun buildNotification(): Notification {
        val pi = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, App.CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_float)
            .setContentTitle("律匠")
            .setContentText("悬浮控制运行中")
            .setContentIntent(pi)
            .setOngoing(true)
            .build()
    }

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
                        if (!moved) onIconClick()
                    }
                }
                return true
            }
        })

        windowManager.addView(icon, params)
        floatView = icon
    }

    /** Phase 0：单击 → 屏幕中心注入一次 tap，验证 Shizuku 通道 */
    private fun onIconClick() {
        executor.execute {
            if (!ShellBridge.awaitReady()) {
                report("checkpoint1: shizuku not ready")
                toast("Shizuku 服务未就绪")
                return@execute
            }
            val metrics = resources.displayMetrics
            val cx = metrics.widthPixels / 2
            val cy = metrics.heightPixels / 2
            val t0 = System.currentTimeMillis()
            val out = ShellBridge.tap(cx, cy)
            val cost = System.currentTimeMillis() - t0
            report("checkpoint1: input tap $cx $cy -> ${out.ifEmpty { "ok" }} cost=${cost}ms")
            toast("已注入点击 ($cx, $cy) ${cost}ms")
        }
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
        android.os.Handler(mainLooper).post {
            Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
        }
    }

    companion object {
        private const val TAG = "FloatService"
        private const val NOTIFICATION_ID = 1
        private const val ICON_SIZE_PX = 140
        private const val TOUCH_SLOP = 12f
    }
}
