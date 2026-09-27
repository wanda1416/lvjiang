"""DSL 标量类型公共工具

int/float 双类型语义：
- int/float 直接返回
- 字符串按内容判断：含小数点 → float，否则 → int
- bool 返回 None（不参与数值运算）

布尔字面文本只认 ``true`` / ``false`` / ``1`` / ``0``，见 :data:`BOOL_TEXT`。
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


#: 布尔参数允许的字面文本（大小写不敏感）。
#: 字符串只收这四种。YAML 会先把未加引号的 yes/no/on/off 解析为真正的 bool；
#: 加引号后它们仍是字符串，不在此处作为额外别名接受。
BOOL_TEXT = {"true": True, "false": False, "1": True, "0": False}


def is_bool_text(val) -> bool:
    """字符串是否是允许的布尔字面文本。"""
    return isinstance(val, str) and val.strip().lower() in BOOL_TEXT


def to_bool(val) -> bool:
    """把参数值解释为布尔。

    控件给出的是真正的 ``bool``，而配置文件里可能存成字符串，两种形态必须得到
    同一个结果。字符串只认 :data:`BOOL_TEXT`；其余类型退回 Python 真值性。
    """
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return BOOL_TEXT.get(val.strip().lower(), False)
    return bool(val)
