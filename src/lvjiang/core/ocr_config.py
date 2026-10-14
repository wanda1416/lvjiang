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
    from .config import get_resolver

    doc = load_ocr_config()
    recognition = dict(doc.get("recognition") or {})
    recognition["region_batch"] = {
        "min_canvas_side": config.min_canvas_side,
        "max_content_height": config.max_content_height,
        "gap": config.gap,
    }
    doc["recognition"] = recognition
    get_resolver().save_merged(OCR_CONFIG_PATH, doc)
