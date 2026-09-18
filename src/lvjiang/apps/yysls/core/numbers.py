"""数值容错转换的公共实现。

两种语义，不可互换：

- ``to_float`` / ``to_int``：**宽松**转换。装备/词条 dict 里的数值可能是
  数字、数字字符串、None 或缺失，全部折成数字参与比较和计算；解析不了
  给默认值。适合"拿来算"的场景。
- ``strict_float``：**严格**类型检查。只接受 int/float（bool 除外），字符串
  与其它类型一律视为"不是数"。适合口径校验：例如上限比例现算时，一个
  字符串数值说明数据本身有问题，不应被悄悄转成数字。

抛领域异常的校验（属性模型、伤害模型）和 OCR 文本解析各有自己的语义，
不归这里。
"""
from __future__ import annotations

from typing import Any, overload


def to_float(value: Any, default: float = 0.0) -> float:
    """宽松转 float：``None``/空/解析失败 → ``default``。"""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def to_int(value: Any, default: int = 0) -> int:
    """宽松转 int：``None``/空/解析失败 → ``default``。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


@overload
def strict_float(value: Any) -> float | None: ...
@overload
def strict_float(value: Any, default: float) -> float: ...


def strict_float(value: Any, default: float | None = None) -> float | None:
    """严格转 float：只接受 int/float（不含 bool），否则 ``default``。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)
