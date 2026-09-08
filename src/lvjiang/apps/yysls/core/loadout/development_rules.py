"""扫描装备养成规则 — 新旧快照之间的合法变化判定，全局唯一实现。

扫描装备来自游戏内真实背包，只允许沿游戏本身提供的三条路径变化：

- 转律：把商角徵羽中的一个词条换成别的词条，换后槽位固定
- 承音：升到下一个已配置等级，且该等级配置允许承音
- 培养：只增不减地提高词条或定音数值

其余字段一律不可改。判定与展示分离：本模块只回答「这次变化合不合法」，
返回面向用户的原因文本或 ``None``；拦截还是提示由调用方决定 ——
:mod:`.repository` 在写入边界抛 ``ValueError``，养成对话框在点确定时
弹提示。两处共用本模块，避免规则各写一份后逐渐分叉。

「本次没有任何改动」不属于本模块：那是交互体验问题，不是数据合法性
问题，留给对话框自己判断。
"""

from __future__ import annotations

from .....i18n import tr

# 扫描装备的身份字段：由游戏产出时决定，养成过程中恒定不变
IMMUTABLE_KEYS: tuple[str, ...] = (
    "type", "name", "quality", "original_level", "base_attr", "base_attr_2",
)

# 词条槽位下标；1 号位是宫，为首词条不可转律
AFFIX_INDEXES: tuple[int, ...] = (1, 2, 3, 4, 5)
FIRST_AFFIX_INDEX = 1


def _affix(equip: dict, index: int) -> dict:
    return equip.get(f"affix_{index}") or {}


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


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
        return tr("扫描装备的既定属性不可修改: ") + "、".join(changed)
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
    if new_level == old_level:
        if old_chengyin != new_chengyin:
            return tr("承音必须同时提升装备等级")
        return None

    configs = sorted(
        get_game_config().get_level_configs(), key=lambda item: item.level)
    current = next(
        (item for item in configs if item.level == old_level), None)
    next_level = next(
        (item.level for item in configs if item.level > old_level), None)
    if (old_chengyin or not new_chengyin or current is None
            or not current.allow_chengyin or new_level != next_level):
        return tr("承音只能提升到下一个已配置等级")
    return None


def _check_dingyin(old: dict, new: dict) -> str | None:
    old_dingyin = old.get("dingyin") or {}
    new_dingyin = new.get("dingyin") or {}
    if old_dingyin.get("name") != new_dingyin.get("name"):
        return tr("扫描装备不能新增、删除或更换定音词条")
    if _num(new_dingyin.get("value")) < _num(old_dingyin.get("value")):
        return tr("培养只能提高定音数值")
    return None


def _check_affixes(old: dict, new: dict) -> str | None:
    changed_names: list[int] = []
    old_transferred: list[int] = []
    for index in AFFIX_INDEXES:
        before = _affix(old, index)
        after = _affix(new, index)
        if before.get("is_transferred"):
            old_transferred.append(index)
        if before.get("name") != after.get("name"):
            changed_names.append(index)
            if not before.get("name") or not after.get("name"):
                return tr("扫描装备不能新增或删除词条")
            # 转律换掉的是整条词条，新数值与旧数值不可比，跳过增长校验
            continue
        if bool(before.get("is_transferred")) != bool(
                after.get("is_transferred")):
            return tr("转律槽位标记不能单独修改")
        if _num(after.get("value")) < _num(before.get("value")):
            return tr("培养只能提高词条数值")

    if len(changed_names) > 1 or changed_names == [FIRST_AFFIX_INDEX]:
        return tr("转律只能修改商角徵羽中的一个词条")
    if old_transferred and changed_names and changed_names != old_transferred:
        return tr("再次转律只能修改原固定槽位")
    if changed_names and not _affix(new, changed_names[0]).get(
            "is_transferred"):
        return tr("转律后的词条必须标记 is_transferred")
    return None
