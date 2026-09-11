"""OCR 聚合配置。

``ocr.yaml`` 是 OCR 能力的统一配置入口。当前包含文本规范化和区域批量
识别参数，后续新增引擎或识别策略时继续增加独立顶层分组。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OCR_CONFIG_PATH = "ocr.yaml"
_LEGACY_RULES_PATH = "ocr_rules.yaml"


@dataclass(frozen=True)
class RegionBatchConfig:
    """多个文字区域拼成一张识别画布时使用的参数。"""

    min_canvas_side: int = 736
    max_content_height: int = 1200
    gap: int = 16


def load_ocr_config() -> dict[str, Any]:
    """读取 OCR 聚合配置，并兼容旧版 local 清洗规则。"""
    from .config import get_resolver

    resolver = get_resolver()
    config = resolver.load_merged(OCR_CONFIG_PATH)

    # 旧版允许用户在 config/local/ocr_rules.yaml 自定义清洗规则。新配置还
    # 没有本地覆盖时继续采用它；用户下次保存后会自然写入 ocr.yaml。
    local = resolver.load_local(OCR_CONFIG_PATH)
    if "normalization" not in local:
        legacy = resolver.load_local(_LEGACY_RULES_PATH)
        if legacy:
            config["normalization"] = {"groups": {"equip": {
                "label": "装备词条",
                "replacements": dict(legacy.get("replacements") or {}),
                "patterns": dict(legacy.get("patterns") or {}),
            }}}
    normalization = dict(config.get("normalization") or {})
    if "groups" not in normalization and (
        isinstance(normalization.get("replacements"), dict)
        or isinstance(normalization.get("patterns"), dict)
    ):
        config["normalization"] = {"groups": {"equip": {
            "label": "装备词条",
            "replacements": dict(normalization.get("replacements") or {}),
            "patterns": dict(normalization.get("patterns") or {}),
        }}}
    return config


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
