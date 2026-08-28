"""yysls 对通用参考图 OCR 字段的领域解释。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from loguru import logger

from lvjiang.core.recognizers import ReferenceInfo, ReferenceRecognizer
from lvjiang.core.reference_db import ReferenceDatabase

REQUIRED_OUTPUT_FIELDS = ("level_text", "count_text")


def get_missing_output_fields(db: ReferenceDatabase) -> list[str]:
    existing = {field.key for field in db.get_output_fields()}
    return [key for key in REQUIRED_OUTPUT_FIELDS if key not in existing]


#: 游戏内数量的万位后缀。**不能走 tr()**：这是 OCR 从游戏画面读到的文本，
#: 恒为中文，与用户界面语言无关。用 tr() 会让"把界面切成英文"意外改变解析
#: 规则——目前两个语言文件都把它映射回"万"所以没出事，但那是巧合不是保证。
_WAN_SUFFIX = "万"


def parse_number(text: str) -> int | None:
    """解析 yysls 数量/等级文本，包括“万”单位和 OCR 噪声。

    含多段数字时取最后一段（OCR 噪声如 '0/1 1092' 取 1092）。
    """
    text = text.strip()
    if not text:
        return None
    multiplier = 1
    if text.endswith(_WAN_SUFFIX):
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
    count: int = 0
    count_recognized: bool = False
    devoted: int | None = None
    real_level: int = 0


def parse_tuning_material(info: ReferenceInfo) -> TuningMaterial:
    """让 Python 类工作流复用 DSL 解析语义，并对识别失败安全降级。

    材料面板属于连续自动化路径：单个格子未匹配，或数量/等级 OCR
    暂时读不到，都不能让整次调律异常退出。未匹配格按空材料处理；
    已匹配材料的数值无法解析时按 0。DSL 的 ``with`` 转换仍保持严格，
    真正缺少 schema 输出字段时继续报错。
    """
    base = ReferenceRecognizer.build_rich_base(info)
    base.setdefault("level_text", "")
    base.setdefault("count_text", "")
    count_text = str(base.get("count_text", "") or "")
    count_source = count_text.split("/")[-1] if "/" in count_text else count_text
    count_recognized = parse_number(count_source) is not None
    if info.label and not count_recognized:
        logger.warning(
            f"材料数量 OCR 无法识别: label={info.label!r}, "
            f"count_text={count_text!r}，按 0 处理"
        )
    rich = parse_rich_base(base)
    devoted = rich.get("devoted")
    return TuningMaterial(
        label=info.label,
        confidence=float(info.confidence),
        count=rich.get("count", 0),
        count_recognized=count_recognized,
        devoted=(devoted if devoted is not None else 0)
        if "/" in count_text else None,
        real_level=rich.get("real_level", 0),
    )
