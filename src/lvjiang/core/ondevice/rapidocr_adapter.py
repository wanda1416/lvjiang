"""RapidOCR 设备端适配 — 替换 Android 上装不上的三个原生依赖点

设备端装的 rapidocr_onnxruntime 就是 PyPI 原版（纯 Python 包，PC 端无需任何改动），
但它声明的 pyclipper / shapely / onnxruntime 都是原生扩展，Chaquopy 包仓库没有对应的
Android wheel，安装时由 android/app/pystubs/ 下的占位包顶替。占位包里的每个符号被真实
调用都会抛 RuntimeError，因此使用 RapidOCR 之前必须先由本模块完成替换：

- DBPostProcess.unclip：原实现用 shapely 算 area/length、pyclipper 做多边形外扩，
  这里改为纯 cv2 等价实现（推导见 _unclip_cv2 的注释）。
- OrtInferSession：原实现直连 onnxruntime，这里换成注入的会话工厂（设备端指向 Kotlin
  侧 OnnxBridge，底层是 onnxruntime-android）。

两个替换互相独立：
- patch_unclip() 在 PC 上同样可用，且正是「去掉 pyclipper/shapely 后识别结果是否仍然
  一致」的验证手段 —— PC 上两条路径都能跑，可直接对同一张图做 diff。
- patch_infer_session() 只在设备端有意义。
"""

import importlib

import cv2
import numpy as np

# 三个 text_* 模块用的是绝对导入（from rapidocr_onnxruntime.utils import OrtInferSession），
# 导入完成后各自持有一份独立的名字绑定，只改 utils 包不会影响它们，必须逐个模块替换。
_INFER_SESSION_HOLDERS = (
    "rapidocr_onnxruntime.utils.infer_engine",
    "rapidocr_onnxruntime.utils",
    "rapidocr_onnxruntime.ch_ppocr_cls.text_cls",
    "rapidocr_onnxruntime.ch_ppocr_det.text_detect",
    "rapidocr_onnxruntime.ch_ppocr_rec.text_recognize",
)


def _unclip_cv2(self, box: np.ndarray) -> np.ndarray:
    """DBPostProcess.unclip 的纯 cv2 替代实现

    可以绕开多边形偏移，是因为上下游的形状被限死了：传进来的 box 恒为 get_mini_boxes
    产出的 4 点旋转矩形，返回值又立刻被 get_mini_boxes 取 minAreaRect。矩形按 JT_ROUND
    外扩 distance 得到的是圆角矩形，而圆角矩形的最小外接矩形恰好等于原矩形四边各外移
    distance —— 中心与旋转角不变，宽高各加 2*distance。（圆角只影响四个角，不影响
    沿矩形自身两个轴向的极值点，后者由四条笔直偏移段给出。）

    两处细节是为了对齐原实现的数值行为，否则边界会差 1~2px，进而改变识别阶段的
    裁剪范围并吐出不同的边缘字符/标点：

    1. 面积与周长用 float64 手算（鞋带公式 + 边长求和）而不用 cv2.contourArea/arcLength：
       后者要求输入 float32，会损失精度；原实现的 shapely Polygon.area/.length 走 float64。
    2. 外扩前把顶点截断为整数、外扩后再四舍五入：pyclipper 全程在整数坐标系里运算，
       AddPath 会把浮点顶点截断成整数，输出坐标也是取整的。注意 distance 仍用未截断的
       原始顶点计算 —— 原实现里 shapely 拿到的也是原始浮点 box。

    返回 dtype 用 float32（数值均为整数）：cv2.minAreaRect 对 float32 的支持比各平台上
    宽窄不一的 int64 更可靠，而几何上与整数坐标等价。
    """
    pts = np.asarray(box, dtype=np.float64).reshape(-1, 2)

    x, y = pts[:, 0], pts[:, 1]
    area = 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))
    length = np.linalg.norm(pts - np.roll(pts, 1, axis=0), axis=1).sum()
    distance = area * self.unclip_ratio / length

    truncated = pts.astype(np.int64).astype(np.float32)
    (cx, cy), (w, h), angle = cv2.minAreaRect(truncated)
    expanded = cv2.boxPoints(((cx, cy), (w + 2 * distance, h + 2 * distance), angle))
    return np.round(expanded).astype(np.float32).reshape((-1, 1, 2))


_unclip_cv2._lvjiang_patched = True


def patch_unclip() -> None:
    """把 DBPostProcess.unclip 替换为不依赖 pyclipper/shapely 的实现

    重复调用无副作用。

    Raises:
        RuntimeError: 上游结构已变（找不到 unclip），此时不应静默放过 —— 说明
            rapidocr_onnxruntime 版本与本适配层的假设不再匹配
    """
    from rapidocr_onnxruntime.ch_ppocr_det.utils import DBPostProcess

    original = getattr(DBPostProcess, "unclip", None)
    if original is None:
        raise RuntimeError(
            "DBPostProcess.unclip 不存在，rapidocr_onnxruntime 的内部结构已变更，"
            "需重新核对本适配层的替换点"
        )
    if getattr(original, "_lvjiang_patched", False):
        return

    DBPostProcess.unclip = _unclip_cv2


def patch_infer_session(session_factory) -> None:
    """把 OrtInferSession 替换为指定的会话工厂

    Args:
        session_factory: 可调用对象，接受 rapidocr 传入的 config 字典
            （含 model_path / use_cuda / use_dml / intra_op_num_threads /
            inter_op_num_threads），返回的会话实例需满足 rapidocr 实际用到的接口：
            - __call__(input_content: np.ndarray) -> list[np.ndarray]
            - have_key(key: str = "character") -> bool
            - get_character_list(key: str = "character") -> list[str]
            后两者读的是 ONNX 模型元数据里的字符表，仅识别模型会用到。

    Raises:
        RuntimeError: 某个模块里找不到 OrtInferSession，说明上游结构已变更
    """
    for module_name in _INFER_SESSION_HOLDERS:
        module = importlib.import_module(module_name)
        if not hasattr(module, "OrtInferSession"):
            raise RuntimeError(
                f"{module_name} 中找不到 OrtInferSession，"
                "rapidocr_onnxruntime 的内部结构已变更，需重新核对本适配层的替换点"
            )
        module.OrtInferSession = session_factory


def patch_all(session_factory) -> None:
    """设备端入口：一次完成两处替换

    必须在构造 RapidOCR 之前调用，否则 text_* 模块会在 __init__ 里直接实例化
    占位的 OrtInferSession 并抛错。

    Args:
        session_factory: 见 patch_infer_session
    """
    patch_unclip()
    patch_infer_session(session_factory)
