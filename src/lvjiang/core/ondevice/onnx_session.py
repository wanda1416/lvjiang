"""设备端推理会话 — 把 Kotlin OnnxBridge 包装成 rapidocr 期望的接口

Chaquopy 包仓库没有 onnxruntime 的 Android wheel，设备端的推理由
com.lvjiang.app.OnnxBridge（onnxruntime-android）承担。本模块负责两件事：
1. 把 OnnxBridge 包装成 rapidocr 的 OrtInferSession 那几个方法；
2. install() 一次性完成 rapidocr 的两处替换，是设备端使用 OCR 的唯一入口。

模型仍是 rapidocr_onnxruntime 自带的三个 .onnx（随 Chaquopy 打包进 APK），
与 PC 端同一份权重，因此差异只来自推理引擎实现本身。
"""

import numpy as np

from .rapidocr_adapter import patch_all


class JavaInferSession:
    """rapidocr OrtInferSession 的设备端替身

    只实现 rapidocr 真正调用到的四个入口（构造 + __call__ + have_key +
    get_character_list），不复刻原类里那些 CUDA/DirectML 探测逻辑 —— 设备端只有 CPU。
    """

    def __init__(self, config):
        model_path = config.get("model_path")
        if not model_path:
            raise ValueError("config 中缺少 model_path")

        # 延迟导入：只有在 Chaquopy 环境里才存在 com.lvjiang.app，
        # 这样本模块在 PC 上也能被导入（便于静态检查与单测）。
        from com.lvjiang.app import OnnxBridge

        threads = int(config.get("intra_op_num_threads", -1) or -1)
        self._bridge = OnnxBridge(str(model_path), threads)

    def __call__(self, input_content: np.ndarray):
        """单输入推理，返回 [输出数组]

        返回列表是为了对齐原实现（session.run 返回全部输出，rapidocr 只取 [0]）。

        输出数组由 np.frombuffer 构造，是只读的。rapidocr 的三条后处理链路都只读它，
        真有原地写入会抛「assignment destination is read-only」而不是静默出错，
        因此不额外拷贝一份（检测模型的输出可达数 MB）。
        """
        arr = np.ascontiguousarray(input_content, dtype=np.float32)
        # shape 显式转成 int 列表：numpy 的整型标量不会被 Chaquopy 认成 long
        output = self._bridge.run(arr.tobytes(), [int(d) for d in arr.shape])
        data = np.frombuffer(output.data, dtype=np.float32)
        return [data.reshape(tuple(output.shape))]

    def have_key(self, key: str = "character") -> bool:
        return self._bridge.metadata(key) is not None

    def get_character_list(self, key: str = "character") -> list[str]:
        value = self._bridge.metadata(key)
        if value is None:
            raise KeyError(f"模型元数据中没有 {key!r}")
        return value.splitlines()

    def close(self) -> None:
        self._bridge.close()


def install() -> None:
    """设备端启用 OCR 前必须调用一次

    完成 unclip 去 pyclipper/shapely + OrtInferSession 换成 OnnxBridge 两处替换。
    必须在构造 RapidOCR 之前调用。重复调用无副作用。
    """
    patch_all(JavaInferSession)
