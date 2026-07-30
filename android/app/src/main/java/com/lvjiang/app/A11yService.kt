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
 *   - dispatchGesture() 手势注入（点击/滑动）
 * 因此 accessibilityEventTypes 配成 none，不产生任何事件回调开销。
 */
class A11yService : AccessibilityService() {

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        Log.i(TAG, "无障碍服务已连接")
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

    /** 单点点击；durationMs 是按住时长 */
    fun tap(x: Int, y: Int, durationMs: Long = 50): Boolean {
        val path = Path().apply { moveTo(x.toFloat(), y.toFloat()) }
        return dispatch(path, 0, durationMs)
    }

    /** 直线滑动 */
    fun swipe(x1: Int, y1: Int, x2: Int, y2: Int, durationMs: Long): Boolean {
        val path = Path().apply {
            moveTo(x1.toFloat(), y1.toFloat())
            lineTo(x2.toFloat(), y2.toFloat())
        }
        return dispatch(path, 0, durationMs)
    }

    /**
     * 长按：dispatchGesture 单个 stroke 的时长上限约 1 分钟，实际业务里
     * 长按都是几百毫秒到几秒，直接用 stroke 时长表达即可。
     */
    fun longPress(x: Int, y: Int, durationMs: Long): Boolean {
        val path = Path().apply { moveTo(x.toFloat(), y.toFloat()) }
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

    /** 服务能力自检信息，出问题时用来确认配置 xml 里的 flag 真的生效了 */
    fun capabilities(): String {
        val service = A11yService.instance ?: return "服务未连接"
        val info: AccessibilityServiceInfo = service.serviceInfo ?: return "serviceInfo 为 null"
        return "flags=0x${Integer.toHexString(info.flags)} capabilities=0x${
            Integer.toHexString(info.capabilities)
        }"
    }
}
