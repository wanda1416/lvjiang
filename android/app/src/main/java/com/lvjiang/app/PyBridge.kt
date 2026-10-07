package com.lvjiang.app

import android.content.Context
import android.util.Log
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject

/**
 * PyBridge — Chaquopy 调用的唯一入口。
 *
 * 三处都要用 Python（MainActivity 自检、FloatService 任务、后续的引导页），
 * 各写一遍 `if (!Python.isStarted()) Python.start(...)` 迟早会漏，
 * 而 Python.start 重复调用会直接抛异常。统一收在这里。
 *
 * 所有方法都是阻塞的，必须在工作线程调用；Python 侧任务本身是异步的
 * （start 立刻返回，靠 status 轮询看进度），所以这里的阻塞都是毫秒级，
 * 唯一的例外是首次 ensureStarted（解释器初始化 + 模块导入，秒级）。
 */
object PyBridge {

    private const val TAG = "PyBridge"
    private const val CONFIGURED_APPS = "yysls"
    private const val MODULE = "lvjiang.core.ondevice.task_runner"
    private const val SETTINGS_MODULE = "lvjiang.core.ondevice.task_settings"
    private const val CALIB_MODULE = "lvjiang.core.ondevice.screen_calib_api"
    private const val OFFLINE_MODULE = "lvjiang.core.ondevice.offline"

    @Volatile
    private var startFailure: String? = null

    /** 启动解释器（幂等）。失败原因会被记住，避免每次调用都重跑一遍必然失败的初始化。 */
    @Synchronized
    fun ensureStarted(context: Context): String? {
        startFailure?.let { return it }
        return try {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(context.applicationContext))
            }
            Python.getInstance()
                .getModule("lvjiang.core.ondevice.plugins")
                .callAttr("configure_apps", CONFIGURED_APPS)
            null
        } catch (e: Throwable) {
            val msg = "Python 启动失败：${e.message}"
            Log.e(TAG, msg, e)
            startFailure = msg
            msg
        }
    }

    private fun module(context: Context, name: String = MODULE): PyObject {
        ensureStarted(context)?.let { throw IllegalStateException(it) }
        return Python.getInstance().getModule(name)
    }

    /**
     * 调 task_runner 的某个函数，返回解析好的 JSON 对象。
     *
     * Python 侧的四个入口都返回 JSON 文本且自己吞掉异常，所以正常路径不会抛；
     * 能抛到这里的只有「解释器/模块层面就没起来」，统一包装成
     * `{ok:false, message:...}`，调用方不必再写一层 try。
     */
    private fun callJson(
        context: Context,
        fn: String,
        vararg args: Any?,
        moduleName: String = MODULE,
    ): JSONObject {
        return try {
            JSONObject(module(context, moduleName).callAttr(fn, *args).toString())
        } catch (e: Throwable) {
            Log.e(TAG, "调用 $fn 失败", e)
            JSONObject().apply {
                put("ok", false)
                put("state", "failed")
                put("message", "${e.javaClass.simpleName}: ${e.message}")
            }
        }
    }

    /** 可执行任务清单：`{ok, tasks:[{id,name,source}], error}` */
    fun listTasks(context: Context): JSONObject = callJson(context, "list_tasks")
    fun listUsers(context: Context): JSONObject = callJson(context, "list_users")
    fun selectUser(context: Context, username: String): JSONObject = synchronized(AgentServer.executionLock) {
        if (AgentServer.pcControlsDevice()) {
            JSONObject().put("ok", false).put("message", "PC 正在执行，请先结束 PC 任务")
        } else {
            callJson(context, "select_user", username)
        }
    }

    /** 启动任务：`{ok, message}`。ok=false 时任务未启动，message 可直接 toast。 */
    fun startTask(context: Context, taskId: String, username: String = ""): JSONObject = synchronized(AgentServer.executionLock) {
        if (AgentServer.pcControlsDevice()) {
            JSONObject().put("ok", false).put("message", "PC 正在执行，请先结束 PC 任务")
        } else {
            callJson(context, "start_task", taskId, "", username)
        }
    }

    /** 请求停止：`{ok, message}` */
    fun stopTask(context: Context): JSONObject = callJson(context, "stop_task")
    fun pauseTask(context: Context): JSONObject = callJson(context, "pause_task")
    fun resumeTask(context: Context): JSONObject = callJson(context, "resume_task")
    fun applySync(context: Context, path: String, preserveTaskParams: Boolean): JSONObject =
        callJson(context, "apply_sync", path, preserveTaskParams, moduleName = OFFLINE_MODULE)
    fun checkRuntime(context: Context, ocr: Boolean = true, screenRepetitions: Int = 0): JSONObject =
        callJson(context, "check_runtime", ocr, screenRepetitions, moduleName = OFFLINE_MODULE)

    /** 状态快照：`{state, task_name, message, elapsed, stopping, logs}` */
    fun status(context: Context): JSONObject = callJson(context, "get_status")

    fun listTaskSettings(context: Context, username: String): JSONObject =
        callJson(context, "list_settings", username, moduleName = SETTINGS_MODULE)

    fun getTaskSettings(context: Context, username: String, taskId: String): JSONObject =
        callJson(context, "get_settings", username, taskId, moduleName = SETTINGS_MODULE)

    fun previewTaskSettings(context: Context, username: String, taskId: String, payload: String): JSONObject =
        callJson(context, "preview_parameters", username, taskId, payload, moduleName = SETTINGS_MODULE)

    fun saveTaskSettings(context: Context, username: String, taskId: String, token: String,
                         payload: String, reset: Boolean = false): JSONObject = synchronized(AgentServer.executionLock) {
        if (AgentServer.pcControlsDevice()) {
            JSONObject().put("ok", false).put("message", "PC 正在执行，请先结束 PC 任务")
        } else {
            callJson(context, "save_settings", username, taskId, token, payload, reset, moduleName = SETTINGS_MODULE)
        }
    }

    /** 屏幕标定接口（CalibActivity 用）：calib_info / calib_capture / calib_locate / calib_solve / calib_save / … */
    fun calib(context: Context, fn: String, vararg args: Any?): JSONObject =
        callJson(context, fn, *args, moduleName = CALIB_MODULE)
}
