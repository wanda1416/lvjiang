"""内置函数 - 基础运算

统一 number（float）语义：参数宽容转 float，返回 float。
与算术表达式（engine._eval_arith）行为一致：除法为浮点除，除 0 返回 0。
"""

from ._registry import builtin_func


@builtin_func("add")
def _add(a, b, *args) -> float:
    """两数相加

    .wf 用法:
        eval $new_val = add($old_val, 1)
    """
    try:
        return float(a) + float(b)
    except (ValueError, TypeError):
        return 0.0


@builtin_func("sub")
def _sub(a, b, *args) -> float:
    """两数相减

    .wf 用法:
        eval $remain = sub($total, 1)
    """
    try:
        return float(a) - float(b)
    except (ValueError, TypeError):
        return 0.0


@builtin_func("mul")
def _mul(a, b, *args) -> float:
    """两数相乘

    .wf 用法:
        eval $double = mul($val, 2)
    """
    try:
        return float(a) * float(b)
    except (ValueError, TypeError):
        return 0.0


@builtin_func("div")
def _div(a, b, *args) -> float:
    """两数相除（浮点除），除数为 0 返回 0

    .wf 用法:
        eval $half = div($total, 2)
    """
    try:
        divisor = float(b)
        if divisor == 0:
            return 0.0
        return float(a) / divisor
    except (ValueError, TypeError):
        return 0.0


@builtin_func("mod")
def _mod(a, b, *args) -> float:
    """取模运算，除数为 0 返回 0

    .wf 用法:
        eval $remainder = mod($val, 3)
    """
    try:
        divisor = float(b)
        if divisor == 0:
            return 0.0
        return float(a) % divisor
    except (ValueError, TypeError):
        return 0.0


@builtin_func("min")
def _min(a, b, *args) -> float:
    """取多个数中最小值（至少两个参数）

    .wf 用法:
        eval $clamped = min($val, 100)
        eval $lowest = min($a, $b, $c)
    """
    try:
        return min(float(a), float(b), *(float(x) for x in args))
    except (ValueError, TypeError):
        return 0.0


@builtin_func("max")
def _max(a, b, *args) -> float:
    """取多个数中最大值（至少两个参数）

    .wf 用法:
        eval $at_least = max($val, 1)
        eval $highest = max($a, $b, $c)
    """
    try:
        return max(float(a), float(b), *(float(x) for x in args))
    except (ValueError, TypeError):
        return 0.0


@builtin_func("abs")
def _abs(a, *args) -> float:
    """取绝对值

    .wf 用法:
        eval $positive = abs($diff)
    """
    try:
        return abs(float(a))
    except (ValueError, TypeError):
        return 0.0
