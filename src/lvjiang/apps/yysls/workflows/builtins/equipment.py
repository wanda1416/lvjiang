"""内置函数 - 装备解析、评估与判定"""


from loguru import logger

from lvjiang.workflows.builtins._registry import builtin_func

from .....i18n import tr

# ─── 材料富解析 ───────────────────────────────────────────

@builtin_func("yysls_rich_parse")
def _yysls_rich_parse(base: dict) -> dict:
    """yysls rich 模式：从 level_text/count_text 解析 real_level / count / devoted

    解析完成后删除原始 level_text / count_text，保持 dict 扁平干净。
    """
    from ...core.recognizer.material_recognizer import _parse_number
    level_text = base.pop("level_text", "")
    count_text = base.pop("count_text", "")
    real_level = _parse_number(level_text)
    if real_level is not None:
        base["real_level"] = real_level
    if "/" in count_text:
        parts = count_text.split("/")
        devoted = _parse_number(parts[0])
        count = _parse_number(parts[-1]) if parts else None
        if devoted is not None:
            base["devoted"] = devoted
        if count is not None:
            base["count"] = count
    else:
        count = _parse_number(count_text)
        if count is not None:
            base["count"] = count
    return base


# ─── 装备解析 ───────────────────────────────────────────

@builtin_func("to_equipment")
def _to_equipment(raw_data: dict) -> dict:
    """解析装备 OCR 原始数据为标准装备字典

    返回 EquipmentData.to_dict() 结果（已自动包含 _fp 指纹），
    支持 DSL 链式字段访问：$weapon.affix_1.value / $weapon._fp 等。

    .wf 用法:
        scan [equip_weapon_detail] as $result
        eval main_weapon = to_equipment($result)
        log $main_weapon._fp
        collect $main_weapon
    """
    if not isinstance(raw_data, dict) or not raw_data:
        logger.warning("to_equipment: 输入为空或非字典")
        return {}

    from ...core.equip_parser import get_equipment_parser
    parser = get_equipment_parser()

    try:
        return parser.parse(raw_data).to_dict()
    except Exception as e:
        logger.warning(f"to_equipment: 解析失败: {e}")
        return {}


# ─── 指纹 ───────────────────────────────────────────────

@builtin_func("make_fingerprint")
def _make_fingerprint(equip_data: dict, *args) -> str:
    """基于装备数据生成去重指纹（MD5 前 8 位 hex）

    指纹由 type + level + quality + is_chengyin + 全部词条(name:value) 组成。
    空数据或空字典返回空字符串。

    注意：to_equipment 已自动计算 _fp 字段，通常无需再调用此函数。
    仅当需要对外部 dict 计算指纹时使用。

    .wf 用法:
        eval $fp = make_fingerprint($equip)
    """
    from ...core.equip_parser.models import make_fingerprint
    return make_fingerprint(equip_data)


# ─── 词条上限查询 ───────────────────────────────────────

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
    from ...config import get_game_config
    result = get_game_config().get_affix_caps(level, str(affix_name))
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
    from ...config import get_game_config
    result = get_game_config().get_affix_caps(level, str(affix_name))
    if result is None:
        return 0
    return result["chengyin"]


# ─── 装备判定 ───────────────────────────────────────────

# 高价值词条关键词（用于 is_good_equip 判定）
# TODO: 从调律规则配置中读取，当前为硬编码
_HIGH_VALUE_KEYWORDS = [
    tr("大外攻"), tr("会心"), tr("会意"), tr("三率"),
    tr("劲"), tr("敏"), tr("势"),
    tr("武学增效"), tr("首领增伤"),
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


# ─── 装备评估 ───────────────────────────────────────────

# evaluate() 缓存的判定器实例
_cached_judge = None


@builtin_func("evaluate")
def _evaluate(equip_data: dict, *args) -> dict:
    """判定装备，返回评级结果 dict

    使用穷举匹配制判定器（默认规则 huiyi_general 会意-通用）。
    返回 JudgeResult.to_dict() 结果。

    .wf 用法:
        eval $equip = to_equipment($scan)
        eval $result = evaluate($equip)
        if $result.rating equals "顶级"
            log "顶级装备！"
        end
    """
    if not isinstance(equip_data, dict) or not equip_data:
        return {"rating": tr("垃圾"), "skipped": True, "reasons": [tr("空数据")]}

    from ...core.equip_parser.models import EquipmentData
    from ...core.evaluator import get_tuning_judge

    # 判定器实例（缓存到模块级变量）
    global _cached_judge
    if _cached_judge is None:
        _cached_judge = get_tuning_judge("huiyi_general")
        logger.info(f"evaluate: 使用规则 '{_cached_judge.rule_name}'")

    # dict → EquipmentData
    equip = EquipmentData.from_dict(equip_data)
    result = _cached_judge.judge(equip)
    return result.to_dict()
