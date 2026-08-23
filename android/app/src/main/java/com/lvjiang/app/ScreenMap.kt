package com.lvjiang.app

import android.content.Context
import android.os.Build
import android.util.DisplayMetrics
import android.util.Log
import android.view.Surface
import android.view.WindowManager
import org.json.JSONObject
import java.io.File

/**
 * ScreenMap — 截图坐标 → 输入坐标的设备级映射（屏幕映射）。
 *
 * 上层（PC 端识别、设备端工作流）在**截图**上量出的像素坐标，下发给 dispatchGesture /
 * `input tap` 时默认假设截图空间 == 输入空间。绝大多数设备确实如此：takeScreenshot /
 * screencap、真实显示、无障碍手势三者是同一张全屏像素网格，映射为恒等。但有的设备会偏：
 * 挖孔/刘海处理方式不同、截图被缩放、应用区有黑边——这时按截图坐标点下去就落偏。
 *
 * 这里按机型 + 当前朝向分辨率存一份逐轴仿射：
 *
 *   input_pct = screenshot_pct * scale + offset
 *
 * 文件 `filesDir/lvjiang/calib/<型号_WxH>.json`，横竖屏各一条（key 带 WxH），无文件即恒等。
 * 映射在注入口统一施加（A11yBridge / ShellBridge 的手势方法），因此 PC 代理通道和设备端
 * Python 通道都自动生效；标定由 PC 经 AgentServer 的 calib_* op 完成（配合 CalibOverlay
 * 把"将要点的位置"画到屏幕上，再截图比对）。
 */
class ScreenMap(
    val sx: Double = 1.0,
    val ox: Double = 0.0,
    val sy: Double = 1.0,
    val oy: Double = 0.0,
) {
    val isIdentity: Boolean
        get() = sx == 1.0 && ox == 0.0 && sy == 1.0 && oy == 0.0

    fun mapX(pct: Double): Double = (pct * sx + ox).coerceIn(0.0, 1.0)
    fun mapY(pct: Double): Double = (pct * sy + oy).coerceIn(0.0, 1.0)

    fun toJson(): JSONObject = JSONObject()
        .put("sx", sx).put("ox", ox).put("sy", sy).put("oy", oy)

    companion object {
        private const val TAG = "ScreenMap"

        @Volatile
        private var appContext: Context? = null

        /** 按 key 缓存已加载的映射，免得每次手势都读文件；save/clear 时失效 */
        @Volatile
        private var cached: Pair<String, ScreenMap>? = null

        fun init(context: Context) {
            appContext = context.applicationContext
        }

        fun fromJson(j: JSONObject): ScreenMap = ScreenMap(
            j.optDouble("sx", 1.0), j.optDouble("ox", 0.0),
            j.optDouble("sy", 1.0), j.optDouble("oy", 0.0),
        )

        fun dir(): File = File(requireContext().filesDir, "lvjiang/calib")

        /** 标定文件名：机型 + 带朝向的分辨率（空格 / 斜杠换成下划线） */
        fun key(w: Int, h: Int): String =
            "${Build.MODEL}_${w}x${h}".replace(' ', '_').replace('/', '_')

        fun file(w: Int, h: Int): File = File(dir(), "${key(w, h)}.json")

        /**
         * 当前朝向下的真实屏幕尺寸（含挖孔区，和 takeScreenshot / screencap 的尺寸一致）。
         *
         * 后台进程查 getRealMetrics 在部分机型上拿到的是自然朝向（竖屏）尺寸，而游戏强制横屏时
         * 截图是横的；所以按 display.rotation 把宽高摆正，否则百分比换算出的 y 会超出屏幕。
         */
        fun screenSize(): Pair<Int, Int> {
            val wm = requireContext().getSystemService(Context.WINDOW_SERVICE) as WindowManager
            @Suppress("DEPRECATION")
            val display = wm.defaultDisplay
            val m = DisplayMetrics()
            @Suppress("DEPRECATION")
            display.getRealMetrics(m)
            var w = m.widthPixels
            var h = m.heightPixels
            val landscape = isLandscape(rotation())
            if (landscape && h > w) { val t = w; w = h; h = t }
            if (!landscape && w > h) { val t = w; w = h; h = t }
            return w to h
        }

        fun rotation(): Int {
            val wm = requireContext().getSystemService(Context.WINDOW_SERVICE) as WindowManager
            @Suppress("DEPRECATION")
            return wm.defaultDisplay.rotation
        }

        private fun isLandscape(rotation: Int) =
            rotation == Surface.ROTATION_90 || rotation == Surface.ROTATION_270

        /** 当前朝向分辨率对应的映射；无文件 / 文件坏了 → 恒等 */
        fun current(): ScreenMap {
            val (w, h) = screenSize()
            val k = key(w, h)
            cached?.let { if (it.first == k) return it.second }
            val loaded = load(w, h)
            cached = k to loaded
            return loaded
        }

        fun load(w: Int, h: Int): ScreenMap {
            val f = file(w, h)
            if (!f.exists()) return ScreenMap()
            return try {
                fromJson(JSONObject(f.readText()))
            } catch (e: Exception) {
                Log.w(TAG, "标定文件损坏，按恒等处理: ${f.name}", e)
                ScreenMap()
            }
        }

        /** 保存；恒等映射等价于删文件，不留一份"全是 1 和 0"的冗余配置 */
        fun save(w: Int, h: Int, map: ScreenMap) {
            val f = file(w, h)
            if (map.isIdentity) {
                if (f.exists()) f.delete()
            } else {
                f.parentFile?.mkdirs()
                f.writeText(map.toJson().toString())
            }
            cached = null
            Log.i(TAG, "标定已保存 ${f.name}: ${if (map.isIdentity) "恒等（文件已删）" else map.toJson()}")
        }

        /** 截图像素坐标 → 输入像素坐标；恒等时原样返回（绝大多数设备走这条快路径） */
        fun mapPoint(x: Int, y: Int): IntArray {
            val map = current()
            if (map.isIdentity) return intArrayOf(x, y)
            val (w, h) = screenSize()
            val mx = (map.mapX(x.toDouble() / w) * w).toInt()
            val my = (map.mapY(y.toDouble() / h) * h).toInt()
            return intArrayOf(mx, my)
        }

        private fun requireContext(): Context =
            appContext ?: A11yService.instance?.applicationContext
                ?: throw IllegalStateException("ScreenMap 未初始化（App.onCreate 未执行）")
    }
}
