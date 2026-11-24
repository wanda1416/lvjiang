package com.lvjiang.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.PixelFormat
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * CalibOverlay — 屏幕映射标定用的全屏透明覆盖层。
 *
 * 在"注入器**将要**点的像素"上画一个十字准星 + 圆环，外加 10% 网格和朝向/尺寸标头。
 * 覆盖层铺在注入器使用的同一套显示坐标系上（当前朝向、扩进挖孔区），所以
 * **准星位置 == 手势落点**：截一张图看准星压没压在目标上，截图空间和输入空间有没有偏移
 * 一眼就能看出来；PC 端还能直接在截图里找准星颜色，自动反推 ScreenMap 的仿射参数。
 *
 * 需要悬浮窗权限（和 FloatService 同一个），没授权时 show() 抛异常，由调用方转成错误返回。
 */
object CalibOverlay {

    /** 准星颜色：纯色洋红，游戏/应用界面里几乎不会大面积出现，PC 端按它找准星（calib.py MARK_BGR 同值） */
    const val MARK_COLOR = 0xFFFF00E5.toInt()

    /** 十字臂长与圆环半径（像素），PC 端检测窗口按它估 */
    const val ARM_PX = 60
    const val RING_PX = 40

    private val handler = Handler(Looper.getMainLooper())

    @Volatile
    private var view: OverlayView? = null

    private class OverlayView(c: Context) : View(c) {
        var markX = -1f
        var markY = -1f
        var label = ""
        private val cross = Paint().apply {
            color = MARK_COLOR; strokeWidth = 3f; isAntiAlias = false
        }
        private val ring = Paint().apply {
            color = MARK_COLOR; style = Paint.Style.STROKE; strokeWidth = 3f; isAntiAlias = false
        }
        private val grid = Paint().apply { color = 0x3300E5FF; strokeWidth = 1f }
        // 文字用白色：准星色只留给十字与圆环，PC 端按颜色找准星时不会被文字带偏
        private val text = Paint().apply { color = Color.WHITE; textSize = 34f; isAntiAlias = true }
        private val textBg = Paint().apply { color = 0xAA000000.toInt() }

        override fun onDraw(canvas: Canvas) {
            val w = width.toFloat()
            val h = height.toFloat()
            for (i in 1 until 10) {
                canvas.drawLine(w * i / 10, 0f, w * i / 10, h, grid)
                canvas.drawLine(0f, h * i / 10, w, h * i / 10, grid)
            }
            val orient = if (w >= h) "LANDSCAPE" else "PORTRAIT"
            val hdr = "$orient overlay=${width}x${height}"
            canvas.drawRect(8f, 8f, 8f + text.measureText(hdr) + 16f, 54f, textBg)
            canvas.drawText(hdr, 16f, 44f, text)
            if (markX >= 0) {
                canvas.drawLine(markX - ARM_PX, markY, markX + ARM_PX, markY, cross)
                canvas.drawLine(markX, markY - ARM_PX, markX, markY + ARM_PX, cross)
                canvas.drawCircle(markX, markY, RING_PX.toFloat(), ring)
                val lbl = label.ifEmpty { "%.0f,%.0f".format(markX, markY) }
                canvas.drawRect(
                    markX + RING_PX + 4, markY - 40,
                    markX + RING_PX + 4 + text.measureText(lbl) + 16, markY + 8, textBg,
                )
                canvas.drawText(lbl, markX + RING_PX + 12, markY - 6, text)
            }
        }
    }

    /** 覆盖层当前尺寸（宽, 高），未显示时 null；用来核对它真的铺满了全屏 */
    fun size(): IntArray? = view?.let { intArrayOf(it.width, it.height) }

    fun isShowing(): Boolean = view != null

    /**
     * 显示覆盖层（幂等）。在主线程 addView，同步等它完成，这样调用方返回后截图一定
     * 已经能看到网格；没有悬浮窗权限时 addView 抛 BadTokenException，原样抛给调用方。
     */
    fun show(context: Context) {
        if (view != null) return
        runOnMain {
            if (view != null) return@runOnMain
            val wm = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
            val v = OverlayView(context)
            val lp = WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                    WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT,
            ).apply {
                gravity = Gravity.TOP or Gravity.START
                // 扩进挖孔区，让覆盖层铺满整个真实显示（= 截图 + 输入空间），
                // 否则默认被挖孔 inset，尺寸比截图小一截，准星就对不上
                if (Build.VERSION.SDK_INT >= 28) {
                    layoutInDisplayCutoutMode =
                        WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
                }
                // Android 11+ 不再理会 FLAG_LAYOUT_NO_LIMITS 对系统栏的意思：不声明
                // fitInsetsTypes=0 的话窗口会被导航栏 inset（实测 1080x1920 的屏只给 1080x1794）
                if (Build.VERSION.SDK_INT >= 30) {
                    fitInsetsTypes = 0
                }
            }
            wm.addView(v, lp)
            view = v
        }
    }

    /** 在像素 (px, py) 画准星；同步等重绘请求入队，调用方再留一帧再截图即可 */
    fun mark(context: Context, px: Int, py: Int, label: String = "") {
        show(context)
        runOnMain {
            view?.let {
                it.markX = px.toFloat()
                it.markY = py.toFloat()
                it.label = label
                it.invalidate()
            }
        }
    }

    fun hide(context: Context) {
        runOnMain {
            view?.let {
                val wm = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
                try { wm.removeView(it) } catch (_: Exception) {}
            }
            view = null
        }
    }

    /** 在主线程执行并等完成，把主线程抛出的异常带回调用线程 */
    private fun runOnMain(block: () -> Unit) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            block()
            return
        }
        val latch = CountDownLatch(1)
        var error: Throwable? = null
        handler.post {
            try { block() } catch (e: Throwable) { error = e } finally { latch.countDown() }
        }
        if (!latch.await(3, TimeUnit.SECONDS)) throw IllegalStateException("覆盖层操作超时（主线程阻塞？）")
        error?.let { throw it }
    }
}
