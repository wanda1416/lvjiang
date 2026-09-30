package com.lvjiang.app

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityService.ScreenshotResult
import android.accessibilityservice.AccessibilityServiceInfo
import android.graphics.Bitmap
import android.graphics.Path
import android.util.Log
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import java.nio.ByteBuffer
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * A11yService — 截图与输入的主通道。
 *
 * 为什么不用 Shizuku 当主通道：Shizuku 的无 root 模式必须由 adb 引导启动
 * （它本质是个跑在 shell uid 的 app_process 进程），手机重启一次就失效，
 * 要用户重新进开发者选项配对无线调试。无障碍服务只需在设置里开一次开关，
 * 开关状态持久化在 secure settings 里，重启保留，且能用 adb 直接写入
 * （开发期可全自动开启，不依赖人工点击）。
 *
 * 本服务不监听任何界面事件，只把系统赋予无障碍服务的两项能力借出来：
 *   - takeScreenshot()  整屏截图（Android 11+）
 *   - dispatchGesture() 手势注入（点击/滑动/推住不放）
 *   - performGlobalAction() 系统 BACK/HOME
 * 因此 accessibilityEventTypes 配成 none，不产生任何事件回调开销。
 */
class A11yService : AccessibilityService() {

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        Log.i(TAG, "无障碍服务已连接")
        // 幂等：App.onCreate 已启动过则直接返回；这里兜底进程由系统为绑定服务而拉起的情况
        AgentServer.start()
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        instance = null
        Log.i(TAG, "无障碍服务已断开")
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    /** 不监听事件：本服务只借用截图与手势能力 */
    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit

    override fun onInterrupt() = Unit

    companion object {
        private const val TAG = "A11yService"

        /**
         * 服务实例由系统创建，无法自己 new，只能在 onServiceConnected 里登记。
         * 拿不到就说明无障碍开关没开（或被系统关掉了）。
         */
        @Volatile
        @JvmStatic
        var instance: A11yService? = null
            private set
    }
}

/**
 * A11yBridge — 无障碍能力的同步门面，供 Python 侧调用。
 *
 * 系统给的截图与手势 API 都是异步回调式，而工作流引擎是同步的顺序脚本，
 * 所以这里统一用 CountDownLatch 折叠成阻塞调用。所有方法都不抛异常，
 * 失败一律返回空值 / false，由 Python 侧按返回值判断并落进自检报告。
 *
 * 注意 Kotlin object 在 Chaquopy 里要走 A11yBridge.INSTANCE：编译后那些方法
 * 仍是实例方法，挂在编译器生成的 INSTANCE 静态字段上。
 */
/**
 * 时间线上的一路触点。字段与 PC 协议的 `gesture.strokes[]` 一一对应，
 * 不在两端各起一套名字。
 */
data class TimelineStroke(
    val startMs: Long,
    val moveMs: Long,
    val holdMs: Long,
    val x1: Int,
    val y1: Int,
    val x2: Int,
    val y2: Int,
)

object A11yBridge {

    private const val TAG = "A11yBridge"

    /** 截图回调线程池：单线程够用，截图本身是串行的 */
    private val executor = Executors.newSingleThreadExecutor()

    /** 无障碍服务是否已连接（唯一的「通道可用」判据） */
    fun isReady(): Boolean = A11yService.instance != null

    /**
     * 整屏截图，返回 [宽, 高, RGBA 字节] 三元组；失败返回 null。
     *
     * 刻意不编码成 PNG：Python 侧拿 RGBA 裸字节可以直接 numpy.frombuffer + reshape，
     * 省掉「PNG 压缩 → imdecode 解压」这一对纯浪费的往返。
     *
     * takeScreenshot 有节流（数百毫秒级最小间隔），连续调用过快会返回
     * ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT，调用方需自行留间隔。
     */
    fun screenshotRgba(timeoutMs: Long = 5000): Array<Any>? {
        val service = A11yService.instance ?: run {
            Log.w(TAG, "截图失败：无障碍服务未连接")
            return null
        }

        val latch = CountDownLatch(1)
        var result: Array<Any>? = null

        service.takeScreenshot(
            Display.DEFAULT_DISPLAY,
            executor,
            object : AccessibilityService.TakeScreenshotCallback {
                override fun onSuccess(screenshot: ScreenshotResult) {
                    try {
                        result = toRgba(screenshot)
                    } catch (e: Throwable) {
                        Log.e(TAG, "截图转换失败", e)
                    } finally {
                        // HardwareBuffer 不释放会很快耗尽缓冲区，后续截图全部失败
                        screenshot.hardwareBuffer.close()
                        latch.countDown()
                    }
                }

                override fun onFailure(errorCode: Int) {
                    Log.w(TAG, "截图失败 errorCode=$errorCode")
                    latch.countDown()
                }
            },
        )

        if (!latch.await(timeoutMs, TimeUnit.MILLISECONDS)) {
            Log.w(TAG, "截图超时 ${timeoutMs}ms")
            return null
        }
        return result
    }

    /**
     * HardwareBuffer 包成的 Bitmap 是 HARDWARE config，不能直接读像素，
     * 必须先 copy 成 ARGB_8888 才能拷进 ByteBuffer。
     */
    private fun toRgba(screenshot: ScreenshotResult): Array<Any> {
        val hardware = Bitmap.wrapHardwareBuffer(screenshot.hardwareBuffer, screenshot.colorSpace)
            ?: throw IllegalStateException("wrapHardwareBuffer 返回 null")
        val bitmap = hardware.copy(Bitmap.Config.ARGB_8888, false)
            ?: throw IllegalStateException("copy 到 ARGB_8888 失败")
        hardware.recycle()

        val buffer = ByteBuffer.allocate(bitmap.byteCount)
        bitmap.copyPixelsToBuffer(buffer)
        val width = bitmap.width
        val height = bitmap.height
        bitmap.recycle()

        return arrayOf(width, height, buffer.array())
    }

    // 所有手势的坐标都是调用方在截图上量出来的，进来先过一遍 ScreenMap（截图空间 → 输入空间，
    // 绝大多数设备是恒等）。映射放在这个注入口而不是各调用方：PC 代理通道与设备端 Python
    // 通道都经过这里，标定一次两边同时生效。

    /** 单点点击；durationMs 是按住时长 */
    fun tap(x: Int, y: Int, durationMs: Long = 50): Boolean {
        val p = ScreenMap.mapPoint(x, y)
        val path = Path().apply { moveTo(p[0].toFloat(), p[1].toFloat()) }
        return dispatch(path, 0, durationMs)
    }

    /** 直线滑动 */
    fun swipe(x1: Int, y1: Int, x2: Int, y2: Int, durationMs: Long): Boolean {
        val a = ScreenMap.mapPoint(x1, y1)
        val b = ScreenMap.mapPoint(x2, y2)
        val path = Path().apply {
            moveTo(a[0].toFloat(), a[1].toFloat())
            lineTo(b[0].toFloat(), b[1].toFloat())
        }
        return dispatch(path, 0, durationMs)
    }

    /**
     * 长按：dispatchGesture 单个 stroke 的时长上限约 1 分钟，实际业务里
     * 长按都是几百毫秒到几秒，直接用 stroke 时长表达即可。
     */
    fun longPress(x: Int, y: Int, durationMs: Long): Boolean {
        val p = ScreenMap.mapPoint(x, y)
        val path = Path().apply { moveTo(p[0].toFloat(), p[1].toFloat()) }
        return dispatch(path, 0, durationMs)
    }

    private fun dispatch(path: Path, startTime: Long, durationMs: Long): Boolean {
        val service = A11yService.instance ?: run {
            Log.w(TAG, "手势失败：无障碍服务未连接")
            return false
        }

        val stroke = android.accessibilityservice.GestureDescription.StrokeDescription(
            path, startTime, durationMs.coerceAtLeast(1),
        )
        val gesture = android.accessibilityservice.GestureDescription.Builder()
            .addStroke(stroke)
            .build()

        val latch = CountDownLatch(1)
        var completed = false

        val ok = service.dispatchGesture(
            gesture,
            object : AccessibilityService.GestureResultCallback() {
                override fun onCompleted(description: android.accessibilityservice.GestureDescription?) {
                    completed = true
                    latch.countDown()
                }

                override fun onCancelled(description: android.accessibilityservice.GestureDescription?) {
                    Log.w(TAG, "手势被取消")
                    latch.countDown()
                }
            },
            null,
        )
        if (!ok) {
            Log.w(TAG, "dispatchGesture 返回 false（服务未就绪或手势非法）")
            return false
        }

        // 等回调而不是立即返回：上层脚本紧接着就要截图看结果，手势没落地就截等于白截。
        // 超时给足 stroke 时长 + 3s 余量。
        if (!latch.await(durationMs + 3000, TimeUnit.MILLISECONDS)) {
            Log.w(TAG, "等待手势回调超时")
            return false
        }
        return completed
    }

    /**
     * 推到位后按住：移动 stroke（willContinue）+ 同一指针的 dwell stroke（continueStroke）。
     *
     * 为什么不是一条长 stroke：单 stroke 的 duration 是沿整条 path 的总时长，手指会在
     * durationMs 内**匀速滑完全程**，推摇杆时就成了"慢慢推"而不是"推到位停住"；
     * continueStroke 的 dwell 段 path 只有 1px，时长却是 holdMs，这才是按住不动。
     * 两段各等回调，总耗时 ≈ moveMs + holdMs。
     */
    fun holdMove(x1: Int, y1: Int, x2: Int, y2: Int, moveMs: Long, holdMs: Long): Boolean {
        val service = A11yService.instance ?: run {
            Log.w(TAG, "手势失败：无障碍服务未连接")
            return false
        }
        val a = ScreenMap.mapPoint(x1, y1)
        val b = ScreenMap.mapPoint(x2, y2)
        val movePath = Path().apply {
            moveTo(a[0].toFloat(), a[1].toFloat())
            lineTo(b[0].toFloat(), b[1].toFloat())
        }
        val moveStroke = android.accessibilityservice.GestureDescription.StrokeDescription(
            movePath, 0, moveMs.coerceAtLeast(1), holdMs > 0,
        )
        if (!dispatchAndWait(service, moveStroke, moveMs)) return false
        if (holdMs <= 0) return true
        val dwellPath = Path().apply {
            moveTo(b[0].toFloat(), b[1].toFloat())
            lineTo(b[0] + 1f, b[1].toFloat())
        }
        val dwellStroke = moveStroke.continueStroke(dwellPath, 0, holdMs, false)
        return dispatchAndWait(service, dwellStroke, holdMs)
    }

    /**
     * 输入时间线：若干路触点各自在时间线上的起点，一次性并发下发。
     *
     * 关键是**一个** GestureDescription 装多条 stroke——同属一个手势才是真并发，
     * 拆成多次 dispatch 就退化成顺序执行，而 PC 侧看到的仍是"并发已生效"。
     * 每条 stroke 的 startTime 相对手势起点，系统按帧调度。
     *
     * 整组原子：任一路非法或被真实触摸打断，回调都是整组 onCancelled，不会留下
     * "一根手指还按着"的半截状态。所以这里也不做部分成功的返回。
     *
     * strokes 每项：startMs / moveMs / holdMs / (x1,y1) / (x2,y2)。落地方式见下面
     * 循环里的注释——时间线里的推杆是"直接按在目标点上保持"，没有滑动过程。
     * 未在真机验证：多路并发能否被游戏接受、本机 getMaxStrokeCount 的实际值，
     * 用 `python -m lvjiang.core.android.gesture_probe` 单独测。
     */
    fun timeline(strokes: List<TimelineStroke>): String? {
        val service = A11yService.instance ?: return "无障碍服务未连接"
        if (strokes.isEmpty()) return "时间线没有任何步骤"
        val maxStrokes = android.accessibilityservice.GestureDescription.getMaxStrokeCount()
        if (strokes.size > maxStrokes) {
            return "时间线有 ${strokes.size} 路触点，本机同时可注入上限为 $maxStrokes"
        }
        val maxDuration =
            android.accessibilityservice.GestureDescription.getMaxGestureDuration()
        val span = strokes.maxOf { it.startMs + it.moveMs + it.holdMs }
        if (span > maxDuration) {
            return "时间线总长 ${span}ms 超过本机单次手势上限 ${maxDuration}ms"
        }

        val builder = android.accessibilityservice.GestureDescription.Builder()
        for (s in strokes) {
            val b = ScreenMap.mapPoint(s.x2, s.y2)
            // 一条 stroke 只能匀速走完整条 path，表达不了"滑到位再停住"；而
            // continueStroke 的续接段要另起一次 dispatchGesture（holdMove 就是
            // 那么做的），放进同一个 GestureDescription 就不是并发了。
            //
            // 所以时间线里的推杆取**直接按在目标点上并保持**：手指整段时间都在
            // 推满的位置，这正是"推到位停住"要的效果，只是没有滑动过程。
            // moveMs 因此并入总时长而不单独插值。
            val duration = (s.moveMs + s.holdMs).coerceAtLeast(1)
            val path = Path().apply { moveTo(b[0].toFloat(), b[1].toFloat()) }
            builder.addStroke(
                android.accessibilityservice.GestureDescription
                    .StrokeDescription(path, s.startMs, duration),
            )
        }

        val gesture = try {
            builder.build()
        } catch (e: IllegalStateException) {
            return "时间线手势非法: ${e.message}"
        }
        val latch = CountDownLatch(1)
        var completed = false
        val ok = service.dispatchGesture(
            gesture,
            object : AccessibilityService.GestureResultCallback() {
                override fun onCompleted(description: android.accessibilityservice.GestureDescription?) {
                    completed = true
                    latch.countDown()
                }

                override fun onCancelled(description: android.accessibilityservice.GestureDescription?) {
                    Log.w(TAG, "时间线手势被取消")
                    latch.countDown()
                }
            },
            null,
        )
        if (!ok) return "dispatchGesture 返回 false（服务未就绪或手势非法）"
        latch.await(span + 2000, TimeUnit.MILLISECONDS)
        return if (completed) null else "时间线手势未完成（可能被真实触摸取消）"
    }

    /** 系统全局动作：BACK / HOME（对应 Python 侧 press "ESC" / press "HOME"） */
    fun globalBack(): Boolean = globalAction(AccessibilityService.GLOBAL_ACTION_BACK)

    fun globalHome(): Boolean = globalAction(AccessibilityService.GLOBAL_ACTION_HOME)

    private fun globalAction(action: Int): Boolean {
        val service = A11yService.instance ?: run {
            Log.w(TAG, "全局动作失败：无障碍服务未连接")
            return false
        }
        return service.performGlobalAction(action)
    }

    private fun dispatchAndWait(
        service: AccessibilityService,
        stroke: android.accessibilityservice.GestureDescription.StrokeDescription,
        durationMs: Long,
    ): Boolean {
        val gesture = android.accessibilityservice.GestureDescription.Builder()
            .addStroke(stroke)
            .build()
        val latch = CountDownLatch(1)
        var completed = false
        val ok = service.dispatchGesture(
            gesture,
            object : AccessibilityService.GestureResultCallback() {
                override fun onCompleted(description: android.accessibilityservice.GestureDescription?) {
                    completed = true
                    latch.countDown()
                }

                override fun onCancelled(description: android.accessibilityservice.GestureDescription?) {
                    Log.w(TAG, "手势被取消")
                    latch.countDown()
                }
            },
            null,
        )
        if (!ok) {
            Log.w(TAG, "dispatchGesture 返回 false（服务未就绪或手势非法）")
            return false
        }
        if (!latch.await(durationMs + 3000, TimeUnit.MILLISECONDS)) {
            Log.w(TAG, "等待手势回调超时")
            return false
        }
        return completed
    }

    /** 服务能力自检信息，出问题时用来确认配置 xml 里的 flag 真的生效了 */
    fun capabilities(): String {
        val service = A11yService.instance ?: return "服务未连接"
        val info: AccessibilityServiceInfo = service.serviceInfo ?: return "serviceInfo 为 null"
        return "flags=0x${Integer.toHexString(info.flags)} capabilities=0x${
            Integer.toHexString(info.capabilities)
        }"
    }
}
