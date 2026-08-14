"""DSL 数值类型公共工具

int/float 双类型语义：
- int/float 直接返回
- 字符串按内容判断：含小数点 → float，否则 → int
- bool 返回 None（不参与数值运算）
"""


def to_number(val) -> int | float | None:
    """将值转为数值，失败时返回 None

    - bool → None（Python 中 bool ⊂ int，需先拦截）
    - int/float → 直接返回
    - 字符串：含小数点 → float，否则 → int
    - 其他类型或转换失败 → None
    """
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return val
    try:
        s = str(val)
        f = float(s)
        if "." not in s and f.is_integer():
            return int(f)
        return f
    except (ValueError, TypeError):
        return None
