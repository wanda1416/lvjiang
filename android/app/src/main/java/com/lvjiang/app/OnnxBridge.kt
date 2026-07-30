package com.lvjiang.app

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.util.Log
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * 一次推理的输出：扁平 float32 字节流 + 形状。
 *
 * 不直接返回 ORT 的嵌套数组（OnnxTensor.getValue 会为 1x3xHxW 这种张量构造出
 * 成千上万个 Java 子数组），而是把 float32 原样拷进 ByteArray，由 Python 侧
 * np.frombuffer + reshape 零拷贝还原。
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

    init {
        val file = File(modelPath)
        if (!file.isFile) {
            throw IllegalArgumentException("模型文件不存在：$modelPath")
        }
        val options = OrtSession.SessionOptions()
        if (intraOpNumThreads > 0) {
            options.setIntraOpNumThreads(intraOpNumThreads)
        }
        options.setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT)
        session = env.createSession(modelPath, options)
        Log.i(TAG, "session ready: ${file.name} (${file.length()} bytes)")
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
    fun run(inputData: ByteArray, shape: LongArray): OnnxOutput {
        // ORT 只在 buffer 是 direct 时才能零拷贝，否则它自己会再拷一遍
        val buffer = ByteBuffer.allocateDirect(inputData.size)
            .order(ByteOrder.nativeOrder())
        buffer.put(inputData)
        buffer.rewind()

        val inputName = session.inputNames.first()
        OnnxTensor.createTensor(env, buffer.asFloatBuffer(), shape).use { tensor ->
            session.run(mapOf(inputName to tensor)).use { result ->
                val out = result[0] as OnnxTensor
                val floats = out.floatBuffer
                val bytes = ByteArray(floats.remaining() * Float.SIZE_BYTES)
                ByteBuffer.wrap(bytes).order(ByteOrder.nativeOrder())
                    .asFloatBuffer().put(floats)
                return OnnxOutput(bytes, out.info.shape)
            }
        }
    }

    fun close() {
        try {
            session.close()
        } catch (e: Throwable) {
            Log.w(TAG, "session close failed", e)
        }
    }

    companion object {
        private const val TAG = "OnnxBridge"

        /** OrtEnvironment 是进程级单例，三个模型（det/cls/rec）共用 */
        private val env: OrtEnvironment by lazy { OrtEnvironment.getEnvironment() }
    }
}
