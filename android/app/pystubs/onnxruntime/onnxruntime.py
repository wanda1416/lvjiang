"""onnxruntime 占位实现（见同目录 setup.py 的说明）。

上游 utils/infer_engine.py 在模块顶层 import 五个名字，且这五个的实际调用点
（L39/43/52/55/99）全都落在 OrtInferSession 类内部。设备端 adapter 会整类替换
OrtInferSession，因此这些名字只需存在即可让 import 通过。
任何真实调用都意味着替换未生效 —— 立即失败并说明原因，而不是静默走错路径。
"""

_MSG = (
    "onnxruntime 在 Android 端不可用（Chaquopy 无此原生包）。"
    "OrtInferSession 应已被 rapidocr_adapter 替换为 Kotlin OnnxBridge 通道，"
    "出现此错误说明替换未生效。"
)


class GraphOptimizationLevel:
    """真包中是 pybind11 枚举，这里退化为常量容器。

    取值沿用 onnxruntime 的定义顺序，以防有代码按整数比较。
    """

    ORT_DISABLE_ALL = 0
    ORT_ENABLE_BASIC = 1
    ORT_ENABLE_EXTENDED = 2
    ORT_ENABLE_ALL = 99


class InferenceSession:
    def __init__(self, *args, **kwargs):
        raise RuntimeError(_MSG)


class SessionOptions:
    def __init__(self, *args, **kwargs):
        raise RuntimeError(_MSG)


def get_available_providers():
    raise RuntimeError(_MSG)


def get_device():
    raise RuntimeError(_MSG)
