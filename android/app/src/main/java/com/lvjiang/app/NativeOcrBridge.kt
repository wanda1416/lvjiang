package com.lvjiang.app

import org.json.JSONArray
import org.json.JSONObject

/** 图像字节只进入一次；预处理、模型输出和解码均留在 native。 */
class NativeOcrBridge(det: String, cls: String, rec: String, settings: String) {
    private var handle: Long

    init {
        val config = JSONObject(settings)
        val global = config.getJSONObject("Global")
        val detector = config.getJSONObject("Det")
        val classifier = config.getJSONObject("Cls")
        val recognizer = config.getJSONObject("Rec")
        val clsShape = classifier.getJSONArray("cls_image_shape")
        val recShape = recognizer.getJSONArray("rec_img_shape")
        val integers = intArrayOf(
            global.getInt("max_side_len"), global.getInt("min_side_len"),
            global.getInt("min_height"), global.getInt("width_height_ratio"),
            detector.getInt("limit_side_len"), detector.getInt("max_candidates"),
            if (detector.getBoolean("use_dilation")) 1 else 0,
            clsShape.getInt(1), clsShape.getInt(2), recShape.getInt(1), recShape.getInt(2),
            config.getInt("max_detector_pixels"),
        )
        val floats = floatArrayOf(
            detector.getDouble("thresh").toFloat(), detector.getDouble("box_thresh").toFloat(),
            detector.getDouble("unclip_ratio").toFloat(), global.getDouble("text_score").toFloat(),
            classifier.getDouble("cls_thresh").toFloat(),
        )
        handle = create(det, cls, rec, config.getInt("threads"), integers, floats)
        RuntimeDiagnostics.record("native_ocr_open", JSONObject().put("backend", "cpp-ort"))
    }

    @Synchronized
    fun recognize(image: ByteArray, width: Int, height: Int): String {
        check(handle != 0L) { "OCR 会话已关闭" }
        val started = System.nanoTime()
        val fields = JSONObject().put("backend", "cpp-ort")
            .put("input_bytes", image.size).put("input_shape", JSONArray(intArrayOf(height, width, 3)))
        RuntimeDiagnostics.record("native_ocr_begin", fields)
        try {
            val payload = JSONObject(String(run(handle, image, width, height), Charsets.UTF_8))
            fields.put("models", payload.getJSONObject("models"))
            fields.put("texts", payload.getJSONArray("results").length())
            return payload.getJSONArray("results").toString()
        } catch (error: Throwable) {
            fields.put("error", error.javaClass.simpleName).put("message", error.message?.take(500))
            throw error
        } finally {
            fields.put("elapsed_ms", (System.nanoTime() - started) / 1_000_000)
            RuntimeDiagnostics.record("native_ocr_end", fields)
        }
    }

    @Synchronized
    fun close() {
        if (handle == 0L) return
        destroy(handle)
        handle = 0L
        RuntimeDiagnostics.record("native_ocr_close", JSONObject().put("backend", "cpp-ort"))
    }

    private external fun create(det: String, cls: String, rec: String, threads: Int,
                                integers: IntArray, floats: FloatArray): Long
    private external fun run(handle: Long, image: ByteArray, width: Int, height: Int): ByteArray
    private external fun destroy(handle: Long)

    companion object {
        init { System.loadLibrary("lvjiang_ocr") }
    }
}
