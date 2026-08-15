"""内置函数 - 通用工具（字符串、字典、列表、范围、时间）"""

from loguru import logger

from ._registry import builtin_func

# ─── 字符串 ─────────────────────────────────────────────

@builtin_func("concat")
def _concat(*args) -> str:
    """拼接多个参数为字符串

    null 参数视为空字符串，与字符串上下文行为一致。

    .wf 用法:
        log concat("当前数据: ", $dict.key)
        eval $msg = concat("结果: ", $var, " 完成")
    """
    return "".join("" if arg is None else str(arg) for arg in args)


@builtin_func("split")
def _split(s: str, delimiter: str) -> list:
    """按分隔符拆分字符串，返回列表

    返回的列表可用 $var[0] / $var[1] 访问各段。

    .wf 用法:
        eval $parts = split("20223/23028", "/")
        # $parts = ["20223", "23028"]
        eval $current = $parts[0]
        eval $total = $parts[1]
    """
    return str(s).split(str(delimiter))


# ─── 范围 ───────────────────────────────────────────────

@builtin_func("range")
def _range(*args) -> list:
    """生成整数范围列表（闭区间）

    .wf 用法:
        eval $list = range(1, 100)     # [1, 2, ..., 100]
        for i in range(1, 5)           # 迭代 1, 2, 3, 4, 5
            ...
        end

    参数:
        range(end)        → [1, 2, ..., end]
        range(start, end) → [start, start+1, ..., end]
    """
    if len(args) == 1:
        end = int(args[0])
        return list(range(1, end + 1))
    elif len(args) == 2:
        start = int(args[0])
        end = int(args[1])
        return list(range(start, end + 1))
    else:
        raise ValueError(f"range() 需要 1-2 个参数，收到 {len(args)} 个")


# ─── 字典 ───────────────────────────────────────────────

@builtin_func("count_nonempty")
def _count_nonempty(scan_result, *args) -> int:
    """统计非空项数量

    - dict: 统计非空值字段数量（空字符串/纯空白不计）
    - list: 统计元素数量
    - 其他: 返回 0

    .wf 用法:
        eval n = count_nonempty($result)
        eval n = count_nonempty($list)
    """
    if isinstance(scan_result, list):
        return len(scan_result)
    if not isinstance(scan_result, dict):
        return 0
    return sum(1 for v in scan_result.values() if v and str(v).strip())


@builtin_func("contains")
def _contains(scan_result: dict, *args) -> bool:
    """检查 scan 结果中是否有任意字段包含指定文本

    .wf 用法: contains(result, "调律")
    """
    if not isinstance(scan_result, dict) or not args:
        return False
    text = str(args[0])
    return any(text in v for v in scan_result.values() if isinstance(v, str))


@builtin_func("find_key")
def _find_key(scan_result: dict, *args) -> str:
    """在字典 values 中查找包含目标文本的项，返回其 key 名

    找不到时返回空字符串 ""，配合 if 判断。

    .wf 用法:
        scan [scene].[f1, f2, f3] as $scan
        eval $key = find_key($scan, "调律")
        if $key
            click [scene].$key
        end
    """
    if not isinstance(scan_result, dict) or not args:
        return ""
    target = str(args[0])
    for key, value in scan_result.items():
        if isinstance(value, str) and target in value:
            logger.debug(f"find_key: '{target}' 命中 {key}='{value}'")
            return key
    logger.debug(f"find_key: '{target}' 未找到")
    return ""


# ─── 列表/字典追加 ──────────────────────────────────────

@builtin_func("append")
def _append(list_or_dict, *args) -> str:
    """向列表追加元素，或向字典添加键值对

    - append($list, $value) → 追加 value 到 list
    - append($dict, $key, $value) → 设置 dict[key] = value

    返回空字符串（副作用操作）。

    .wf 用法:
        eval append($candidates, $equip_data)
        eval append($fingerprints, $slot, $fp)
    """
    if list_or_dict is None:
        return ""
    if isinstance(list_or_dict, list) and len(args) >= 1:
        list_or_dict.append(args[0])
    elif isinstance(list_or_dict, dict) and len(args) >= 2:
        list_or_dict[str(args[0])] = args[1]
    else:
        logger.warning(f"append: 参数不匹配 list={isinstance(list_or_dict, list)} args={len(args)}")
    return ""


# ─── 字典/列表扩展 ─────────────────────────────────────

@builtin_func("len")
def _len(obj) -> int:
    """返回长度

    - dict: 返回 key 数（含空值）
    - list: 返回元素数
    - str: 返回字符数
    - 其他: 返回 0

    .wf 用法:
        eval $n = len($dict)
        eval $n = len($list)
        if len($list) > 0
            ...
        end
    """
    if isinstance(obj, (dict, list, str)):
        return len(obj)
    return 0


@builtin_func("keys")
def _keys(obj, *args) -> list:
    """返回字典所有 key 的列表

    非字典返回空列表。常用于 for 迭代：
        for k in keys($dict)
            ...
        end
    """
    if isinstance(obj, dict):
        return list(obj.keys())
    return []


@builtin_func("values")
def _values(obj, *args) -> list:
    """返回字典所有 value 的列表

    非字典返回空列表。
    """
    if isinstance(obj, dict):
        return list(obj.values())
    return []


@builtin_func("has_key")
def _has_key(obj, *args) -> bool:
    """检查字典是否包含指定 key

    .wf 用法:
        if has_key($dict, "target_key")
            ...
        end
    """
    if not isinstance(obj, dict) or not args:
        return False
    return str(args[0]) in obj


@builtin_func("del_key")
def _del_key(obj, *args) -> str:
    """删除字典指定 key（不存在不报错）

    返回空字符串（副作用操作）。
    """
    if isinstance(obj, dict) and args:
        obj.pop(str(args[0]), None)
    return ""


@builtin_func("remove")
def _remove(obj, *args) -> str:
    """删除列表中首个匹配元素

    返回空字符串（副作用操作）。
    """
    if isinstance(obj, list) and args:
        try:
            obj.remove(args[0])
        except ValueError:
            pass
    return ""


@builtin_func("slice")
def _slice(obj, *args) -> list:
    """列表切片（闭区间）

    - slice($list, start, end) → list[start:end+1]
    - start/end 支持负数索引（Python 语义）
    - 越界自动截断，不报错
    - 非列表返回空列表
    """
    if not isinstance(obj, list) or len(args) < 2:
        return []
    try:
        start = int(args[0])
        end = int(args[1])
    except (TypeError, ValueError):
        return []
    return obj[start:end + 1]


# ─── 时间 ──────────────────────────────────────────────────

@builtin_func("clock")
def _clock() -> float:
    """获取当前 Unix 时间戳（秒精度 float）

    .wf 用法:
        eval $ts = clock()
        eval $elapsed = clock() - $start_ts
    """
    import time
    return time.time()


@builtin_func("datetime")
def _datetime(*args) -> str:
    """时间格式化：支持当前时间或指定时间戳

    - datetime() → 当前时间，默认格式 "YYYY-MM-DD HH:MM:SS"
    - datetime("格式") → 当前时间，自定义 strftime 格式
    - datetime($ts) → 指定时间戳，默认格式
    - datetime($ts, "格式") → 指定时间戳，自定义格式

    .wf 用法:
        eval $start = clock()
        # ... 执行操作 ...
        eval $elapsed = clock() - $start
        log concat("开始时间: ", datetime($start, "%H:%M:%S"))
        log concat("当前时间: ", datetime())
    """
    from datetime import datetime
    default_fmt = "%Y-%m-%d %H:%M:%S"

    if not args:
        # datetime() → 当前时间默认格式
        return datetime.now().strftime(default_fmt)

    first = args[0]
    if isinstance(first, (int, float)):
        # 第一个参数是时间戳
        ts = float(first)
        fmt = str(args[1]) if len(args) > 1 else default_fmt
        return datetime.fromtimestamp(ts).strftime(fmt)
    else:
        # 第一个参数是格式字符串
        return datetime.now().strftime(str(first))
