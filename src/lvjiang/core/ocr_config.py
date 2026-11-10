"""OCR 聚合配置。

``ocr.yaml`` 是 OCR 能力的统一配置入口。当前包含文本规范化和区域批量
识别参数，后续新增引擎或识别策略时继续增加独立顶层分组。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OCR_CONFIG_PATH = "ocr.yaml"


@dataclass(frozen=True)
class RegionBatchConfig:
    """多个文字区域拼成一张识别画布时使用的参数。"""

    min_canvas_side: int = 736
    max_content_height: int = 1200
    gap: int = 16


@dataclass(frozen=True)
class DeviceOCRConfig:
    """手机资源预算；不影响 PC 的识别参数。

    ``max_detector_pixels`` 默认 1,200,000：检测模型的设计输入是短边
    ``limit_side_len=736``，16:9 下约 736x1636，再放大并不提升小字命中，
    却按面积线性放大检测输入与中间特征图，是手机侧内存峰值的主要来源。
    """

    threads: int = 2
    max_detector_pixels: int = 1_200_000


#: PC 端识别线程的数轴上限；实际可用核数由运行期再钳一次。
PC_OCR_THREADS_MAX = 16
PC_OCR_INTER_THREADS_MAX = 8
#: 检测输入短边的可调区间；736 是 RapidOCR 出厂值。
LIMIT_SIDE_LEN_MIN = 480
LIMIT_SIDE_LEN_MAX = 1920


@dataclass(frozen=True)
class PcOCRConfig:
    """PC 端单次识别的并行度。

    RapidOCR 出厂把 Det/Cls/Rec 三个模型的 ``intra_op_num_threads`` 和
    ``inter_op_num_threads`` 都设成 ``-1``，即每个模型各开满物理核：多个
    线程池互相争抢，并行效率反而下降。本机 2048x920 实测（同一张图三次均值）：

    - 默认 ``-1/-1``：8.44 CPU 秒/次、墙钟 2279 ms
    - ``threads=4``：3.98 CPU 秒/次、墙钟 1337 ms
    - ``threads=2``：2.38 CPU 秒/次、墙钟 1578 ms

    所以默认 2：CPU 只剩三成，墙钟还比出厂快；``inter_threads`` 固定 1，
    三个模型各自已有线程池，再开一路只会多一次调度争抢。
    """

    threads: int = 2
    inter_threads: int = 1


@dataclass(frozen=True)
class DetectConfig:
    """检测模型的输入尺寸。

    ``limit_side_len`` 是检测前把画面按短边缩到的上限：调小更快更省 CPU，
    但小字号（访客描述这类）更容易漏检；736 是出厂值。
    """

    limit_side_len: int = 736


def load_device_ocr_config() -> DeviceOCRConfig:
    config = (load_ocr_config().get("recognition") or {}).get("device_ocr") or {}
    value = DeviceOCRConfig(
        threads=int(config.get("threads", 2)),
        max_detector_pixels=int(
            config.get("max_detector_pixels", DeviceOCRConfig.max_detector_pixels)),
    )
    if value.threads < 1 or value.max_detector_pixels < 1024:
        raise ValueError("手机 OCR 线程数至少为 1，检测像素预算至少为 1024")
    return value


def load_ocr_config() -> dict[str, Any]:
    """读取统一 OCR 配置；清洗规则使用 normalization.default/groups。"""
    from .config import get_resolver

    return get_resolver().load_merged(OCR_CONFIG_PATH)


def load_region_batch_config() -> RegionBatchConfig:
    """读取区域批量识别参数。"""
    recognition = load_ocr_config().get("recognition") or {}
    batch = recognition.get("region_batch") or {}
    return RegionBatchConfig(
        min_canvas_side=batch.get("min_canvas_side", 736),
        max_content_height=batch.get("max_content_height", 1200),
        gap=batch.get("gap", 16),
    )


def save_region_batch_config(config: RegionBatchConfig) -> None:
    """保存区域批量识别参数，同时保留 OCR 配置中的其他分组。"""
    save_recognition_settings(region_batch=config)


def load_pc_ocr_config() -> PcOCRConfig:
    """读取 PC 端识别并行度。"""
    recognition = load_ocr_config().get("recognition") or {}
    raw = recognition.get("pc_ocr") or {}
    value = PcOCRConfig(
        threads=int(raw.get("threads", PcOCRConfig.threads)),
        inter_threads=int(raw.get("inter_threads", PcOCRConfig.inter_threads)),
    )
    if not 1 <= value.threads <= PC_OCR_THREADS_MAX:
        raise ValueError(f"识别线程数必须在 1–{PC_OCR_THREADS_MAX} 之间，当前 {value.threads}")
    if not 1 <= value.inter_threads <= PC_OCR_INTER_THREADS_MAX:
        raise ValueError(
            f"并行算子线程数必须在 1–{PC_OCR_INTER_THREADS_MAX} 之间，当前 {value.inter_threads}"
        )
    return value


def load_detect_config() -> DetectConfig:
    """读取检测模型输入尺寸。"""
    recognition = load_ocr_config().get("recognition") or {}
    raw = recognition.get("detect") or {}
    value = DetectConfig(limit_side_len=int(raw.get("limit_side_len", DetectConfig.limit_side_len)))
    if not LIMIT_SIDE_LEN_MIN <= value.limit_side_len <= LIMIT_SIDE_LEN_MAX:
        raise ValueError(
            f"检测输入短边必须在 {LIMIT_SIDE_LEN_MIN}–{LIMIT_SIDE_LEN_MAX} px 之间，"
            f"当前 {value.limit_side_len}"
        )
    return value


def ocr_engine_options() -> dict[str, Any]:
    """PC 端构造 RapidOCR 用的参数。

    调用方不再各自传 options：所有 ``OCREngine()`` 都吃这一份配置，
    避免新增构造点时又退回出厂 ``-1``（吃满物理核）。
    """
    pc = load_pc_ocr_config()
    detect = load_detect_config()
    return {
        # 前两个是 RapidOCR 的 Global 键，会被分发到 Det/Cls/Rec 三个会话；
        # 检测尺寸必须带 det_ 前缀，写裸 limit_side_len 会被静默忽略。
        "intra_op_num_threads": pc.threads,
        "inter_op_num_threads": pc.inter_threads,
        "det_limit_side_len": detect.limit_side_len,
    }


def save_recognition_settings(
    *,
    region_batch: RegionBatchConfig | None = None,
    pc_ocr: PcOCRConfig | None = None,
    detect: DetectConfig | None = None,
) -> None:
    """一次写入识别的各个分组。

    只覆盖调用方传入的分组，其余分组（含清洗规则）原样保留——OCR 配置里
    还有用户手改的 ``normalization``，多个保存入口各自读改写容易把它回滚。
    """
    from .config import get_resolver

    doc = load_ocr_config()
    recognition = dict(doc.get("recognition") or {})
    if region_batch is not None:
        recognition["region_batch"] = {
            "min_canvas_side": region_batch.min_canvas_side,
            "max_content_height": region_batch.max_content_height,
            "gap": region_batch.gap,
        }
    if pc_ocr is not None:
        recognition["pc_ocr"] = {
            "threads": pc_ocr.threads,
            "inter_threads": pc_ocr.inter_threads,
        }
    if detect is not None:
        recognition["detect"] = {"limit_side_len": detect.limit_side_len}
    doc["recognition"] = recognition
    get_resolver().save_merged(OCR_CONFIG_PATH, doc)
