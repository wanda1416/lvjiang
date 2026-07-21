"""内置函数 - 通用工具（字符串、字典、列表、范围）"""

from loguru import logger

from ._registry import builtin_func


# ─── 字符串 ─────────────────────────────────────────────

@builtin_func("concat")
def _concat(*args) -> str:
    """拼接多个参数为字符串

    .wf 用法:
        log concat("当前数据: ", $dict.key)
        eval $msg = concat("结果: ", $var, " 完成")
    """
    return "".join(str(arg) for arg in args)


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

@builtin_func("count_key")
def _count_key(scan_result, *args) -> int:
    """统计数量

    - dict: 统计非空字段数量
    - list: 统计元素数量
    - 其他: 返回 0

    .wf 用法:
        eval n = count_key(result)
        eval n = count_key($list)
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
