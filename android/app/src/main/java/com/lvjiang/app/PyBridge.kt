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
    private const val MODULE = "lvjiang.core.ondevice.task_runner"
    private const val TUNING_MODULE = "lvjiang.core.ondevice.tuning_config"

    @Volatile
    private var startFailure: String? = null

    /** 启动解释器（幂等）。失败原因会被记住，避免每次调用都重跑一遍必然失败的初始化。 */
    @Synchronized
    fun ensureStarted(context: Context): String? {
        startFailure?.let { return it }
        if (Python.isStarted()) return null
        return try {
            Python.start(AndroidPlatform(context.applicationContext))
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

    /** 启动任务：`{ok, message}`。ok=false 时任务未启动，message 可直接 toast。 */
    fun startTask(context: Context, taskId: String): JSONObject =
        callJson(context, "start_task", taskId, "")

    /** 请求停止：`{ok, message}` */
    fun stopTask(context: Context): JSONObject = callJson(context, "stop_task")

    /** 状态快照：`{state, task_name, message, elapsed, stopping, logs}` */
    fun status(context: Context): JSONObject = callJson(context, "get_status")

    /** 调律配置合并视图：`{ok, rules, slot_groups, switches, error}` */
    fun getTuningConfig(context: Context): JSONObject =
        callJson(context, "get_tuning_config", moduleName = TUNING_MODULE)

    /** 保存调律配置：`{ok, message}`。ok=false 时 message 为校验失败原因。 */
    fun saveTuningConfig(context: Context, payload: String): JSONObject =
        callJson(context, "save_tuning_config", payload, moduleName = TUNING_MODULE)
}
