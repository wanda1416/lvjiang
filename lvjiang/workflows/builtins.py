"""工作流内置函数注册表

提供 DSL 中可调用的内置函数，用于数据判定和条件分支。

使用方式（.wf 文件）：
    eval result = is_good_equip(last_scan)
    if is_good_equip(last_scan)
        collect_as good_equip
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

@builtin_func("contains")
def _contains(scan_result: dict, *args) -> bool:
    """检查 scan 结果中是否有任意字段包含指定文本

    .wf 用法: contains(last_scan, "调律")
    """
    if not isinstance(scan_result, dict) or not args:
        return False
    text = str(args[0])
    return any(text in v for v in scan_result.values() if isinstance(v, str))


@builtin_func("count")
def _count(scan_result: dict, *args) -> int:
    """统计 scan 结果中非空字段数量

    .wf 用法: eval n = count(last_scan)
    """
    if not isinstance(scan_result, dict):
        return 0
    return sum(1 for v in scan_result.values() if v and str(v).strip())


# ─── 装备解析函数 ─────────────────────────────────────

# 部位分类 → 代表槽位（用于 parse_slot 的分派逻辑）
_CATEGORY_REPR_SLOT = {
    "weapon": "main_weapon",
    "jewelry": "ring",
    "armor": "head",
}


def _infer_equip_category(raw_data: dict) -> str:
    """从 OCR 原始文字推断装备类别

    纯基于 equip_type 字段内容判断，不依赖场景信息。
    """
    equip_type = raw_data.get("equip_type", "")
    if "武器" in equip_type:
        return "weapon"
    # 防具特征：含防具类别名
    for cat in ["冠胄", "胸甲", "胫甲", "腕甲"]:
        if cat in equip_type:
            return "armor"
    # 首饰特征：含“环”“佩”或无武器/防具特征
    for kw in ["环", "佩"]:
        if kw in equip_type:
            return "jewelry"
    # 默认尝试防具解析（最宽松）
    return "armor"


@builtin_func("equipment_parser")
def _equipment_parser(raw_data: dict) -> dict:
    """解析装备 OCR 原始数据为 EquipmentData 字典

    纯基于 OCR 文字分析装备类型，不依赖场景信息。
    解析不出来说是解析方法的 bug，修复解析器即可。

    .wf 用法:
        scan [equip_weapon_detail]
        eval [main_weapon = equipment_parser([last_scan])]
        collect [main_weapon]
    """
    if not isinstance(raw_data, dict) or not raw_data:
        logger.warning("equipment_parser: 输入为空或非字典")
        return {}

    from ..equip_parser import EquipmentParser
    parser = EquipmentParser()

    category = _infer_equip_category(raw_data)
    slot_key = _CATEGORY_REPR_SLOT.get(category, "head")

    try:
        equip = parser.parse_slot(slot_key, raw_data)
        return {slot_key: equip}
    except Exception as e:
        logger.warning(f"equipment_parser: 解析失败: {e}")
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
        scan [equip_weapon_detail]
        if is_good_equip(last_scan)
            collect_as good_equip
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
