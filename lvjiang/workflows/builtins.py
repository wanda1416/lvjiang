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
from typing import Callable


# 全局函数注册表
_FUNCTION_REGISTRY: dict[str, Callable] = {}


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


# ─── 通用工具函数 ─────────────────────────────────────────

@builtin_func("concat")
def _concat(*args) -> str:
    """拼接多个参数为字符串

    .wf 用法:
        log concat("当前数据: ", $dict.key)
        eval $msg = concat("结果: ", $var, " 完成")
    """
    return "".join(str(arg) for arg in args)


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
def _count(scan_result: dict, *args) -> int:
    """统计 scan 结果中非空字段数量

    .wf 用法: eval n = count(result)
    """
    if not isinstance(scan_result, dict):
        return 0
    return sum(1 for v in scan_result.values() if v and str(v).strip())


# ─── 装备解析函数 ─────────────────────────────────────


@builtin_func("to_equipment")
def _to_equipment(raw_data: dict) -> dict:
    """解析装备 OCR 原始数据为标准装备字典

    返回 EquipmentData.to_dict() 结果，支持 DSL 链式字段访问：
    $weapon.affix_1.value / $weapon.base_attr_1.name 等。

    .wf 用法:
        scan [equip_weapon_detail] as $result
        eval main_weapon = to_equipment($result)
        collect $main_weapon
    """
    if not isinstance(raw_data, dict) or not raw_data:
        logger.warning("to_equipment: 输入为空或非字典")
        return {}

    from ..equip_parser import EquipmentParser
    parser = EquipmentParser()

    try:
        return parser.parse(raw_data).to_dict()
    except Exception as e:
        logger.warning(f"to_equipment: 解析失败: {e}")
        return {}


# ─── 装备判定函数 ─────────────────────────────────────────

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
