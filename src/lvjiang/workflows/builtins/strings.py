"""内置函数 - 字符串处理"""

import re

from ._coerce import to_number
from ._registry import builtin_func

#: 本模块函数的缺省分组；个别函数用 @builtin_func(group=...) 覆盖
GROUP = "字符串处理"

#: 正则模式长度上限：超长模式基本是拼接错误，也更容易写出灾难性回溯
_MAX_PATTERN_LEN = 2000

#: 正则输入文本长度上限：OCR 单行结果远小于此值，超限说明喂错了数据
_MAX_SUBJECT_LEN = 20000

#: repeat() 结果长度上限，避免误传巨大的次数直接吃满内存
_MAX_REPEAT_LEN = 1_000_000


def _user_error(message: str):
    """构造 WorkflowUserError

    延迟导入：builtins 会被 engine 反向依赖，模块顶层导入 signals 会成环。
    """
    from ..engine.signals import WorkflowUserError
    return WorkflowUserError(message)


def _compile(func_name: str, pattern, subject: str) -> re.Pattern:
    """校验并编译正则

    非法正则、空模式、超长模式与超长文本一律抛 WorkflowUserError：静默返回
    False 会让写错的分支永远不成立，排查成本远高于当场失败。

    注意这里只做长度与语法防护，**不做超时**——CPython 的 re 没有超时机制，
    嵌套量词（如 ``(a+)+``）在长文本上仍可能长时间占用执行线程。
    """
    if pattern is None:
        raise _user_error(f"{func_name}: 正则表达式不能为空")
    pattern = str(pattern)
    if not pattern:
        raise _user_error(f"{func_name}: 正则表达式不能为空")
    if len(pattern) > _MAX_PATTERN_LEN:
        raise _user_error(
            f"{func_name}: 正则过长（{len(pattern)} 字符 > {_MAX_PATTERN_LEN}）")
    if len(subject) > _MAX_SUBJECT_LEN:
        raise _user_error(
            f"{func_name}: 待匹配文本过长（{len(subject)} 字符 > {_MAX_SUBJECT_LEN}），"
            "请先截取目标区域再匹配")
    try:
        return re.compile(pattern)
    except re.error as e:
        raise _user_error(f"{func_name}: 非法正则 {pattern!r}: {e}") from e


# ─── 字符串 ─────────────────────────────────────────────

@builtin_func(
    "substr",
    params={
        "s": "源文本",
        "*args": "起始下标与可选结束下标（闭区间，支持负数）",
    },
    returns="str",
)
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
        except (TypeError, ValueError):
            return s[start:]
        # 闭区间的 +1 只对非负下标成立：负数 end 表示「从末尾倒数」，必须先
        # 换算成绝对位置。否则 end=-1 会算成 s[start:-1+1] = s[start:0]，
        # 恒为空串；end=-2 则会少取一个字符。
        stop = end + 1 if end >= 0 else max(len(s) + end + 1, 0)
        return s[start:stop]
    return s[start:]


@builtin_func(
    "split_lines",
    params={
        "s": "源文本",
    },
    returns="list[str]",
)
def _split_lines(s) -> list[str]:
    """按实际换行拆分文本，兼容 LF、CRLF 和 CR；空行由调用方决定如何处理。"""
    return str(s).splitlines() if s is not None else []


@builtin_func(
    "split",
    params={
        "s": "源文本",
        "*args": "分隔符；空串表示按字符拆，省略返回单元素列表",
    },
    returns="list[str]",
)
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


@builtin_func(
    "replace",
    params={
        "s": "源文本",
        "*args": "被替换文本与新文本",
    },
    returns="str",
)
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


@builtin_func(
    "join",
    params={
        "items": "字符串列表",
        "sep": "分隔符，null 视为空串，缺省为空串",
    },
    returns="str",
)
def _join(items, sep="") -> str:
    """用分隔符拼接列表

    - join($list, ",") → str
    - 非列表返回空串（与 len / slice 的宽容口径一致）
    - 元素为 null 时按空串处理
    """
    if not isinstance(items, list):
        return ""
    delimiter = "" if sep is None else str(sep)
    return delimiter.join("" if item is None else str(item) for item in items)


@builtin_func(
    "startswith",
    params={
        "s": "源文本",
        "prefix": "前缀",
    },
    returns="bool",
)
def _startswith(s, prefix) -> bool:
    """判断文本是否以指定前缀开头

    前缀为空串时返回 true（与 Python str.startswith 一致）。
    """
    text = "" if s is None else str(s)
    return text.startswith("" if prefix is None else str(prefix))


@builtin_func(
    "endswith",
    params={
        "s": "源文本",
        "suffix": "后缀",
    },
    returns="bool",
)
def _endswith(s, suffix) -> bool:
    """判断文本是否以指定后缀结尾

    后缀为空串时返回 true（与 Python str.endswith 一致）。
    """
    text = "" if s is None else str(s)
    return text.endswith("" if suffix is None else str(suffix))


@builtin_func(
    "index_of",
    params={
        "s": "源文本",
        "sub": "待查找子串",
    },
    returns="int",
)
def _index_of(s, sub) -> int:
    """返回子串首次出现的下标

    - index_of($s, "x") → int，找不到返回 -1
    - 空子串一律返回 -1：Python 的 find("") 返回 0，与「在下标 0 找到」
      无法区分，这里按「没找到」处理
    """
    if sub is None:
        return -1
    needle = str(sub)
    if not needle:
        return -1
    return ("" if s is None else str(s)).find(needle)


@builtin_func(
    "repeat",
    params={
        "s": "源文本",
        "count": "重复次数，<= 0 返回空串",
    },
    returns="str",
)
def _repeat(s, count) -> str:
    """重复文本指定次数

    结果长度超过 1,000,000 字符时抛错，避免误传巨大的次数吃满内存。
    """
    text = "" if s is None else str(s)
    try:
        times = int(count)
    except (TypeError, ValueError):
        raise _user_error(f"repeat: 重复次数必须是整数，实际 {count!r}") from None
    if times <= 0 or not text:
        return ""
    if len(text) * times > _MAX_REPEAT_LEN:
        raise _user_error(
            f"repeat: 结果过长（{len(text) * times} 字符 > {_MAX_REPEAT_LEN}）")
    return text * times


@builtin_func(
    "match",
    params={
        "s": "源文本",
        "pattern": "正则表达式（Python re 语法，部分匹配）",
    },
    returns="bool",
)
def _match(s, pattern) -> bool:
    """正则匹配（Python re.search）

    - match($s, regex) → bool
    - 非法正则抛 WorkflowUserError：此前返回 False，写错的正则会让分支永远
      不成立，且只在 debug 日志里留一行
    - 需要取匹配内容用 regex_find / regex_find_all
    """
    text = "" if s is None else str(s)
    return bool(_compile("match", pattern, text).search(text))


# ─── 正则 ─────────────────────────────────────────────

@builtin_func(
    "regex_find",
    params={
        "s": "源文本",
        "pattern": "正则表达式（Python re 语法）",
        "group": "捕获组序号，0 为整段匹配，缺省 0",
    },
    returns="str",
)
def _regex_find(s, pattern, group=0) -> str:
    """返回首个匹配的指定分组

    - regex_find($s, "等级\\s*(\\d+)", 1) → "110"
    - 无匹配、或分组可选而未参与匹配时返回空串
    - 分组序号越界抛 WorkflowUserError（写错数字比静默返回空串更容易发现）
    """
    text = "" if s is None else str(s)
    compiled = _compile("regex_find", pattern, text)
    try:
        index = int(group)
    except (TypeError, ValueError):
        raise _user_error(f"regex_find: 分组序号必须是整数，实际 {group!r}") from None
    if index < 0 or index > compiled.groups:
        raise _user_error(
            f"regex_find: 分组序号 {index} 超出范围（该正则共 {compiled.groups} 个捕获组）")
    found = compiled.search(text)
    if found is None:
        return ""
    value = found.group(index)
    return "" if value is None else value


@builtin_func(
    "regex_find_all",
    params={
        "s": "源文本",
        "pattern": "正则表达式（Python re 语法）",
    },
    returns="list[list[str]]",
)
def _regex_find_all(s, pattern) -> list[list[str]]:
    """返回全部匹配及其分组

    - 每项为 ``[整段匹配, 分组1, 分组2, ...]``，下标 0 恒为整段匹配
    - 正则没有捕获组时，每项只有一个元素
    - 可选分组未参与匹配时按空串占位，保证每项长度一致
    - 无匹配返回空列表
    """
    text = "" if s is None else str(s)
    compiled = _compile("regex_find_all", pattern, text)
    results = []
    for found in compiled.finditer(text):
        row = [found.group(0)]
        for index in range(1, compiled.groups + 1):
            value = found.group(index)
            row.append("" if value is None else value)
        results.append(row)
    return results


@builtin_func(
    "regex_replace",
    params={
        "s": "源文本",
        "pattern": "正则表达式（Python re 语法）",
        "replacement": "替换文本，支持 \\1 形式的反向引用；null 视为空串",
    },
    returns="str",
)
def _regex_replace(s, pattern, replacement) -> str:
    """正则替换全部匹配（Python re.sub）

    - regex_replace($s, "\\s+", "") → 删除所有空白
    - regex_replace($s, "(\\d+)-(\\d+)", "\\2-\\1") → 交换两段数字
    """
    text = "" if s is None else str(s)
    compiled = _compile("regex_replace", pattern, text)
    repl = "" if replacement is None else str(replacement)
    return compiled.sub(repl, text)


@builtin_func(
    "regex_split",
    params={
        "s": "源文本",
        "pattern": "正则表达式（Python re 语法）",
    },
    returns="list[str]",
)
def _regex_split(s, pattern) -> list[str]:
    """按正则拆分文本（Python re.split）

    与 Python 一致：正则含捕获组时，分隔符本身也会作为元素出现在结果里。
    """
    text = "" if s is None else str(s)
    compiled = _compile("regex_split", pattern, text)
    return compiled.split(text)


@builtin_func(
    "regex_escape",
    params={
        "s": "待转义文本",
    },
    returns="str",
)
def _regex_escape(s) -> str:
    """把文本转义为可安全嵌入正则的字面量

    - regex_escape("材料(紫)") → "材料\\(紫\\)"
    - OCR 得到名字后拼进正则时必须先转义，否则名字里的 . ( * 会变成元字符
    """
    return re.escape("" if s is None else str(s))


@builtin_func(
    "trim",
    params={
        "s": "源文本",
    },
    returns="str",
)
def _trim(s) -> str:
    """去除两端空白

    非字符串先 str() 转换。
    """
    return str(s).strip() if s is not None else ""


@builtin_func(
    "upper",
    params={
        "s": "源文本",
    },
    returns="str",
)
def _upper(s) -> str:
    """转大写"""
    return str(s).upper() if s is not None else ""


@builtin_func(
    "lower",
    params={
        "s": "源文本",
    },
    returns="str",
)
def _lower(s) -> str:
    """转小写"""
    return str(s).lower() if s is not None else ""


@builtin_func(
    "to_num",
    params={
        "s": "源文本",
    },
    returns="int | float",
)
def _to_num(s):
    """字符串转数字，失败返回 0

    含小数点 → float，否则 → int。
    """
    result = to_number(s)
    return result if result is not None else 0


_NUMBER_BOUNDARY_LEFT = r"(?<![\d.,，+\-])"
_NUMBER_BOUNDARY_RIGHT = r"(?![\d.,，])"


@builtin_func(
    "extract_int",
    params={
        "s": "源文本",
        "*args": "其余参数忽略",
    },
    returns="int",
)
def _extract_int(s, *args) -> int:
    """从文本中提取第一项非负整数（容忍数字之外的 OCR 乱码）。

    小数、负数和带逗号分隔符的数字不按整数的一部分误提取；找不到
    完整整数时返回 -1，使合法的 0 与解析失败保持可区分。
    """
    s = str(s) if s is not None else ""
    match = re.search(
        rf"{_NUMBER_BOUNDARY_LEFT}\d+{_NUMBER_BOUNDARY_RIGHT}", s,
    )
    return int(match.group(0)) if match else -1


@builtin_func(
    "extract_progress",
    params={
        "s": "源文本",
        "*args": "其余参数忽略",
    },
)
def _extract_progress(s, *args) -> dict:
    """从 OCR 文本中提取唯一的 ``当前值/总量`` 进度对。

    允许进度对前后存在 OCR 噪声、斜杠两侧存在空白，并兼容全角斜杠。
    两侧数字支持千分位逗号（半角/全角，如 ``1,950/2,500``）；不满足
    每三位一组的逗号仍视为噪声分隔符，不参与提取。只有唯一且满足
    ``0 <= current <= total``、``total > 0`` 的候选才算有效；不猜测数字
    内部的字母乱码，也不从残缺文本中退化提取单个整数。
    """
    text = str(s) if s is not None else ""
    candidates: list[tuple[int, int]] = []
    number = r"(?:\d{1,3}(?:[,，]\d{3})+|\d+)"
    pattern = (
        rf"{_NUMBER_BOUNDARY_LEFT}({number})\s*[/／]\s*"
        rf"({number}){_NUMBER_BOUNDARY_RIGHT}"
    )
    for match in re.finditer(pattern, text):
        current = int(match.group(1).replace(",", "").replace("，", ""))
        total = int(match.group(2).replace(",", "").replace("，", ""))
        if total > 0 and current <= total:
            candidates.append((current, total))

    if len(candidates) != 1:
        return {"valid": False, "current": -1, "total": -1}

    current, total = candidates[0]
    return {"valid": True, "current": current, "total": total}


@builtin_func(
    "extract_num",
    params={
        "s": "源文本",
        "*args": "其余参数忽略",
    },
    returns="int | float",
)
def _extract_num(s, *args) -> int | float:
    """从文本中提取第一项非负数（整数或小数）。

    找不到完整数字时返回 -1，使合法的 0 与解析失败保持可区分。
    """
    s = str(s) if s is not None else ""
    match = re.search(
        rf"{_NUMBER_BOUNDARY_LEFT}\d+(?:\.\d+)?{_NUMBER_BOUNDARY_RIGHT}",
        s,
    )
    if not match:
        return -1
    text = match.group(0)
    return float(text) if "." in text else int(text)
