"""内置函数 - 基础运算"""

from ._registry import builtin_func


@builtin_func("add")
def _add(a, b, *args) -> int:
    """两数相加

    .wf 用法:
        eval $new_val = add($old_val, 1)
    """
    try:
        return int(a) + int(b)
    except (ValueError, TypeError):
        return 0


@builtin_func("sub")
def _sub(a, b, *args) -> int:
    """两数相减

    .wf 用法:
        eval $remain = sub($total, 1)
    """
    try:
        return int(a) - int(b)
    except (ValueError, TypeError):
        return 0


@builtin_func("mul")
def _mul(a, b, *args) -> int:
    """两数相乘

    .wf 用法:
        eval $double = mul($val, 2)
    """
    try:
        return int(a) * int(b)
    except (ValueError, TypeError):
        return 0


@builtin_func("div")
def _div(a, b, *args) -> int:
    """两数相除（整除），除数为 0 返回 0

    .wf 用法:
        eval $half = div($total, 2)
    """
    try:
        divisor = int(b)
        if divisor == 0:
            return 0
        return int(a) // divisor
    except (ValueError, TypeError):
        return 0


@builtin_func("mod")
def _mod(a, b, *args) -> int:
    """取模运算，除数为 0 返回 0

    .wf 用法:
        eval $remainder = mod($val, 3)
    """
    try:
        divisor = int(b)
        if divisor == 0:
            return 0
        return int(a) % divisor
    except (ValueError, TypeError):
        return 0


@builtin_func("min")
def _min(a, b, *args) -> int:
    """取两数中较小值

    .wf 用法:
        eval $clamped = min($val, 100)
    """
    try:
        return min(int(a), int(b))
    except (ValueError, TypeError):
        return 0


@builtin_func("max")
def _max(a, b, *args) -> int:
    """取两数中较大值

    .wf 用法:
        eval $at_least = max($val, 1)
    """
    try:
        return max(int(a), int(b))
    except (ValueError, TypeError):
        return 0


@builtin_func("abs")
def _abs(a, *args) -> int:
    """取绝对值

    .wf 用法:
        eval $positive = abs($diff)
    """
    try:
        return abs(int(a))
    except (ValueError, TypeError):
        return 0
