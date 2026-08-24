"""装备合法性判定器 — 全局唯一实现

游戏本身产不出来的装备组合，一律视为「状态异常」。异常来源通常是
OCR 误读，也可能是手工构造的模拟装备或历史脏数据。

本模块只做判定，不修数据、不丢数据：调用方拿到原因列表后自行决定是
标注（扫描路径）还是拦截（模拟装备创建路径）。判定结果写入
``EquipmentData.extra_data[ILLEGAL_KEY]``，序列化后即 ``_extra.illegal_equip``，
UI 依据该字段在装备名称后显示「!」提醒用户手工校正。

判定依据只来自游戏级装备/词组配置，以及
docs/10-game/01-equipment-system.md 中的装备产出硬规则。这里绝不读取调律规则：
调律规则属于用户玩法策略，不能决定一件装备是否能由游戏产出。

定音不在本模块判定。止戈定音是可预计的合法状态，由定音解析链路使用独立
``_extra.is_zhige_dingyin`` 标记，不得混入 ``illegal_equip``。
"""

from __future__ import annotations

from dataclasses import dataclass

from ....i18n import tr
from .equip_parser.models import Affix, EquipmentData

# extra_data / _extra 中记录异常原因的键
ILLEGAL_KEY = "illegal_equip"

# 判定码（写日志、测试断言用；面向用户展示的是 message）
CODE_DUPLICATE_AFFIX = "duplicate_affix"
CODE_ATTACK_OVERFLOW = "attack_overflow"
CODE_DIVINE_OVERFLOW = "divine_overflow"
CODE_TRANSFERRED_DIVINE = "transferred_divine"
CODE_CAP_OVERFLOW = "cap_overflow"
CODE_UNKNOWN_EQUIP_TYPE = "unknown_equip_type"
CODE_UNKNOWN_AFFIX = "unknown_affix"
CODE_INVALID_FIRST_AFFIX = "invalid_first_affix"
CODE_INVALID_AFFIX_PART = "invalid_affix_part"
CODE_WEAPON_AFFIX_MISMATCH = "weapon_affix_mismatch"
CODE_MALFORMED_AFFIX = "malformed_affix"


@dataclass(frozen=True)
class IllegalReason:
    """一条异常原因"""
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def _over_cap(cap_pct) -> bool:
    """cap_pct 是否超过 100。

    历史数据或异常输入里 cap_pct 可能是字符串甚至 None，一律按「判不出来
    就不算异常」处理——判定器宁可漏报也不能在扫描途中抛异常。
    """
    try:
        return cap_pct is not None and float(cap_pct) > 100
    except (TypeError, ValueError):
        return False


def _categories() -> tuple[str, tuple[str, ...]]:
    """返回（属攻类归属名, 神力词条归属名元组）"""
    return tr("属攻类"), (tr("增效类"), tr("武器类"))


def validate_equipment(equip: EquipmentData) -> list[IllegalReason]:
    """判定 :class:`EquipmentData` 是否为游戏可产出的合法组合。

    ``EquipmentData.affixes`` 按宫商角徵羽顺序排列且不留空位，因此列表下标
    即槽位号。若数据可能缺中间槽（如模拟装备对话框允许只填第 2 条），
    请改用 :func:`validate_equipment_dict`，它按 ``affix_N`` 键读取槽位号。
    """
    return _validate_slots(list(enumerate(equip.affixes, 1)), equip.type)


#: 组合类判定码——描述「这几条词条能不能凑在一起」。
#: 不含 CODE_CAP_OVERFLOW：那是单条词条的数值问题，与组合无关。
COMBINATION_CODES = (
    CODE_UNKNOWN_EQUIP_TYPE,
    CODE_UNKNOWN_AFFIX,
    CODE_INVALID_FIRST_AFFIX,
    CODE_INVALID_AFFIX_PART,
    CODE_WEAPON_AFFIX_MISMATCH,
    CODE_MALFORMED_AFFIX,
    CODE_DUPLICATE_AFFIX,
    CODE_ATTACK_OVERFLOW,
    CODE_DIVINE_OVERFLOW,
    CODE_TRANSFERRED_DIVINE,
)


def validate_combination_dict(equip_dict: dict) -> list[IllegalReason]:
    """只判定组合类规则，忽略数值超上限。

    用于两类场景：创建模拟装备时决定是否拦下保存；模拟词条变更（如词条培养
    分析）时跳过游戏产不出的候选组合——这些场景手里的数值可能是临时的或
    根本没算 ``cap_pct``，不该因此被否决。
    """
    return [r for r in validate_equipment_dict(equip_dict)
            if r.code in COMBINATION_CODES]


def validate_equipment_dict(equip_dict: dict) -> list[IllegalReason]:
    """判定装备 JSON dict，按 ``affix_1`` ~ ``affix_5`` 键取槽位号。

    槽位可以缺失（值为 None 或键不存在），不会影响其余槽位的槽位号。
    """
    slots: list[tuple[int, Affix]] = []
    malformed: list[IllegalReason] = []
    for i in range(1, 6):
        raw = equip_dict.get(f"affix_{i}")
        if raw is None:
            continue
        if not isinstance(raw, dict):
            malformed.append(IllegalReason(
                CODE_MALFORMED_AFFIX,
                tr("词条 {i} 数据格式异常").format(i=i),
            ))
            continue
        name = str(raw.get("name") or "").strip()
        value = raw.get("value")
        if not name or not isinstance(value, (int, float)) or isinstance(value, bool):
            malformed.append(IllegalReason(
                CODE_MALFORMED_AFFIX,
                tr("词条 {i} 缺少有效名称或数值").format(i=i),
            ))
            continue
        slots.append((i, Affix.from_dict({**raw, "name": name})))
    return malformed + _validate_slots(slots, equip_dict.get("type"))


def _validate_slots(slots: list[tuple[int, Affix]],
                    equip_type: str | None) -> list[IllegalReason]:
    """核心判定：slots 为 (槽位号, 词条) 列表，槽位号从 1 开始。

    返回全部违规原因（可能多条）；完全合法返回空列表。

    判定项：
    1. ``duplicate_affix`` —— 词条 2-5 互不重复（铁律一）。词条 2-5 是调律
       产出的，第 N 次调出不会与前面重复；但**允许与首词条相同**，首词条是
       装备自带的初始词条，不由调律产出。
    2. ``attack_overflow`` —— 属攻类词条最多 2 条（铁律二：绝不出现第三次）。
    3. ``divine_overflow`` —— 神力词条最多 1 条。
    4. ``transferred_divine`` —— 神力词条被标记为转律产出（转律不产神力词条）。
    5. 装备类型、词条池、首词条池、词条部位与武器专属词条均以游戏配置为准。
    6. ``cap_overflow`` —— 普通词条 ``cap_pct`` 超过 100，即数值高于该等级
       上限，只可能是 OCR 数值误读或等级识别错误。

    首词条（affix_1）不参与 1-4 的组合判定，但必须属于对应部位的首词条池。
    定音完全不参与本函数。
    """
    from ..config import get_game_config
    gc = get_game_config()
    attack_cat, divine_cats = _categories()
    reasons: list[IllegalReason] = []

    type_name = str(equip_type or "").strip()
    type_to_group = gc.get_type_to_group()
    group = type_to_group.get(type_name, "")
    # “武器”是部分 UI 使用的部位显示名，不是实际装备类型。
    if not group or type_name == tr("武器"):
        reasons.append(IllegalReason(
            CODE_UNKNOWN_EQUIP_TYPE,
            tr("无法识别装备类型：「{type_name}」").format(
                type_name=type_name or tr("空")),
        ))
        group = ""
    part = gc.get_group_to_part().get(group, "") if group else ""

    normal_names = set(gc.get_normal_affix_names())
    weapon_map = gc.get_all_weapon_wuxue_affixes()
    weapon_affixes = set(weapon_map.values())
    first_names = set(gc.get_first_affixes(group)) if group else set()

    # 配置级合法性：未知词条先报，避免 get_affix_parts 对未知项“缺省全部位”
    # 的宽松回退将 OCR 错词误判为合法。
    known_slots: list[tuple[int, Affix]] = []
    for slot, affix in slots:
        if affix.name not in normal_names:
            reasons.append(IllegalReason(
                CODE_UNKNOWN_AFFIX,
                tr("词条 {slot}「{name}」不在普通词条配置中")
                .format(slot=slot, name=affix.name),
            ))
            continue
        known_slots.append((slot, affix))
        if not group:
            continue
        if slot == 1:
            if affix.name not in first_names:
                reasons.append(IllegalReason(
                    CODE_INVALID_FIRST_AFFIX,
                    tr("首词条「{name}」不能出现在{part}")
                    .format(name=affix.name, part=part),
                ))
            continue
        if affix.name in weapon_affixes:
            expected = weapon_map.get(type_name, "")
            if affix.name != expected:
                detail = (tr("，该武器对应「{expected}」").format(expected=expected)
                          if expected else "")
                reasons.append(IllegalReason(
                    CODE_WEAPON_AFFIX_MISMATCH,
                    tr("词条 {slot}「{name}」与装备类型「{type_name}」不匹配{detail}")
                    .format(slot=slot, name=affix.name,
                            type_name=type_name, detail=detail),
                ))
            continue
        if part not in gc.get_affix_parts(affix.name):
            reasons.append(IllegalReason(
                CODE_INVALID_AFFIX_PART,
                tr("词条 {slot}「{name}」不能出现在{part}")
                .format(slot=slot, name=affix.name, part=part),
            ))

    # 词条 2-5：调律产出的部分。首词条（槽位 1）不参与组合类判定。
    tuned = [a for slot, a in known_slots if slot >= 2]

    # 词条 2-5 互不重复。先算出来但**最后**再追加：数量类规则（属攻超量/
    # 神力超量）报得更具体，两条神力恰好选了同一个时应提示「神力最多 1 条」
    # 而不是笼统的「不能重复」，更贴近用户要改的那一步。
    names = [a.name for a in tuned]
    dup_reasons = [
        IllegalReason(
            CODE_DUPLICATE_AFFIX,
            tr("词条 2-5 不能重复：「{name}」出现了 {n} 次")
            .format(name=name, n=names.count(name)),
        )
        for name in dict.fromkeys(names)       # 保持出现顺序，每个名字只报一次
        if names.count(name) > 1
    ]

    # 归属相关
    attack_count = 0
    divine_count = 0
    for affix in tuned:
        category = gc.get_affix_category(affix.name)
        if category == attack_cat:
            attack_count += 1
        elif category in divine_cats:
            divine_count += 1
            if affix.is_transferred:
                reasons.append(IllegalReason(
                    CODE_TRANSFERRED_DIVINE,
                    tr("神力词条「{name}」不能是转律产出：转律不会产出神力词条")
                    .format(name=affix.name),
                ))
    if attack_count > 2:
        reasons.append(IllegalReason(
            CODE_ATTACK_OVERFLOW,
            tr("属攻类词条（含无相）最多 2 条，当前词条 2-5 里有 {n} 条")
            .format(n=attack_count),
        ))
    if divine_count > 1:
        reasons.append(IllegalReason(
            CODE_DIVINE_OVERFLOW,
            tr("神力词条最多 1 条，当前词条 2-5 里有 {n} 条").format(n=divine_count),
        ))

    reasons.extend(dup_reasons)

    # 普通词条数值超上限；定音由独立链路处理，不在这里判断。
    for i, affix in slots:
        if _over_cap(affix.cap_pct):
            reasons.append(IllegalReason(
                CODE_CAP_OVERFLOW,
                tr("词条 {i}「{name}」数值 {value} 达上限的 {pct}%，超出该等级上限")
                .format(i=i, name=affix.name, value=affix.value, pct=affix.cap_pct),
            ))
    return reasons


def annotate_equipment_dict(equip_dict: dict) -> list[IllegalReason]:
    """重新判定 dict 并刷新 ``_extra.illegal_equip``。

    用于历史库存加载：新增异常类型上线后，不要求装备必须重新扫描才能被发现。
    仅修改 extra 标记，不修改装备词条或指纹。
    """
    reasons = validate_equipment_dict(equip_dict)
    raw_extra = equip_dict.get("_extra")
    extra = raw_extra if isinstance(raw_extra, dict) else {}
    if reasons:
        equip_dict["_extra"] = extra
        extra[ILLEGAL_KEY] = [r.message for r in reasons]
    else:
        extra.pop(ILLEGAL_KEY, None)
    return reasons


def annotate_equipment(equip: EquipmentData) -> list[IllegalReason]:
    """判定并把结果写入 ``extra_data[ILLEGAL_KEY]``，返回原因列表。

    合法时**移除**该键（而不是写空列表），避免重新解析后旧标记残留。
    """
    reasons = validate_equipment(equip)
    if reasons:
        equip.extra_data[ILLEGAL_KEY] = [r.message for r in reasons]
    else:
        equip.extra_data.pop(ILLEGAL_KEY, None)
    return reasons


def illegal_reasons_of(equip_dict: dict) -> list[str]:
    """从装备 JSON dict 读取异常原因，供 UI 判断是否显示「!」。

    容忍历史数据把原因写成单个字符串的情况。
    """
    raw = (equip_dict.get("_extra") or {}).get(ILLEGAL_KEY)
    if not raw:
        return []
    if isinstance(raw, str):
        return [s for s in (part.strip() for part in raw.split(",")) if s]
    return [str(x) for x in raw]
