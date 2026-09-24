"""真实装备养成规则 — 新旧快照之间的合法变化判定，全局唯一实现。

真实装备来自游戏内背包扫描，只允许沿游戏本身提供的三条路径变化：

- 转律：把商角徵羽中的一个词条换成别的词条，换后槽位固定
- 承音：升到下一个已配置等级，且该等级配置允许承音
- 培养：只增不减地提高词条或同名定音数值；定音词条本身可自由切换

其余字段一律不可改。判定与展示分离：本模块只回答「这次变化合不合法」，
返回面向用户的原因文本或 ``None``；拦截还是提示由调用方决定 ——
:mod:`.repository` 在写入边界抛 ``ValueError``，养成对话框在点确定时
弹提示。两处共用本模块，避免规则各写一份后逐渐分叉。

「本次没有任何改动」不属于本模块：那是交互体验问题，不是数据合法性
问题，留给对话框自己判断。
"""

from __future__ import annotations

from .....i18n import tr
from ..affix_cap import can_cultivate_affix_values
from ..numbers import to_float

# 扫描装备的身份字段：由游戏产出时决定，养成过程中恒定不变
IMMUTABLE_KEYS: tuple[str, ...] = (
    "type", "name", "quality", "original_level", "base_attr", "base_attr_2",
)

# 词条槽位下标；1 号位是宫，为首词条不可转律
AFFIX_INDEXES: tuple[int, ...] = (1, 2, 3, 4, 5)
FIRST_AFFIX_INDEX = 1


def _affix(equip: dict, index: int) -> dict:
    return equip.get(f"affix_{index}") or {}


def check_real_development(old: dict, new: dict) -> str | None:
    """判定扫描装备从 ``old`` 到 ``new`` 的养成是否合法。

    合法返回 ``None``，否则返回可直接展示给用户的原因。
    """
    return (
        _check_identity(old, new)
        or _check_chengyin(old, new)
        or _check_dingyin(old, new)
        or _check_affixes(old, new)
    )


def _check_identity(old: dict, new: dict) -> str | None:
    changed = [key for key in IMMUTABLE_KEYS if old.get(key) != new.get(key)]
    if changed:
        return tr("真实装备的既定属性不可修改: ") + "、".join(changed)
    return None


def _check_chengyin(old: dict, new: dict) -> str | None:
    from ...config import get_game_config

    old_level = int(old.get("level") or 0)
    new_level = int(new.get("level") or 0)
    if new_level < old_level:
        return tr("承音后的装备等级不能降低")

    old_chengyin = bool(old.get("is_chengyin"))
    new_chengyin = bool(new.get("is_chengyin"))
    if old_chengyin and not new_chengyin:
        return tr("承音状态不能撤销")
    if old_chengyin == new_chengyin and new_level == old_level:
        return None
    if old_chengyin:
        return tr("已承音的装备不能再次承音")
    if new_level > old_level and not new_chengyin:
        return tr("提升装备等级必须同时承音")

    game_config = get_game_config()
    if not game_config.can_chengyin_this_season(old_level):
        return tr("本赛季该等级的装备已不能承音")
    configs = sorted(
        game_config.get_level_configs(), key=lambda item: item.level)
    current = next(
        (item for item in configs if item.level == old_level), None)
    if current is None or not current.allow_chengyin:
        return tr("该等级的装备不支持承音")
    if new_level == old_level:
        # 原地承音：装备已经在本赛季最高等阶，升无可升，只标记承音、数值不变。
        if old_level < game_config.current_equip_level():
            return tr("承音必须同时提升装备等级")
        return None
    next_level = next(
        (item.level for item in configs if item.level > old_level), None)
    if new_level != next_level:
        return tr("承音只能提升到下一个已配置等级")
    return None


def _check_dingyin(old: dict, new: dict) -> str | None:
    """普通定音槽的合法变化。

    一件装备可以同时定着普通定音和止戈定音，游戏里随时无成本切换，所以
    「这次只扫到止戈」是合法现场，不是删除了普通定音——两种定音各占一槽，
    写入时本次没带的那一槽会由仓储从旧记录合并回来。这里只管普通定音槽：
    已有的普通定音不能凭空新增或删除；定音词条本身可无成本切换，只有保持
    同名时才比较数值并要求只增不减。
    """
    old_dingyin = old.get("dingyin") or {}
    new_dingyin = new.get("dingyin") or {}
    if not new_dingyin.get("name"):
        if old_dingyin.get("name"):
            return tr("真实装备不能删除定音词条")
        return None
    if not old_dingyin.get("name"):
        return tr("真实装备不能新增定音词条")
    if old_dingyin.get("name") != new_dingyin.get("name"):
        return None
    old_value = to_float(old_dingyin.get("value"))
    new_value = to_float(new_dingyin.get("value"))
    if new_value < old_value:
        return tr("培养只能提高定音数值")
    return None


def expected_affix_name_after_level_change(
    name: str, old_level: int, new_level: int,
) -> str:
    """同一条词条在目标等级上应有的名字。

    游戏在承音跨过某个等阶时会把旧词条自动换成合并后的新词条（115 起
    单体/群体类奇术增伤 → 全奇术增伤）。这是同一条词条换了名字，不是转律，
    所以不能按「改了名字」去判它占用转律名额、也不该给它打上转律标记。
    """
    if not name or new_level <= old_level:
        return name
    from ...config import get_game_config

    return get_game_config().resolve_affix_upgrade(
        name, old_level, new_level) or name


def _becomes_chengyin(old: dict, new: dict) -> bool:
    return (not bool(old.get("is_chengyin"))
            and bool(new.get("is_chengyin")))


def _chengyin_cap(name: str, level: int) -> float | None:
    from ..affix_cap import affix_cap_value

    return affix_cap_value(level, name, chengyin=True)


def _check_affixes(old: dict, new: dict) -> str | None:
    changed_names: list[int] = []
    old_transferred: list[int] = []
    old_level = int(old.get("level") or 0)
    new_level = int(new.get("level") or 0)
    becoming_chengyin = _becomes_chengyin(old, new)
    for index in AFFIX_INDEXES:
        before = _affix(old, index)
        after = _affix(new, index)
        if before.get("is_transferred"):
            old_transferred.append(index)
        expected = expected_affix_name_after_level_change(
            str(before.get("name") or ""), old_level, new_level)
        if expected != str(after.get("name") or ""):
            changed_names.append(index)
            if not before.get("name") or not after.get("name"):
                return tr("真实装备不能新增或删除词条")
            # 转律换掉的是整条词条，新数值与旧数值不可比，跳过增长校验
            continue
        if bool(before.get("is_transferred")) != bool(
                after.get("is_transferred")):
            return tr("转律槽位标记不能单独修改")
        before_value = to_float(before.get("value"))
        after_value = to_float(after.get("value"))
        if (not can_cultivate_affix_values(old) and not becoming_chengyin
                and after_value != before_value):
            return tr("非承音装备不能培养词条数值")
        if after_value < before_value:
            # 承音的那一刻允许向下截断：承音上限低于普通上限，原地承音时
            # 已经顶满的词条必须落回承音上限，这不是「培养倒退」。
            cap = (_chengyin_cap(str(after.get("name") or ""), new_level)
                   if becoming_chengyin else None)
            if cap is None or abs(after_value - cap) > 1e-6:
                return tr("培养只能提高词条数值")

    if len(changed_names) > 1 or changed_names == [FIRST_AFFIX_INDEX]:
        return tr("转律只能修改商角徵羽中的一个词条")
    if old_transferred and changed_names and changed_names != old_transferred:
        return tr("再次转律只能修改原固定槽位")
    if changed_names:
        from ...config import get_game_config
        from .transmute import (
            judge_transmute_eligibility,
            transmute_candidates,
        )

        game_config = get_game_config()
        eligibility = judge_transmute_eligibility(old, game_config)
        index = changed_names[0]
        targets = (
            transmute_candidates(old, game_config, slots=eligibility.slots)
            if eligibility.eligible else {}
        )
        if _affix(new, index).get("name") not in targets.get(index, []):
            return tr("当前真实装备不支持该转律变化")
    if changed_names and not _affix(new, changed_names[0]).get(
            "is_transferred"):
        return tr("转律后的词条必须标记 is_transferred")
    return None
