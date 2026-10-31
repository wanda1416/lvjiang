"""内置函数 - 基础运算

int/float 双类型语义：参数保持原始类型，int+int→int，int+float→float。
除法始终为浮点除，除 0 返回 0.0。
"""

from ._coerce import to_number
from ._registry import builtin_func

#: 本模块函数的缺省分组；个别函数用 @builtin_func(group=...) 覆盖
GROUP = "基础运算"


@builtin_func(
    "add",
    params={
        "a": "加数",
        "b": "被加数",
        "*args": "其余加数，依次累加",
    },
    returns="int | float",
)
def _add(a, b, *args):
    """两数相加

    .wf 用法:
        eval $new_val = add($old_val, 1)
    """
    a_num, b_num = to_number(a), to_number(b)
    if a_num is None or b_num is None:
        return 0
    return a_num + b_num


@builtin_func(
    "sub",
    params={
        "a": "被减数",
        "b": "减数",
        "*args": "其余减数，依次相减",
    },
    returns="int | float",
)
def _sub(a, b, *args):
    """两数相减

    .wf 用法:
        eval $remain = sub($total, 1)
    """
    a_num, b_num = to_number(a), to_number(b)
    if a_num is None or b_num is None:
        return 0
    return a_num - b_num


@builtin_func(
    "mul",
    params={
        "a": "乘数",
        "b": "被乘数",
        "*args": "其余乘数，依次相乘",
    },
    returns="int | float",
)
def _mul(a, b, *args):
    """两数相乘

    .wf 用法:
        eval $double = mul($val, 2)
    """
    a_num, b_num = to_number(a), to_number(b)
    if a_num is None or b_num is None:
        return 0
    return a_num * b_num


@builtin_func(
    "div",
    params={
        "a": "被除数",
        "b": "除数，为 0 时返回 0.0",
        "*args": "其余参数忽略",
    },
    returns="float",
)
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


@builtin_func(
    "mod",
    params={
        "a": "被取模数",
        "b": "模数，为 0 时返回 0",
        "*args": "其余参数忽略",
    },
    returns="int | float",
)
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


@builtin_func(
    "min",
    params={
        "a": "第一个数",
        "b": "第二个数",
        "*args": "其余参与比较的数",
    },
    returns="int | float",
)
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


@builtin_func(
    "max",
    params={
        "a": "第一个数",
        "b": "第二个数",
        "*args": "其余参与比较的数",
    },
    returns="int | float",
)
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


@builtin_func(
    "abs",
    params={
        "a": "待取绝对值的数",
        "*args": "其余参数忽略",
    },
    returns="int | float",
)
def _abs(a, *args):
    """取绝对值

    .wf 用法:
        eval $positive = abs($diff)
    """
    a_num = to_number(a)
    if a_num is None:
        return 0
    return abs(a_num)
