package com.lvjiang.app

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.util.Log
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.atomic.AtomicInteger
import org.json.JSONArray
import org.json.JSONObject

/**
 * 一次推理的输出：扁平 float32 字节流 + 形状。
 *
 * 不直接返回 ORT 的嵌套数组（OnnxTensor.getValue 会为 1x3xHxW 这种张量构造出
 * 成千上万个 Java 子数组）。只提取一份 float32 ByteBuffer 的底层数组，
 * 不再先复制为 FloatBuffer、再复制为 ByteArray。
 */
class OnnxOutput(
    @JvmField val data: ByteArray,
    @JvmField val shape: LongArray,
)

/**
 * OnnxBridge — 用 onnxruntime-android 顶替 Python 的 onnxruntime 包。
 *
 * Chaquopy 包仓库没有 onnxruntime 的 Android wheel（见 android/app/pystubs/onnxruntime），
 * 所以设备端的推理走这里，由 src/lvjiang/core/ondevice/onnx_session.py 包装成
 * rapidocr 期望的 OrtInferSession 接口后注入。
 *
 * 模型文件用的就是 rapidocr_onnxruntime 自带的那三个 .onnx（随 Chaquopy 打包进 APK，
 * 首次导入时被解压到 filesDir），因此与 PC 端跑的是同一份权重。
 *
 * 线程约束：调用方保证不在主线程调用（单次检测推理是百毫秒级）。
 */
class OnnxBridge(modelPath: String, intraOpNumThreads: Int) {

    private val session: OrtSession
    private val options: OrtSession.SessionOptions
    private val modelName = File(modelPath).name
    private var inputBuffer: ByteBuffer? = null
    private var closed = false

    init {
        val file = File(modelPath)
        if (!file.isFile) {
            throw IllegalArgumentException("模型文件不存在：$modelPath")
        }
        options = OrtSession.SessionOptions()
        try {
            // 与 PC 的 RapidOCR 一致：不保留随动态输入增长的 CPU arena。
            options.setCPUArenaAllocator(false)
            if (intraOpNumThreads > 0) {
                options.setIntraOpNumThreads(intraOpNumThreads)
            }
            options.setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT)
            session = env.createSession(modelPath, options)
        } catch (error: Throwable) {
            options.close()
            throw error
        }
        Log.i(TAG, "session ready: ${file.name} (${file.length()} bytes)")
        RuntimeDiagnostics.record("onnx_session_open", JSONObject()
            .put("model", modelName).put("cpu_arena", false).put("sessions", liveSessions.incrementAndGet()))
    }

    fun inputNames(): Array<String> = session.inputNames.toTypedArray()

    fun outputNames(): Array<String> = session.outputNames.toTypedArray()

    /** 读模型自带的自定义元数据；识别模型的字符表就存在这里（key = "character"）。缺失返回 null。 */
    fun metadata(key: String): String? = session.metadata.customMetadata[key]

    /**
     * 单输入单输出推理。
     *
     * @param inputData 输入张量的 float32 小端字节流（numpy ndarray.tobytes() 的结果）
     * @param shape 输入张量形状
     * @return 第一个输出的 float32 字节流与形状
     */
    @Synchronized
    fun run(inputData: ByteArray, shape: LongArray): OnnxOutput {
        check(!closed) { "推理会话已关闭" }
        val started = System.nanoTime()
        val fields = JSONObject().put("model", modelName)
            .put("input_shape", JSONArray(shape.toList())).put("input_bytes", inputData.size)
        RuntimeDiagnostics.record("onnx_run_begin", fields)
        try {
            var buffer = inputBuffer
            if (buffer == null || buffer.capacity() < inputData.size) {
                buffer = ByteBuffer.allocateDirect(inputData.size).order(ByteOrder.nativeOrder())
                inputBuffer = buffer
            }
            buffer.clear()
            buffer.put(inputData)
            buffer.flip()
            fields.put("input_capacity", buffer.capacity())
            OnnxTensor.createTensor(env, buffer.asFloatBuffer(), shape).use { tensor ->
                session.run(mapOf(session.inputNames.first() to tensor)).use { result ->
                    val out = result[0] as OnnxTensor
                    val bytes = out.byteBuffer.array()
                    fields.put("output_shape", JSONArray(out.info.shape.toList())).put("output_bytes", bytes.size)
                    return OnnxOutput(bytes, out.info.shape)
                }
            }
        } catch (error: Throwable) {
            fields.put("error", error.javaClass.simpleName).put("message", error.message?.take(500))
            throw error
        } finally {
            fields.put("elapsed_ms", (System.nanoTime() - started) / 1_000_000)
            RuntimeDiagnostics.record("onnx_run_end", fields)
        }
    }

    @Synchronized
    fun close() {
        if (closed) return
        try {
            session.close()
        } finally {
            options.close()
            inputBuffer = null
            closed = true
            RuntimeDiagnostics.record("onnx_session_close", JSONObject()
                .put("model", modelName).put("sessions", liveSessions.decrementAndGet()))
        }
    }

    companion object {
        private const val TAG = "OnnxBridge"
        private val liveSessions = AtomicInteger()

        /** OrtEnvironment 是进程级单例，三个模型（det/cls/rec）共用 */
        private val env: OrtEnvironment by lazy { OrtEnvironment.getEnvironment() }
    }
}
