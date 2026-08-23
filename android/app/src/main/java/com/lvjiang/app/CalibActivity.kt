package com.lvjiang.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.drawable.ColorDrawable
import android.os.Bundle
import android.util.TypedValue
import android.view.MotionEvent
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate
import androidx.appcompat.widget.AppCompatImageView
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors

/**
 * CalibActivity — 屏幕标定页：用参照大厅截图 + 几组地标对应点，算出本机的布局画布。
 *
 * 上图是随布局分发的参照截图（脚本作者机器上的大厅画面），下图是本机刚截的同一画面。
 * 用户在上图点一个地标（按钮、图标、文字），app 先用模板匹配在下图里找同一地标、
 * 找到就直接标上；找不到（或标偏了）用户在下图点一下纠正。凑够两组拉开距离的点，
 * 画布（游戏内容区在截图里的矩形）就解出来了，绿框画在下图上看一眼，对就保存——
 * 写进 layouts.yaml 的本地覆盖，这台机器上所有场景区域/点击一起跟着平移缩放。
 *
 * 截图手法：本页是透明主题叠在游戏上面的，截图前把内容藏起来、窗口底色设全透明，
 * 无障碍 takeScreenshot 拍到的就是底下的游戏画面；拍完再显示回来。用户不用来回切应用。
 *
 * 算法全在 Python 侧（core/screen_calib.py，PC 与设备共用），这里只画图、收点、调接口。
 */
class CalibActivity : AppCompatActivity() {

    private val executor = Executors.newSingleThreadExecutor()

    private lateinit var root: LinearLayout
    private lateinit var statusText: TextView
    private lateinit var hintText: TextView
    private lateinit var refView: MarkerImageView
    private lateinit var liveView: MarkerImageView
    private lateinit var resultText: TextView
    private lateinit var saveButton: Button

    /** 对应点：参照图像素 ↔ 实时图像素（live 为 null 表示还等用户在下图点） */
    private data class Pair2(val refX: Float, val refY: Float, var liveX: Float? = null, var liveY: Float? = null, var auto: Boolean = false)

    private val pairs = mutableListOf<Pair2>()
    private var solved: JSONObject? = null
    private var layoutName = ""
    private var refPath: String? = null
    private var livePath: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        delegate.localNightMode = AppCompatDelegate.MODE_NIGHT_YES
        super.onCreate(savedInstanceState)
        buildUi()
        // 从悬浮面板进来时游戏在底下：先藏着截一张再显示
        if (intent.getBooleanExtra(EXTRA_CAPTURE, false)) {
            captureHidden()
        } else {
            reload()
        }
    }

    override fun onDestroy() {
        executor.shutdown()
        super.onDestroy()
    }

    // ── UI ────────────────────────────────────────────────

    private fun buildUi() {
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(12), dp(12), dp(12))
        }
        statusText = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            setTextColor(Color.WHITE)
            text = "屏幕标定"
        }
        hintText = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            setTextColor(0xFFAAAAAA.toInt())
        }
        refView = MarkerImageView(this).apply {
            caption = "参照图（脚本作者机器上的大厅）"
            onTap = { x, y -> onRefTap(x, y) }
        }
        liveView = MarkerImageView(this).apply {
            caption = "本机画面"
            onTap = { x, y -> onLiveTap(x, y) }
        }
        resultText = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            setTextColor(0xFF80CBC4.toInt())
        }
        val buttons1 = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        buttons1.addView(smallButton("重新截图") { captureHidden() })
        buttons1.addView(smallButton("撤销一点") { undo() })
        saveButton = smallButton("保存画布") { save() }
        buttons1.addView(saveButton)
        val buttons2 = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        buttons2.addView(smallButton("恢复默认") { reset() })
        buttons2.addView(smallButton("本机画面设为参照图") { useLiveAsReference() })
        buttons2.addView(smallButton("关闭") { finish() })

        root.addView(statusText)
        root.addView(hintText)
        root.addView(refView, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        root.addView(liveView, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        root.addView(resultText)
        root.addView(buttons1)
        root.addView(buttons2)
        setContentView(root)
    }

    private fun smallButton(label: String, onClick: () -> Unit): Button = Button(this).apply {
        text = label
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
        isAllCaps = false
        layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        setOnClickListener { onClick() }
    }

    private fun dp(value: Int): Int =
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value.toFloat(), resources.displayMetrics).toInt()

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    // ── 数据加载 ──────────────────────────────────────────

    private fun reload() {
        executor.execute {
            val info = PyBridge.calib(this, "calib_info")
            val refBmp = info.optString("ref_path").takeIf { it.isNotEmpty() && info.isNull("ref_path").not() }?.let { decode(it) }
            val liveBmp = info.optString("live_path").takeIf { it.isNotEmpty() && info.isNull("live_path").not() }?.let { decode(it) }
            runOnUiThread {
                if (!info.optBoolean("ok")) {
                    statusText.text = "加载失败：${info.optString("error")}"
                    return@runOnUiThread
                }
                layoutName = info.optString("layout")
                refPath = info.optString("ref_path").takeIf { refBmp != null }
                livePath = info.optString("live_path").takeIf { liveBmp != null }
                refView.setBitmap(refBmp)
                liveView.setBitmap(liveBmp)
                val canvas = info.optJSONObject("canvas")
                statusText.text = "屏幕标定 · 布局「$layoutName」 · 当前画布 ${fmtCanvas(canvas)}"
                hintText.text = when {
                    refBmp == null ->
                        "这个布局还没有参照图。游戏停在大厅时点「重新截图」，再点「本机画面设为参照图」（脚本作者在自己机器上做一次即可）"
                    liveBmp == null -> "游戏停在和参照图相同的画面，点「重新截图」"
                    else -> "在上图点一个地标（按钮/图标），app 会在下图找同一地标并标上；找错了就在下图点一下纠正。两组拉开距离的点即可解出画布"
                }
                drawMarkers()
            }
        }
    }

    private fun decode(path: String): Bitmap? = try {
        BitmapFactory.decodeFile(path)
    } catch (e: Throwable) {
        null
    }

    private fun fmtCanvas(c: JSONObject?): String {
        if (c == null) return "?"
        return "x=%.3f y=%.3f w=%.3f h=%.3f".format(
            c.optDouble("x_ratio"), c.optDouble("y_ratio"), c.optDouble("w_ratio"), c.optDouble("h_ratio"))
    }

    // ── 截图（藏起来拍底下的游戏） ──────────────────────────

    private fun captureHidden() {
        root.visibility = View.INVISIBLE
        FloatService.setIconHidden(true)
        val oldBg = window.decorView.background
        window.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
        root.postDelayed({
            executor.execute {
                val r = PyBridge.calib(this, "calib_capture")
                runOnUiThread {
                    window.setBackgroundDrawable(oldBg ?: ColorDrawable(0xCC000000.toInt()))
                    root.visibility = View.VISIBLE
                    FloatService.setIconHidden(false)
                    if (!r.optBoolean("ok")) {
                        toast("截图失败：${r.optString("error")}")
                    } else {
                        pairs.clear()
                        solved = null
                        resultText.text = ""
                    }
                    reload()
                }
            }
        }, 450)
    }

    // ── 收点 ──────────────────────────────────────────────

    private fun onRefTap(x: Float, y: Float) {
        if (livePath == null) {
            toast("先点「重新截图」拿到本机画面")
            return
        }
        // 上一组还没配上就换成这一组（用户改主意了）
        pairs.removeAll { it.liveX == null }
        val p = Pair2(x, y)
        pairs.add(p)
        drawMarkers()
        resultText.text = "在下图自动找地标 ${pairs.size}…"
        executor.execute {
            val r = PyBridge.calib(this, "calib_locate", x.toDouble(), y.toDouble())
            runOnUiThread {
                if (p !in pairs) return@runOnUiThread
                if (r.optBoolean("ok") && r.optBoolean("found")) {
                    p.liveX = r.optDouble("x").toFloat()
                    p.liveY = r.optDouble("y").toFloat()
                    p.auto = true
                    resultText.text = "地标 ${pairs.size} 自动定位（相似度 %.2f）；不对就在下图点一下纠正".format(r.optDouble("score"))
                    drawMarkers()
                    solve()
                } else {
                    resultText.text = "下图没找到地标 ${pairs.size}，请在下图点同一位置"
                }
            }
        }
    }

    private fun onLiveTap(x: Float, y: Float) {
        val p = pairs.lastOrNull()
        if (p == null) {
            toast("先在上图点一个地标")
            return
        }
        p.liveX = x
        p.liveY = y
        p.auto = false
        drawMarkers()
        solve()
    }

    private fun undo() {
        if (pairs.isEmpty()) return
        pairs.removeAt(pairs.size - 1)
        solved = null
        drawMarkers()
        if (pairs.any { it.liveX != null }) solve() else {
            liveView.rect = null
            resultText.text = ""
        }
    }

    private fun drawMarkers() {
        refView.markers = pairs.mapIndexed { i, p -> MarkerImageView.Marker(p.refX, p.refY, "${i + 1}", MARK_REF) }
        liveView.markers = pairs.mapIndexedNotNull { i, p ->
            val lx = p.liveX ?: return@mapIndexedNotNull null
            val ly = p.liveY ?: return@mapIndexedNotNull null
            MarkerImageView.Marker(lx, ly, "${i + 1}${if (p.auto) "*" else ""}", if (p.auto) MARK_AUTO else MARK_MANUAL)
        }
    }

    // ── 求解 / 保存 ───────────────────────────────────────

    private fun solve() {
        val complete = pairs.filter { it.liveX != null && it.liveY != null }
        if (complete.isEmpty()) return
        val arr = JSONArray()
        for (p in complete) {
            arr.put(JSONObject().put("ref_x", p.refX).put("ref_y", p.refY).put("live_x", p.liveX!!).put("live_y", p.liveY!!))
        }
        executor.execute {
            val r = PyBridge.calib(this, "calib_solve", arr.toString())
            runOnUiThread {
                if (!r.optBoolean("ok")) {
                    solved = null
                    liveView.rect = null
                    resultText.text = "解算失败：${r.optString("error")}"
                    return@runOnUiThread
                }
                solved = r.optJSONObject("canvas")
                val rect = r.optJSONArray("rect_live")
                if (rect != null && rect.length() == 4) {
                    liveView.rect = RectF(
                        rect.getInt(0).toFloat(), rect.getInt(1).toFloat(),
                        (rect.getInt(0) + rect.getInt(2)).toFloat(), (rect.getInt(1) + rect.getInt(3)).toFloat(),
                    )
                }
                val axes = when {
                    r.optBoolean("scaled_x") && r.optBoolean("scaled_y") -> "缩放+平移"
                    r.optBoolean("scaled_x") || r.optBoolean("scaled_y") -> "一轴缩放、一轴仅平移（再点一个对角的地标更准）"
                    else -> "仅平移（再点一个对角的地标才能解缩放）"
                }
                resultText.text = "画布 ${fmtCanvas(solved)} · ${complete.size} 组点 · 残差 %.1fpx · $axes\n绿框 = 解出的游戏内容区，核对后点「保存画布」"
                    .format(r.optDouble("residual_px"))
            }
        }
    }

    private fun save() {
        val c = solved ?: run { toast("还没有解出画布"); return }
        executor.execute {
            val r = PyBridge.calib(this, "calib_save", c.toString())
            runOnUiThread {
                toast(if (r.optBoolean("ok")) "已保存到布局「${r.optString("layout")}」" else "保存失败：${r.optString("error")}")
                reload()
            }
        }
    }

    private fun reset() {
        executor.execute {
            val r = PyBridge.calib(this, "calib_reset")
            runOnUiThread {
                toast(if (r.optBoolean("ok")) "已恢复系统默认画布" else "失败：${r.optString("error")}")
                pairs.clear()
                solved = null
                liveView.rect = null
                resultText.text = ""
                reload()
            }
        }
    }

    private fun useLiveAsReference() {
        if (livePath == null) { toast("先点「重新截图」"); return }
        executor.execute {
            val r = PyBridge.calib(this, "calib_use_live_as_reference")
            runOnUiThread {
                toast(if (r.optBoolean("ok")) "本机画面已设为参照图" else "失败：${r.optString("error")}")
                pairs.clear()
                solved = null
                liveView.rect = null
                reload()
            }
        }
    }

    // ── 带标记的图片视图 ──────────────────────────────────

    /** FIT_CENTER 显示一张图，在图像像素坐标上画编号标记 / 矩形，点击回调图像像素坐标 */
    class MarkerImageView(context: Context) : AppCompatImageView(context) {
        data class Marker(val x: Float, val y: Float, val label: String, val color: Int)

        var caption = ""
        var markers: List<Marker> = emptyList()
            set(v) { field = v; invalidate() }
        var rect: RectF? = null
            set(v) { field = v; invalidate() }
        var onTap: ((Float, Float) -> Unit)? = null

        private val ring = Paint().apply { style = Paint.Style.STROKE; strokeWidth = 4f; isAntiAlias = true }
        private val cross = Paint().apply { strokeWidth = 2f; isAntiAlias = true }
        private val text = Paint().apply { color = Color.WHITE; textSize = 30f; isAntiAlias = true; isFakeBoldText = true }
        private val textBg = Paint().apply { color = 0xAA000000.toInt() }
        private val rectPaint = Paint().apply { color = 0xFF69F0AE.toInt(); style = Paint.Style.STROKE; strokeWidth = 4f }
        private val placeholder = Paint().apply { color = 0xFF777777.toInt(); textSize = 34f; isAntiAlias = true }
        private var downX = 0f
        private var downY = 0f

        init {
            scaleType = ScaleType.FIT_CENTER
            setBackgroundColor(0xFF111111.toInt())
            adjustViewBounds = false
        }

        fun setBitmap(bmp: Bitmap?) {
            setImageBitmap(bmp)
            invalidate()
        }

        override fun onTouchEvent(event: MotionEvent): Boolean {
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> { downX = event.x; downY = event.y; return true }
                MotionEvent.ACTION_UP -> {
                    if (Math.abs(event.x - downX) < 20 && Math.abs(event.y - downY) < 20) {
                        toImage(event.x, event.y)?.let { (ix, iy) -> onTap?.invoke(ix, iy) }
                    }
                    return true
                }
            }
            return super.onTouchEvent(event)
        }

        /** 视图坐标 → 图像像素；落在图外返回 null */
        private fun toImage(vx: Float, vy: Float): kotlin.Pair<Float, Float>? {
            val d = drawable ?: return null
            val inv = Matrix()
            if (!imageMatrix.invert(inv)) return null
            val pts = floatArrayOf(vx - paddingLeft, vy - paddingTop)
            inv.mapPoints(pts)
            if (pts[0] < 0 || pts[1] < 0 || pts[0] >= d.intrinsicWidth || pts[1] >= d.intrinsicHeight) return null
            return pts[0] to pts[1]
        }

        private fun toView(ix: Float, iy: Float): FloatArray {
            val pts = floatArrayOf(ix, iy)
            imageMatrix.mapPoints(pts)
            pts[0] += paddingLeft
            pts[1] += paddingTop
            return pts
        }

        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            if (drawable == null) {
                canvas.drawText("（$caption：暂无）", 24f, height / 2f, placeholder)
                return
            }
            rect?.let {
                val tl = toView(it.left, it.top)
                val br = toView(it.right, it.bottom)
                canvas.drawRect(tl[0], tl[1], br[0], br[1], rectPaint)
            }
            for (m in markers) {
                val p = toView(m.x, m.y)
                ring.color = m.color
                cross.color = m.color
                canvas.drawCircle(p[0], p[1], 22f, ring)
                canvas.drawLine(p[0] - 34, p[1], p[0] + 34, p[1], cross)
                canvas.drawLine(p[0], p[1] - 34, p[0], p[1] + 34, cross)
                val w = text.measureText(m.label)
                canvas.drawRect(p[0] + 26, p[1] - 34, p[0] + 26 + w + 12, p[1] - 2, textBg)
                canvas.drawText(m.label, p[0] + 32, p[1] - 8, text)
            }
            canvas.drawRect(0f, 0f, text.measureText(caption) + 16, 40f, textBg)
            canvas.drawText(caption, 8f, 30f, text)
        }
    }

    companion object {
        const val EXTRA_CAPTURE = "capture"
        private const val MARK_REF = 0xFFFFD740.toInt()
        private const val MARK_AUTO = 0xFF40C4FF.toInt()
        private const val MARK_MANUAL = 0xFFFF6E40.toInt()
    }
}
