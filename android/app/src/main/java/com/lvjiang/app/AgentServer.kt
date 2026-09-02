package com.lvjiang.app

import android.net.LocalServerSocket
import android.net.LocalSocket
import android.os.Build
import android.os.Process
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.EOFException
import java.io.IOException
import java.util.concurrent.atomic.AtomicInteger

/**
 * AgentServer — 把设备端的无障碍 / Shizuku 能力借给 PC 端用的代理通道。
 *
 * PC 端（src/lvjiang/core/android/agent.py）经 `adb forward tcp:<port> localabstract:lvjiang-agent`
 * 连进来，之后截图走 takeScreenshot、点击/拖拽/推住走 dispatchGesture、BACK/HOME 走
 * performGlobalAction —— 替代 PC 端原先的 `adb shell input tap/swipe` 与 `screencap -p`：
 * 手势是同步落地的（回调后才返回），推住不放能真正停住，而 `input swipe` 两者都做不到。
 *
 * 线协议（PC 与这里各一份实现，改一边必须同步改另一边）：
 *   请求：4 字节大端长度 + UTF-8 JSON `{"op": "...", ...}`
 *   响应：4 字节大端长度 + UTF-8 JSON 头；若头里有 `"bin": N`，后面紧跟 N 字节二进制负载
 * 同一连接上请求串行，一问一答；服务端对所有 op 加全局锁，跨连接也串行。
 *
 * 安全：abstract 命名空间的 socket 设备上任何进程都能连，所以只接受 adbd（shell uid 2000 /
 * root）和本进程自己的连接，其余直接断开 —— 否则任意 app 都能往屏幕上注入手势。
 *
 * 生命周期跟着进程走：App.onCreate 启动，进程死则随之消失。无障碍开关打开时系统会
 * 常驻绑定本进程，因此对用户来说"开了无障碍 = 代理在线"，不需要额外的开关。
 */
object AgentServer {

    const val SOCKET_NAME = "lvjiang-agent"
    const val PROTOCOL_VERSION = 2

    private const val TAG = "AgentServer"

    /** 请求体上限：正常请求只有几十字节，超出一律视为异常连接 */
    private const val MAX_REQUEST_BYTES = 1 shl 20

    private const val UID_ROOT = 0
    private const val UID_SHELL = 2000

    /** PNG 文件头，用于判定 Shizuku screencap 回的是图还是错误文本 */
    private val PNG_MAGIC = byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)

    @Volatile
    private var server: LocalServerSocket? = null

    private val activeClients = AtomicInteger(0)

    @Volatile
    private var connectedSinceMs = 0L

    @Volatile
    private var lastOp = ""

    @Volatile
    private var lastOpOk = false

    @Volatile
    private var lastOpAtMs = 0L

    /** 应用 Context（App.onCreate 登记），标定覆盖层要用；无障碍服务在时优先用它的 */
    @Volatile
    var appContext: android.content.Context? = null

    /** 所有 op 串行：手势与截图本来就不该并发，省掉各桥接对象的并发考虑 */
    private val dispatchLock = Any()

    fun isRunning(): Boolean = server != null

    fun activeConnectionCount(): Int = activeClients.get()

    fun isPcConnected(): Boolean = activeConnectionCount() > 0

    fun lastCommandSummary(): String = when {
        lastOp.isEmpty() -> "尚无指令"
        lastOpOk -> "$lastOp 成功"
        else -> "$lastOp 失败"
    }

    /** 幂等启动；socket 名被占用（极少见）只记日志，不影响 app 其它功能 */
    @Synchronized
    fun start() {
        if (server != null) return
        val s = try {
            LocalServerSocket(SOCKET_NAME)
        } catch (e: IOException) {
            Log.e(TAG, "监听 $SOCKET_NAME 失败", e)
            return
        }
        server = s
        Thread({ acceptLoop(s) }, "lvjiang-agent-accept").apply {
            isDaemon = true
            start()
        }
        Log.i(TAG, "代理通道已监听 localabstract:$SOCKET_NAME")
    }

    @Synchronized
    fun stop() {
        val s = server ?: return
        server = null
        try {
            s.close()
        } catch (_: IOException) {
        }
        Log.i(TAG, "代理通道已关闭")
    }

    private fun acceptLoop(s: LocalServerSocket) {
        while (server === s) {
            val client = try {
                s.accept()
            } catch (e: IOException) {
                if (server === s) Log.w(TAG, "accept 失败", e)
                break
            }
            Thread({ serve(client) }, "lvjiang-agent-conn").apply {
                isDaemon = true
                start()
            }
        }
    }

    private fun serve(client: LocalSocket) {
        var counted = false
        try {
            val uid = try {
                client.peerCredentials.uid
            } catch (e: IOException) {
                -1
            }
            if (uid != UID_ROOT && uid != UID_SHELL && uid != Process.myUid()) {
                Log.w(TAG, "拒绝来自 uid=$uid 的连接（只接受 adb / 本进程）")
                return
            }
            val count = activeClients.incrementAndGet()
            counted = true
            if (count == 1) {
                connectedSinceMs = System.currentTimeMillis()
                lastOp = ""
                lastOpOk = false
                lastOpAtMs = 0L
            }
            // PC 控制期间隐藏悬浮球并收起面板，避免进入 screencap/scrcpy 截图，
            // 也避免用户从悬浮面板并行发起另一套操作。
            FloatService.setPcConnected(true)
            val input = DataInputStream(client.inputStream.buffered())
            val output = DataOutputStream(client.outputStream.buffered())
            while (true) {
                val len = try {
                    input.readInt()
                } catch (_: EOFException) {
                    return
                }
                if (len <= 0 || len > MAX_REQUEST_BYTES) {
                    Log.w(TAG, "请求长度非法 $len，断开")
                    return
                }
                val buf = ByteArray(len)
                input.readFully(buf)
                val (header, payload) = handle(String(buf, Charsets.UTF_8))
                writeResponse(output, header, payload)
            }
        } catch (e: IOException) {
            Log.d(TAG, "连接结束: ${e.message}")
        } finally {
            if (counted) {
                val remaining = activeClients.decrementAndGet().coerceAtLeast(0)
                if (remaining == 0) {
                    connectedSinceMs = 0L
                    FloatService.setPcConnected(false)
                }
            }
            try {
                client.close()
            } catch (_: IOException) {
            }
        }
    }

    private fun writeResponse(out: DataOutputStream, header: JSONObject, payload: ByteArray?) {
        if (payload != null) header.put("bin", payload.size)
        val bytes = header.toString().toByteArray(Charsets.UTF_8)
        out.writeInt(bytes.size)
        out.write(bytes)
        if (payload != null) out.write(payload)
        out.flush()
    }

    // ─── 请求分发 ───────────────────────────────────────────

    /**
     * 处理一条请求，返回 (JSON 头, 二进制负载或 null)。
     * 与 socket 无关，便于自检里直接调用。任何异常都折叠成 ok=false，连接不会因此断掉。
     */
    fun handle(requestJson: String): Pair<JSONObject, ByteArray?> {
        val req = try {
            JSONObject(requestJson)
        } catch (e: Exception) {
            return fail("请求不是合法 JSON: ${e.message}")
        }
        val op = req.optString("op", "")
        val result = try {
            synchronized(dispatchLock) { dispatch(op, req) }
        } catch (e: Throwable) {
            Log.e(TAG, "op=$op 执行异常", e)
            fail("$op 执行异常: $e")
        }
        if (op.isNotEmpty() && op != "ping" && op != "status") {
            lastOp = op
            lastOpOk = result.first.optBoolean("ok", false)
            lastOpAtMs = System.currentTimeMillis()
        }
        return result
    }

    private fun dispatch(op: String, req: JSONObject): Pair<JSONObject, ByteArray?> = when (op) {
        "ping", "status" -> ok(status())
        "screenshot" -> screenshot(req)
        "tap" -> tap(req)
        "long_press" -> longPress(req)
        "swipe" -> swipe(req)
        "hold_move" -> holdMove(req)
        "key" -> key(req)
        "shell" -> shell(req)
        "calib_get" -> ok(calibInfo())
        "calib_set" -> calibSet(req)
        "calib_clear" -> calibClear()
        "calib_mark" -> calibMark(req)
        "calib_hide" -> calibHide()
        "float_icon" -> floatIcon(req)
        else -> fail("未知 op: $op")
    }

    private fun status(): JSONObject = JSONObject().apply {
        put("protocol", PROTOCOL_VERSION)
        put("app", BuildConfig.VERSION_NAME)
        put("sdk", Build.VERSION.SDK_INT)
        put("a11y", A11yBridge.isReady())
        put("shizuku", ShellBridge.isShizukuAlive())
        put("shizuku_granted", ShellBridge.hasPermission())
        // 屏幕映射：非恒等时 PC 端在连接日志里提示一句，免得用户不知道点击坐标被改写过
        put("calib_identity", ScreenMap.current().isIdentity)
        put("screen", screenInfo())
        put("pc_connected", isPcConnected())
        put("pc_connections", activeConnectionCount())
        put("pc_connected_since_ms", connectedSinceMs)
        put("last_op", lastOp)
        put("last_op_ok", lastOpOk)
        put("last_op_at_ms", lastOpAtMs)
    }

    // ─── 屏幕映射标定（截图坐标 → 输入坐标，见 ScreenMap / CalibOverlay）──────

    private fun screenInfo(): JSONObject {
        val (w, h) = ScreenMap.screenSize()
        return JSONObject().put("w", w).put("h", h).put("rotation", ScreenMap.rotation())
    }

    private fun calibInfo(): JSONObject {
        val (w, h) = ScreenMap.screenSize()
        val map = ScreenMap.current()
        return JSONObject()
            .put("key", ScreenMap.key(w, h))
            .put("screen", screenInfo())
            .put("calib", map.toJson())
            .put("identity", map.isIdentity)
            .put("stored", ScreenMap.file(w, h).exists())
            .put("overlay", CalibOverlay.size()?.let { JSONObject().put("w", it[0]).put("h", it[1]) } ?: JSONObject.NULL)
    }

    /** 保存当前朝向分辨率的仿射参数；缺省轴按恒等 */
    private fun calibSet(req: JSONObject): Pair<JSONObject, ByteArray?> {
        val map = ScreenMap(
            req.optDouble("sx", 1.0), req.optDouble("ox", 0.0),
            req.optDouble("sy", 1.0), req.optDouble("oy", 0.0),
        )
        if (listOf(map.sx, map.ox, map.sy, map.oy).any { it.isNaN() || it.isInfinite() }) {
            return fail("calib_set 参数必须是有限数")
        }
        if (map.sx <= 0 || map.sy <= 0) return fail("calib_set 缩放必须为正")
        val (w, h) = ScreenMap.screenSize()
        ScreenMap.save(w, h, map)
        return ok(calibInfo())
    }

    private fun calibClear(): Pair<JSONObject, ByteArray?> {
        val (w, h) = ScreenMap.screenSize()
        ScreenMap.save(w, h, ScreenMap())
        return ok(calibInfo())
    }

    /**
     * 在"(x, y) 经映射后将落到的像素"上画准星；`tap=true` 时再在同一位置点一下
     * （验证准星 == 手势落点）。需要悬浮窗权限，没授权时报错而不是静默。
     */
    private fun calibMark(req: JSONObject): Pair<JSONObject, ByteArray?> {
        val x = req.getInt("x")
        val y = req.getInt("y")
        val p = ScreenMap.mapPoint(x, y)
        val ctx = A11yService.instance?.applicationContext ?: appContext
            ?: return fail("拿不到 Context（进程未初始化）")
        try {
            CalibOverlay.mark(ctx, p[0], p[1], "$x,$y -> ${p[0]},${p[1]}")
        } catch (e: Throwable) {
            return fail("覆盖层显示失败（悬浮窗权限未授予？）: $e")
        }
        val header = calibInfo().put("px", JSONObject().put("x", p[0]).put("y", p[1]))
        if (!req.optBoolean("tap", false)) return ok(header)
        return withVia(req) { via ->
            // 走各桥接的 tap：它们内部会再做一次同样的映射，因此传原始 (x, y)
            val done = if (via == "a11y") A11yBridge.tap(x, y, req.optLong("duration_ms", 50))
            else ShellBridge.tap(x, y).isEmpty()
            if (done) ok(header.put("via", via)) else fail("标定点击未成功")
        }
    }

    private fun calibHide(): Pair<JSONObject, ByteArray?> {
        val ctx = A11yService.instance?.applicationContext ?: appContext
            ?: return fail("拿不到 Context（进程未初始化）")
        CalibOverlay.hide(ctx)
        return ok(JSONObject())
    }

    /**
     * 动态显隐悬浮球（截图 / 标定时把它藏掉，免得被截进画面）。
     * hidden 缺省 true；悬浮服务没在跑时 running=false，静默即可（本来就没图标）。
     */
    private fun floatIcon(req: JSONObject): Pair<JSONObject, ByteArray?> {
        val hidden = req.optBoolean("hidden", true)
        val running = FloatService.setIconHidden(hidden)
        return ok(JSONObject().put("running", running).put("hidden", hidden))
    }

    // ─── 通道选择 ───────────────────────────────────────────

    /**
     * 解析请求指定的通道：
     *   "a11y"  必须走无障碍；"shell" 必须走 Shizuku；
     *   "auto"（缺省）无障碍可用优先无障碍，否则 Shizuku，两者都没有报错。
     * 返回 null 表示不可用，错误文本已写进 err。
     */
    private fun resolveVia(req: JSONObject, err: StringBuilder): String? {
        val a11y = A11yBridge.isReady()
        val shell = ShellBridge.hasPermission()
        return when (val via = req.optString("via", "auto")) {
            "a11y" -> if (a11y) via else null.also { err.append("无障碍服务未连接") }
            "shell" -> if (shell) via else null.also { err.append("Shizuku 未激活或未授权") }
            else -> when {
                a11y -> "a11y"
                shell -> "shell"
                else -> null.also { err.append("无障碍服务未连接，且 Shizuku 未授权") }
            }
        }
    }

    private inline fun withVia(
        req: JSONObject,
        block: (via: String) -> Pair<JSONObject, ByteArray?>,
    ): Pair<JSONObject, ByteArray?> {
        val err = StringBuilder()
        val via = resolveVia(req, err) ?: return fail(err.toString())
        return block(via)
    }

    /** shell `input ...` 成功时无输出；非空一律当错误文本 */
    private fun shellResult(via: String, out: String): Pair<JSONObject, ByteArray?> =
        if (out.isEmpty()) ok(JSONObject().put("via", via))
        else fail("shell 命令返回: $out")

    private fun gestureResult(via: String, done: Boolean, what: String): Pair<JSONObject, ByteArray?> =
        if (done) ok(JSONObject().put("via", via)) else fail("$what 未成功（手势被取消或超时）")

    // ─── 各 op ──────────────────────────────────────────────

    private fun screenshot(req: JSONObject): Pair<JSONObject, ByteArray?> = withVia(req) { via ->
        if (via == "a11y") {
            val timeout = req.optLong("timeout_ms", 5000)
            val got = A11yBridge.screenshotRgba(timeout)
                ?: return@withVia fail("takeScreenshot 失败（节流或服务异常）", retryable = true)
            val header = JSONObject()
                .put("via", via)
                .put("fmt", "rgba")
                .put("w", got[0] as Int)
                .put("h", got[1] as Int)
            okBin(header, got[2] as ByteArray)
        } else {
            val png = ShellBridge.screencapPng()
            if (png.size < PNG_MAGIC.size || !png.copyOf(PNG_MAGIC.size).contentEquals(PNG_MAGIC)) {
                return@withVia fail("screencap 返回的不是 PNG: ${String(png.copyOf(minOf(png.size, 80)))}")
            }
            okBin(JSONObject().put("via", via).put("fmt", "png"), png)
        }
    }

    private fun tap(req: JSONObject): Pair<JSONObject, ByteArray?> = withVia(req) { via ->
        val x = req.getInt("x")
        val y = req.getInt("y")
        if (via == "a11y") {
            gestureResult(via, A11yBridge.tap(x, y, req.optLong("duration_ms", 50)), "点击")
        } else {
            shellResult(via, ShellBridge.tap(x, y))
        }
    }

    private fun longPress(req: JSONObject): Pair<JSONObject, ByteArray?> = withVia(req) { via ->
        val x = req.getInt("x")
        val y = req.getInt("y")
        val duration = req.optLong("duration_ms", 800)
        if (via == "a11y") {
            gestureResult(via, A11yBridge.longPress(x, y, duration), "长按")
        } else {
            shellResult(via, ShellBridge.swipe(x, y, x, y, duration.toInt()))
        }
    }

    private fun swipe(req: JSONObject): Pair<JSONObject, ByteArray?> = withVia(req) { via ->
        val x1 = req.getInt("x1")
        val y1 = req.getInt("y1")
        val x2 = req.getInt("x2")
        val y2 = req.getInt("y2")
        val duration = req.optLong("duration_ms", 300)
        if (via == "a11y") {
            gestureResult(via, A11yBridge.swipe(x1, y1, x2, y2, duration), "滑动")
        } else {
            shellResult(via, ShellBridge.swipe(x1, y1, x2, y2, duration.toInt()))
        }
    }

    /** 推到位后停住；shell 通道表达不了"停住"，只能把 hold 合并进 swipe 总时长 */
    private fun holdMove(req: JSONObject): Pair<JSONObject, ByteArray?> = withVia(req) { via ->
        val x1 = req.getInt("x1")
        val y1 = req.getInt("y1")
        val x2 = req.getInt("x2")
        val y2 = req.getInt("y2")
        val moveMs = req.optLong("move_ms", 300)
        val holdMs = req.optLong("hold_ms", 0)
        if (via == "a11y") {
            gestureResult(via, A11yBridge.holdMove(x1, y1, x2, y2, moveMs, holdMs), "推住")
        } else {
            shellResult(via, ShellBridge.swipe(x1, y1, x2, y2, (moveMs + holdMs).toInt()))
        }
    }

    /**
     * 按键：`name` 为 BACK/HOME 时无障碍能用 performGlobalAction；其它 keycode 只有
     * Shizuku 的 `input keyevent` 能发，无障碍通道下直接报错（不静默降级）。
     */
    private fun key(req: JSONObject): Pair<JSONObject, ByteArray?> {
        val name = req.optString("name", "").uppercase()
        val keycode = when {
            req.has("keycode") -> req.getInt("keycode")
            name == "BACK" -> 4
            name == "HOME" -> 3
            else -> return fail("key 需要 name=BACK/HOME 或 keycode")
        }
        val isGlobal = keycode == 4 || keycode == 3
        return withVia(req) { via ->
            when {
                via == "a11y" && isGlobal -> gestureResult(
                    via,
                    if (keycode == 4) A11yBridge.globalBack() else A11yBridge.globalHome(),
                    "全局动作 $keycode",
                )
                via == "a11y" -> {
                    // 请求没强制通道、且 Shizuku 在，就转 shell 发 keyevent
                    if (req.optString("via", "auto") == "auto" && ShellBridge.hasPermission()) {
                        shellResult("shell", ShellBridge.execText("input", "keyevent", keycode.toString()))
                    } else {
                        fail("无障碍通道只支持 BACK/HOME，keycode=$keycode 需要 Shizuku")
                    }
                }
                else -> shellResult(via, ShellBridge.execText("input", "keyevent", keycode.toString()))
            }
        }
    }

    /** 任意 shell 命令（Shizuku），stdout 原样作二进制负载返回 */
    private fun shell(req: JSONObject): Pair<JSONObject, ByteArray?> {
        if (!ShellBridge.hasPermission()) return fail("Shizuku 未激活或未授权")
        val arr: JSONArray = req.optJSONArray("cmd") ?: return fail("shell 需要 cmd 数组")
        if (arr.length() == 0) return fail("shell cmd 为空")
        val cmd = Array(arr.length()) { arr.getString(it) }
        return okBin(JSONObject().put("via", "shell"), ShellBridge.exec(*cmd))
    }

    // ─── 响应构造 ───────────────────────────────────────────

    private fun ok(header: JSONObject): Pair<JSONObject, ByteArray?> = header.put("ok", true) to null

    private fun okBin(header: JSONObject, payload: ByteArray): Pair<JSONObject, ByteArray?> =
        header.put("ok", true) to payload

    private fun fail(error: String, retryable: Boolean = false): Pair<JSONObject, ByteArray?> {
        Log.w(TAG, error)
        val header = JSONObject().put("ok", false).put("error", error)
        if (retryable) header.put("retryable", true)
        return header to null
    }
}
