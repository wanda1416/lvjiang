"""设备端推理会话 — 把 Kotlin OnnxBridge 包装成 rapidocr 期望的接口

Chaquopy 包仓库没有 onnxruntime 的 Android wheel，设备端的推理由
com.lvjiang.app.OnnxBridge（onnxruntime-android）承担。本模块负责两件事：
1. 把 OnnxBridge 包装成 rapidocr 的 OrtInferSession 那几个方法；
2. install() 一次性完成 rapidocr 的两处替换，是设备端使用 OCR 的唯一入口。

模型仍是 rapidocr_onnxruntime 自带的三个 .onnx（随 Chaquopy 打包进 APK），
与 PC 端同一份权重，因此差异只来自推理引擎实现本身。
"""

from weakref import WeakSet

import numpy as np

from ...i18n import tr
from .rapidocr_adapter import patch_all

_sessions: WeakSet["JavaInferSession"] = WeakSet()


class JavaInferSession:
    """rapidocr OrtInferSession 的设备端替身

    只实现 rapidocr 真正调用到的四个入口（构造 + __call__ + have_key +
    get_character_list），不复刻原类里那些 CUDA/DirectML 探测逻辑 —— 设备端只有 CPU。
    """

    def __init__(self, config):
        model_path = config.get("model_path")
        if not model_path:
            raise ValueError(tr("config 中缺少 model_path"))

        # 延迟导入：只有在 Chaquopy 环境里才存在 com.lvjiang.app，
        # 这样本模块在 PC 上也能被导入（便于静态检查与单测）。
        from com.lvjiang.app import OnnxBridge

        threads = int(config.get("intra_op_num_threads", -1) or -1)
        self._bridge = OnnxBridge(str(model_path), threads)
        self._closed = False
        _sessions.add(self)

    def __call__(self, input_content: np.ndarray):
        """单输入推理，返回 [输出数组]

        返回列表是为了对齐原实现（session.run 返回全部输出，rapidocr 只取 [0]）。

        输出数组由 np.frombuffer 构造，是只读的。rapidocr 的三条后处理链路都只读它，
        真有原地写入会抛「assignment destination is read-only」而不是静默出错，
        因此不额外拷贝一份（检测模型的输出可达数 MB）。
        """
        arr = np.ascontiguousarray(input_content, dtype=np.float32)
        # shape 显式转成 int 列表：numpy 的整型标量不会被 Chaquopy 认成 long
        from ai.onnxruntime import OrtException
        from java.lang import OutOfMemoryError
        try:
            output = self._bridge.run(arr.tobytes(), [int(d) for d in arr.shape])
        except OutOfMemoryError as exc:
            raise MemoryError("手机 OCR 内存不足，已中止任务；请查看运行诊断日志") from exc
        except OrtException as exc:
            message = str(exc).lower()
            if "allocate memory" in message or "bad_alloc" in message:
                raise MemoryError("手机 ONNX 原生内存分配失败，已中止任务") from exc
            raise
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
        if not getattr(self, "_closed", True):
            self._bridge.close()
            self._closed = True
            _sessions.discard(self)

    def __del__(self):
        # 覆盖 RapidOCR 构造到一半失败时已经创建的会话。
        try:
            self.close()
        except Exception:
            pass


def close_sessions() -> None:
    """同步重载前显式释放设备端全部旧会话，不依赖 Java/Python GC 时机。"""
    for session in list(_sessions):
        session.close()


def install() -> None:
    """设备端启用 OCR 前必须调用一次

    完成 unclip 去 pyclipper/shapely + OrtInferSession 换成 OnnxBridge 两处替换。
    必须在构造 RapidOCR 之前调用。重复调用无副作用。
    """
    patch_all(JavaInferSession)
