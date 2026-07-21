"""工作流内置函数注册表

提供 DSL 中可调用的内置函数，用于数据判定和条件分支。

使用方式（.wf 文件）：
    scan [scene] as [result]
    eval good = is_good_equip(result)
    if [result].field contains "关键词"
        collect [result] as [label]
    end
"""

from loguru import logger
from pathlib import Path
from typing import Callable


# 全局函数注册表
_FUNCTION_REGISTRY: dict[str, Callable] = {}

# evaluate() 缓存的评估器实例
_cached_evaluator = None


def builtin_func(name: str):
    """装饰器：注册内置函数

    用法：
        @builtin_func("is_good_equip")
        def _is_good_equip(scan_result, *args):
            ...
    """
    def decorator(fn: Callable):
        _FUNCTION_REGISTRY[name] = fn
        return fn
    return decorator


def get_function(name: str) -> Callable | None:
    """获取已注册的内置函数，不存在返回 None"""
    return _FUNCTION_REGISTRY.get(name)


def list_functions() -> list[str]:
    """返回所有已注册函数名"""
    return list(_FUNCTION_REGISTRY.keys())


# ─── UI 交互函数 ─────────────────────────────────────────

@builtin_func("messagebox")
def _messagebox(message: str, *args) -> str:
    """弹出 Windows 消息框，阻塞直到用户点击确定

    使用 Win32 MessageBoxW API，可在工作流子线程中安全调用。

    .wf 用法:
        eval messagebox("请在初始界面开始执行")
        eval messagebox(concat("错误: ", $reason))
    """
    import ctypes
    text = str(message)
    if args:
        text += " ".join(str(a) for a in args)
    ctypes.windll.user32.MessageBoxW(0, text, "工作流提示", 0x40)  # MB_ICONINFORMATION
    return text


# ─── 通用工具函数 ─────────────────────────────────────────

@builtin_func("concat")
def _concat(*args) -> str:
    """拼接多个参数为字符串

    .wf 用法:
        log concat("当前数据: ", $dict.key)
        eval $msg = concat("结果: ", $var, " 完成")
    """
    return "".join(str(arg) for arg in args)


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


@builtin_func("contains")
def _contains(scan_result: dict, *args) -> bool:
    """检查 scan 结果中是否有任意字段包含指定文本

    .wf 用法: contains(result, "调律")
    """
    if not isinstance(scan_result, dict) or not args:
        return False
    text = str(args[0])
    return any(text in v for v in scan_result.values() if isinstance(v, str))


@builtin_func("count")
def _count(scan_result, *args) -> int:
    """统计数量

    - dict: 统计非空字段数量
    - list: 统计元素数量
    - 其他: 返回 0

    .wf 用法:
        eval n = count(result)
        eval n = count($list)
    """
    if isinstance(scan_result, list):
        return len(scan_result)
    if not isinstance(scan_result, dict):
        return 0
    return sum(1 for v in scan_result.values() if v and str(v).strip())


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


# ─── 装备解析函数 ─────────────────────────────────────


@builtin_func("to_equipment")
def _to_equipment(raw_data: dict) -> dict:
    """解析装备 OCR 原始数据为标准装备字典

    返回 EquipmentData.to_dict() 结果，支持 DSL 链式字段访问：
    $weapon.affix_1.value / $weapon.base_attr.name 等。

    .wf 用法:
        scan [equip_weapon_detail] as $result
        eval main_weapon = to_equipment($result)
        collect $main_weapon
    """
    if not isinstance(raw_data, dict) or not raw_data:
        logger.warning("to_equipment: 输入为空或非字典")
        return {}

    from ..equip_parser import get_equipment_parser
    parser = get_equipment_parser()

    try:
        return parser.parse(raw_data).to_dict()
    except Exception as e:
        logger.warning(f"to_equipment: 解析失败: {e}")
        return {}


# ─── 装备判定函数 ─────────────────────────────────────────


@builtin_func("affix_cap")
def _affix_cap(affix_name: str, level, *args) -> float:
    """查询词条数值上限（不含单位）

    根据词条名和装备等级，返回该词条在该等级下的上限值。
    自动将真实词条名映射到配置类别（如 "最大外功攻击" → "外功攻击"）。
    找不到配置时返回 0。

    .wf 用法:
        eval $equip = to_equipment($result)
        eval cap = affix_cap($equip.affix_1.name, $equip.level)
        if $equip.affix_1.value > cap
            log concat($equip.affix_1.name, " 超标: ", $equip.affix_1.value, " > ", cap)
        end
    """
    if not affix_name or level is None:
        return 0
    try:
        level = int(level)
    except (ValueError, TypeError):
        return 0
    from ..evaluator.attr_rules import get_attr_rule_manager
    result = get_attr_rule_manager().get_affix_caps(level, str(affix_name))
    if result is None:
        logger.debug(f"affix_cap: 未找到配置 affix={affix_name} level={level}")
        return 0
    return result["cap"]


@builtin_func("chengyin_cap")
def _chengyin_cap(affix_name: str, level, *args) -> float:
    """查询承音装备词条数值上限（不含单位）

    与 affix_cap 相同，但返回承音数值（上限的 94%）。

    .wf 用法:
        eval cap = chengyin_cap($equip.affix_1.name, $equip.level)
    """
    if not affix_name or level is None:
        return 0
    try:
        level = int(level)
    except (ValueError, TypeError):
        return 0
    from ..evaluator.attr_rules import get_attr_rule_manager
    result = get_attr_rule_manager().get_affix_caps(level, str(affix_name))
    if result is None:
        return 0
    return result["chengyin"]


# 高价值词条关键词（用于 is_good_equip 判定）
# TODO: 从流派规则配置中读取，当前为硬编码
_HIGH_VALUE_KEYWORDS = [
    "大外攻", "会心", "会意", "三率",
    "劲", "敏", "势",
    "武学增效", "首领增伤",
]


@builtin_func("is_good_equip")
def _is_good_equip(scan_result: dict, *args) -> bool:
    """判定装备是否值得保留

    基于 OCR 扫描结果中的词条文本，检查是否包含足够多的高价值词条。

    .wf 用法:
        scan [equip_weapon_detail] as [result]
        if is_good_equip(result)
            collect [result] as [good_equip]
        end
    """
    if not isinstance(scan_result, dict) or not scan_result:
        logger.warning("is_good_equip: 输入为空或非字典")
        return False

    hit_count = 0
    for value in scan_result.values():
        if not isinstance(value, str):
            continue
        for kw in _HIGH_VALUE_KEYWORDS:
            if kw in value:
                hit_count += 1
                break  # 同一字段只计一次

    logger.info(f"is_good_equip: 命中 {hit_count} 条高价值词条 (阈值 2)")
    return hit_count >= 2


# ─── 背包遍历函数 ─────────────────────────────────────────

@builtin_func("make_fingerprint")
def _make_fingerprint(equip_data: dict, *args) -> str:
    """基于装备数据生成去重指纹（MD5 前 8 位 hex）

    指纹由 type + level + quality + chengyin + 全部词条(name:value) 组成。
    空数据或空字典返回空字符串。

    .wf 用法:
        eval $equip = to_equipment($scan)
        eval $fp = make_fingerprint($equip)
    """
    if not isinstance(equip_data, dict) or not equip_data:
        return ""
    import hashlib
    parts = [
        str(equip_data.get("type", "") or ""),
        str(equip_data.get("level", "") or ""),
        str(equip_data.get("quality", "") or ""),
        str(equip_data.get("chengyin", "") or ""),
    ]
    # 词条以 affix_1 ~ affix_5 形式存储在 dict 中
    for i in range(1, 6):
        affix = equip_data.get(f"affix_{i}")
        if isinstance(affix, dict) and affix.get("name"):
            parts.append(f"{affix['name']}:{affix.get('value', '')}")
    raw = "+".join(parts)
    return hashlib.md5(raw.encode()).hexdigest()[:8]


# ─── 滚动管理器函数 ────────────────────────────────────────

@builtin_func("check_scroll")
def _check_scroll(_engine, fingerprint: str, *args) -> str:
    """滚动校验：比对 grid[1][1] 指纹与滚动管理器预期

    返回偏移量字符串：
        "0"  — 正常（指纹在已知序列中，偏移量符合预期）
        "1"  — 没有滚动（指纹仍在行 1 位置）
        "-1" — 滚动过头（指纹在行 3 位置）

    .wf 用法:
        eval $offset = check_scroll($fp)
    """
    manager = _engine.context.get("_scroll_manager", {})
    row_fps = manager.get("row_fps", [])
    fingerprints = manager.get("fingerprints", {})

    if not row_fps:
        logger.debug("check_scroll: 无快照数据，视为正常")
        return "0"

    # 全不匹配 = 全部被回收或新内容，视为正常
    if fingerprint not in fingerprints:
        logger.debug(f"check_scroll: 指纹 {fingerprint} 不在已知集合中，视为正常")
        return "0"

    # 找到指纹在原序列中的位置
    for i, fp in enumerate(row_fps):
        if fp == fingerprint:
            # 滚动 1 步后，行 1 应该是原行 2（i=1）
            # offset = i - 1:  0=正常, -1=过头, +1=没滚
            offset = i - 1
            logger.debug(f"check_scroll: 指纹 {fingerprint} 在 row_fps[{i}]，偏移={offset}")
            return str(offset)

    return "0"


@builtin_func("notify_scroll")
def _notify_scroll(_engine, col, row, fingerprint: str, *args) -> str:
    """记录已处理装备的指纹到滚动管理器

    每行第一列（col=1）的指纹记录为行指纹，用于后续滚动校验。

    .wf 用法:
        eval notify_scroll($col, $row, $fp)
    """
    manager = _engine.context.setdefault("_scroll_manager", {
        "row_fps": [],
        "fingerprints": {},
        "scroll_count": 0,
    })
    manager["fingerprints"][fingerprint] = True
    # 每行第一列（col=1）记录为行指纹
    if str(col) == "1":
        manager["row_fps"].append(fingerprint)
        logger.debug(f"notify_scroll: 记录行指纹 row={row} fp={fingerprint}")
    return ""


@builtin_func("scroll_advance")
def _scroll_advance(_engine, *args) -> str:
    """滚动校验通过后，推进状态：移除已滚出的行指纹

    .wf 用法:
        eval scroll_advance()
    """
    manager = _engine.context.get("_scroll_manager", {})
    row_fps = manager.get("row_fps", [])
    if row_fps:
        removed = row_fps.pop(0)
        logger.debug(f"scroll_advance: 移除已滚出指纹 {removed}，剩余 {len(row_fps)} 行")
    manager["scroll_count"] = manager.get("scroll_count", 0) + 1
    return ""


@builtin_func("evaluate")
def _evaluate(equip_data: dict, *args) -> dict:
    """评估装备，返回评级结果 dict

    使用当前流派规则（config/system/rules/ 下第一个 .yaml）进行评估。
    返回 EvaluationResult.to_dict() 结果。

    .wf 用法:
        eval $equip = to_equipment($scan)
        eval $result = evaluate($equip)
        if $result.rating equals "heirloom"
            log "传家宝！"
        end
    """
    if not isinstance(equip_data, dict) or not equip_data:
        return {"rating": "junk", "disqualified": True, "details": ["空数据"]}

    from ..equip_parser.models import EquipmentData
    from ..evaluator.rule_config import load_rule_config
    from ..evaluator.generic_evaluator import GenericEvaluator

    # 加载规则配置（缓存到模块级变量）
    global _cached_evaluator
    if _cached_evaluator is None:
        rules_dir = Path(__file__).resolve().parent.parent.parent / "config" / "system" / "rules"
        rule_files = list(rules_dir.glob("*.yaml"))
        if not rule_files:
            logger.warning("evaluate: 未找到规则配置文件")
            return {"rating": "unknown", "details": ["无规则配置"]}
        config = load_rule_config(rule_files[0])
        _cached_evaluator = GenericEvaluator(config)
        logger.info(f"evaluate: 加载规则 '{config.name}'")

    # dict → EquipmentData
    equip = EquipmentData.from_dict(equip_data)
    result = _cached_evaluator.evaluate(equip)
    return result.to_dict()


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


# ─── Session 持久化函数 ───────────────────────────────────

@builtin_func("save")
def _save(_engine=None, *args) -> str:
    """强制保存 session 到磁盘

    通过 engine._save_callback 触发 SessionManager.save()。

    .wf 用法:
        eval save()
    """
    if _engine is not None and _engine._save_callback is not None:
        _engine._save_callback()
        logger.info("session 已手动保存")
    else:
        logger.warning("save(): 无保存回调，跳过")
    return ""


@builtin_func("panel_rows")
def _panel_rows(_engine, scene_key: str = "", panel_key: str = "") -> int:
    """返回 panel 实际检测到的行数

    .wf 用法:
        eval $rows = panel_rows("bag_equip_detail", "bag_grid")
    """
    cal = _engine._panel_alignments.get((scene_key, panel_key))
    return cal.n_rows if cal else 0


@builtin_func("panel_cols")
def _panel_cols(_engine, scene_key: str = "", panel_key: str = "") -> int:
    """返回 panel 实际检测到的列数

    .wf 用法:
        eval $cols = panel_cols("bag_equip_detail", "bag_grid")
    """
    cal = _engine._panel_alignments.get((scene_key, panel_key))
    return cal.n_cols if cal else 0
