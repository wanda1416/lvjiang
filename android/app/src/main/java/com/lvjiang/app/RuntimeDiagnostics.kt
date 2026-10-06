package com.lvjiang.app

import android.content.Context
import android.os.Debug
import android.os.Process
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.time.Instant

/** 私有目录内有界日志；不写截图、识别原文或配置全文。 */
object RuntimeDiagnostics {
    private const val TAG = "LvjiangRuntime"
    private const val MAX_BYTES = 2L * 1024 * 1024
    private var directory: File? = null

    @Synchronized
    fun initialize(context: Context) {
        directory = File(context.filesDir, "lvjiang/data/diagnostics").apply { mkdirs() }
        record("app_started", JSONObject().put("version", BuildConfig.VERSION_NAME))
    }

    fun snapshot(): JSONObject {
        val runtime = Runtime.getRuntime()
        val result = JSONObject()
            .put("native_alloc_kib", Debug.getNativeHeapAllocatedSize() / 1024)
            .put("java_used_kib", (runtime.totalMemory() - runtime.freeMemory()) / 1024)
            .put("java_max_kib", runtime.maxMemory() / 1024)
        runCatching {
            File("/proc/self/status").useLines { lines ->
                lines.forEach { line ->
                    val key = when {
                        line.startsWith("VmRSS:") -> "rss_kib"
                        line.startsWith("VmHWM:") -> "peak_rss_kib"
                        line.startsWith("VmSwap:") -> "swap_kib"
                        else -> null
                    }
                    if (key != null) result.put(key, line.substringAfter(':').trim().substringBefore(' ').toLong())
                }
            }
        }
        return result
    }

    @Synchronized
    fun record(event: String, fields: JSONObject = JSONObject()) {
        val entry = JSONObject()
            .put("time", Instant.now().toString())
            .put("pid", Process.myPid())
            .put("event", event)
            .put("memory", snapshot())
            .put("fields", fields)
        Log.i(TAG, entry.toString())
        val dir = directory ?: return
        runCatching {
            val file = File(dir, "android-runtime.jsonl")
            if (file.length() >= MAX_BYTES) {
                // 仅轮换本日志拥有的上一份归档，保留最近两份。
                val previous = File(dir, "android-runtime.previous.jsonl")
                if (previous.exists() && !previous.delete()) error("无法轮换旧诊断日志")
                if (!file.renameTo(previous)) error("无法归档诊断日志")
            }
            file.appendText(entry.toString() + "\n")
        }.onFailure { Log.w(TAG, "诊断日志写入失败", it) }
    }

    fun recordMessage(event: String, detail: String) {
        record(event, JSONObject().put("detail", detail.take(2000)))
    }
}
