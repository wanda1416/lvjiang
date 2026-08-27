"""yysls 对通用参考图 OCR 字段的领域解释。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from lvjiang.core.recognizers import ReferenceInfo, ReferenceRecognizer
from lvjiang.core.reference_db import ReferenceDatabase

from .....i18n import tr

REQUIRED_OUTPUT_FIELDS = ("level_text", "count_text")


def get_missing_output_fields(db: ReferenceDatabase) -> list[str]:
    existing = {field.key for field in db.get_output_fields()}
    return [key for key in REQUIRED_OUTPUT_FIELDS if key not in existing]


def parse_number(text: str) -> int | None:
    """解析 yysls 数量/等级文本，包括“万”单位和 OCR 噪声。"""
    text = text.strip()
    if not text:
        return None
    multiplier = 1
    if text.endswith(tr("万")):
        text = text[:-1]
        multiplier = 10000
    try:
        return int(float(text) * multiplier)
    except ValueError:
        numbers = re.findall(r"\d+", text)
        return int(numbers[-1]) * multiplier if numbers else None


def parse_rich_base(base: dict[str, Any]) -> dict[str, Any]:
    """就地解析 DSL rich base，保持既有 builtin 返回约定。"""
    missing = [key for key in REQUIRED_OUTPUT_FIELDS if key not in base]
    if missing:
        raise ValueError(
            "当前图库空间缺少 yysls 输出字段: " + "、".join(missing)
        )
    level_text = str(base.pop("level_text", "") or "")
    count_text = str(base.pop("count_text", "") or "")
    real_level = parse_number(level_text)
    if real_level is not None:
        base["real_level"] = real_level
    if "/" in count_text:
        parts = count_text.split("/")
        devoted = parse_number(parts[0])
        count = parse_number(parts[-1]) if parts else None
        if devoted is not None:
            base["devoted"] = devoted
        if count is not None:
            base["count"] = count
    else:
        count = parse_number(count_text)
        if count is not None:
            base["count"] = count
    return base


@dataclass(slots=True)
class TuningMaterial:
    """自动调律消费的 yysls 材料领域对象。"""

    label: str
    confidence: float
    count: int | None = None
    devoted: int | None = None
    real_level: int | None = None


def parse_tuning_material(info: ReferenceInfo) -> TuningMaterial:
    """让 Python 类工作流复用与 DSL ``with`` 相同的解析语义。"""
    rich = parse_rich_base(ReferenceRecognizer.build_rich_base(info))
    return TuningMaterial(
        label=info.label,
        confidence=float(info.confidence),
        count=rich.get("count"),
        devoted=rich.get("devoted"),
        real_level=rich.get("real_level"),
    )
