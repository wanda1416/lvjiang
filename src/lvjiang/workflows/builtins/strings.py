"""内置函数 - 字符串处理"""

import re

from loguru import logger

from ._coerce import to_number
from ._registry import builtin_func

# ─── 字符串 ─────────────────────────────────────────────

@builtin_func("substr")
def _substr(s, *args) -> str:
    """子串截取

    - substr($s, start)        → s[start:]
    - substr($s, start, end)   → s[start:end+1]（闭区间）
    - start/end 支持负数索引（Python 语义）
    - 越界自动截断，不报错
    - 非字符串先 str() 转换
    """
    s = str(s) if s is not None else ""
    if not args:
        return s
    try:
        start = int(args[0])
    except (TypeError, ValueError):
        return s
    if len(args) >= 2:
        try:
            end = int(args[1])
            return s[start:end + 1]
        except (TypeError, ValueError):
            return s[start:]
    return s[start:]


@builtin_func("split")
def _split(s, *args) -> list:
    """按分隔符拆分为列表

    - split($s, sep) → list
    - sep 为空字符串时按字符拆分（与 Python str.split("") 不同，避免返回单字符列表的歧义）
    - 非字符串先 str() 转换
    """
    s = str(s) if s is not None else ""
    if not args:
        return [s]
    sep = args[0]
    if sep == "":
        return list(s)
    return s.split(str(sep))


@builtin_func("replace")
def _replace(s, *args) -> str:
    """替换所有匹配

    - replace($s, old, new) → str
    - 非字符串先 str() 转换
    """
    s = str(s) if s is not None else ""
    if len(args) < 2:
        return s
    old = str(args[0])
    new = str(args[1])
    if old == "":
        return s
    return s.replace(old, new)


@builtin_func("match")
def _match(s, *args) -> bool:
    """正则匹配（Python re.search）

    - match($s, regex) → bool
    - 非法正则返回 False，不抛错
    - 非字符串先 str() 转换
    """
    s = str(s) if s is not None else ""
    if not args:
        return False
    pattern = str(args[0])
    try:
        return bool(re.search(pattern, s))
    except re.error as e:
        logger.debug(f"match: 非法正则 {pattern!r}: {e}")
        return False


@builtin_func("trim")
def _trim(s) -> str:
    """去除两端空白

    非字符串先 str() 转换。
    """
    return str(s).strip() if s is not None else ""


@builtin_func("upper")
def _upper(s) -> str:
    """转大写"""
    return str(s).upper() if s is not None else ""


@builtin_func("lower")
def _lower(s) -> str:
    """转小写"""
    return str(s).lower() if s is not None else ""


@builtin_func("to_num")
def _to_num(s):
    """字符串转数字，失败返回 0

    含小数点 → float，否则 → int。
    """
    result = to_number(s)
    return result if result is not None else 0
