"""内置函数 - 基础运算

只保留运算符无法表达的四类能力：取模、最值、绝对值、真假压平。四则运算由文法里的
``+ - * /`` 直接表达，这里不再提供 add/sub/mul/div——同名函数与运算符完全
重复，且变参形式容易让调用点误以为能一次连加多值。

int/float 双类型语义：参数保持原始类型，int+int→int，int+float→float；
无法转成数字时返回 0。
"""

from ._coerce import to_flag_value, to_number
from ._registry import builtin_func

#: 本模块函数的缺省分组；个别函数用 @builtin_func(group=...) 覆盖
GROUP = "基础运算"


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


@builtin_func(
    "to_flag",
    params={
        "x": "任意值：按语言真值判定（与 if $x 同口径），非空/非零/true 记 1",
    },
    returns="int",
)
def _to_flag(x):
    """把真假结果压成 1 / 0

    判定口径与 ``if $x`` 完全一致，只把结果写成数字，供需要「1/0 数字」的
    场合使用（返回页面判定、与其他数字比较、累加计数）。不要用 ``1 - $x``
    表达取反：那只在 x 恰为 0/1 时成立，x 是 bool/null/字符串时会算错或报错。

    ``"0"`` / ``"false"`` 是非空字符串，按真值记 **1**；要让字段文本按布尔
    解释，先用 ``contains`` / ``==`` 判定再传入。

    .wf 用法:
        return to_flag(not $in_bag)
        eval $hits = $hits + to_flag($found)
    """
    return to_flag_value(x)
