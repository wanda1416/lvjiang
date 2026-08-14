"""内置函数 - 基础运算

int/float 双类型语义：参数保持原始类型，int+int→int，int+float→float。
除法始终为浮点除，除 0 返回 0.0。
"""

from ._coerce import to_number
from ._registry import builtin_func


@builtin_func("add")
def _add(a, b, *args):
    """两数相加

    .wf 用法:
        eval $new_val = add($old_val, 1)
    """
    a_num, b_num = to_number(a), to_number(b)
    if a_num is None or b_num is None:
        return 0
    return a_num + b_num


@builtin_func("sub")
def _sub(a, b, *args):
    """两数相减

    .wf 用法:
        eval $remain = sub($total, 1)
    """
    a_num, b_num = to_number(a), to_number(b)
    if a_num is None or b_num is None:
        return 0
    return a_num - b_num


@builtin_func("mul")
def _mul(a, b, *args):
    """两数相乘

    .wf 用法:
        eval $double = mul($val, 2)
    """
    a_num, b_num = to_number(a), to_number(b)
    if a_num is None or b_num is None:
        return 0
    return a_num * b_num


@builtin_func("div")
def _div(a, b, *args) -> float:
    """两数相除（浮点除），除数为 0 返回 0.0

    .wf 用法:
        eval $half = div($total, 2)
    """
    a_num, b_num = to_number(a), to_number(b)
    if a_num is None or b_num is None:
        return 0.0
    if b_num == 0:
        return 0.0
    return a_num / b_num


@builtin_func("mod")
def _mod(a, b, *args):
    """取模运算，除数为 0 返回 0

    .wf 用法:
        eval $remainder = mod($val, 3)
    """
    a_num, b_num = to_number(a), to_number(b)
    if a_num is None or b_num is None:
        return 0
    if b_num == 0:
        return 0
    return a_num % b_num


@builtin_func("min")
def _min(a, b, *args):
    """取多个数中最小值（至少两个参数）

    .wf 用法:
        eval $clamped = min($val, 100)
        eval $lowest = min($a, $b, $c)
    """
    nums = [to_number(x) for x in (a, b, *args)]
    valid = [n for n in nums if n is not None]
    if not valid:
        return 0
    return min(valid)


@builtin_func("max")
def _max(a, b, *args):
    """取多个数中最大值（至少两个参数）

    .wf 用法:
        eval $at_least = max($val, 1)
        eval $highest = max($a, $b, $c)
    """
    nums = [to_number(x) for x in (a, b, *args)]
    valid = [n for n in nums if n is not None]
    if not valid:
        return 0
    return max(valid)


@builtin_func("abs")
def _abs(a, *args):
    """取绝对值

    .wf 用法:
        eval $positive = abs($diff)
    """
    a_num = to_number(a)
    if a_num is None:
        return 0
    return abs(a_num)
