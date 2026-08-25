"""调律事件的枚举规范化：OCR/配置文本 → 服务端稳定 ascii 码。

本模块是调律事件通道最关键的 PII 防线（词条名来自 OCR，区域配错时
理论上能读到屏幕上任何文字）——任何一个函数都不允许「查不到就原样
透传」，查不到一律返回 None，调用方据此丢弃整条事件。

只与游戏配置里已有的规范枚举比对（``get_type_to_group``/
``get_normal_affix_names``/``FOOD_LABELS`` 等都是全局唯一真源），不自建
一份可能与它们脱节的映射表——这也是为什么切换界面语言不影响这里的分类
结果：这些常量在游戏文本层面本就不受工具自身 UI 语言影响（游戏客户端
只有中文，``tr()`` 对未登记翻译的游戏领域词汇原样返回中文）。
"""
from __future__ import annotations

from ..core.tuning_rules.models import FOOD_LABELS

_PART_CHOICES = ("weapon", "ring", "pendant", "head", "chest", "leg", "wrist")
_QUALITY_CHOICES = ("blue", "purple", "gold")
_FOOD_CHOICES = ("none", "gold", "purple", "rainbow")
_MODE_CHOICES = ("normal", "force_tune", "tune_full_recycle")

_FOOD_INDEX = dict(zip(FOOD_LABELS, ("gold", "purple", "rainbow"), strict=True))


def part_choices() -> tuple[str, ...]:
    return _PART_CHOICES


def quality_choices() -> tuple[str, ...]:
    return _QUALITY_CHOICES


def food_choices() -> tuple[str, ...]:
    return _FOOD_CHOICES


def mode_choices() -> tuple[str, ...]:
    return _MODE_CHOICES


def normalize_part(equip_type: str | None) -> str | None:
    """装备 type（剑/枪/环/佩/冠胄/...） → 部位分组 key。"""
    if not equip_type:
        return None
    from ..config import get_game_config
    return get_game_config().get_type_to_group().get(equip_type)


def normalize_weapon_type(equip_type: str | None, part: str | None) -> str | None:
    """part == "weapon" 时的具体武器类型（剑/枪/...）；非武器返回 None。"""
    if part != "weapon" or not equip_type:
        return None
    from ..config import get_game_config
    if equip_type in get_game_config().get_weapon_types():
        return equip_type
    return None


def weapon_type_choices() -> list[str]:
    from ..config import get_game_config
    return get_game_config().get_weapon_types()


def normalize_quality(quality: str | None) -> str | None:
    if quality in _QUALITY_CHOICES:
        return quality
    return None


def normalize_food(food_label: str | None) -> str | None:
    """狗粮显示名 → ascii key；未加狗粮（空串）→ "none"；未知标签 → None（丢弃事件）。"""
    if not food_label:
        return "none"
    return _FOOD_INDEX.get(food_label)


def normalize_affix_name(name: str | None) -> str | None:
    """结果词条名：必须精确命中普通词条池，否则 None（丢弃事件）。

    普通词条池的 ``get_normal_affix_names()`` 已经是规则可引用的标准
    别名全集，不需要再经一层"类别"归并——归并会丢失「最大/最小外功攻击」
    这类对调律策略分析有意义的区分度。
    """
    if not name:
        return None
    from ..config import get_game_config
    if name in get_game_config().get_normal_affix_names():
        return name
    return None


def affix_choices() -> list[str]:
    from ..config import get_game_config
    return get_game_config().get_normal_affix_names()


def normalize_active_rule(rule_keys: list[str] | None) -> str:
    """当前启用的调律规则集合 → 排序后以 "+" 拼接的稳定字符串。

    调律可以同时启用多条规则联合判定，没有单一"决策者"概念，故用
    规则 key 的集合而非单个 key 表达"在什么规则激活的情况下"。
    过滤掉不在当前规则清单里的 key（不吞整个事件——active_rule 是
    元数据而非主载荷，个别未知 key 不影响其余字段的价值）。
    """
    from ..core.tuning_rules import get_tuning_rule_manager
    known = set(get_tuning_rule_manager().get_rules().keys())
    kept = sorted(k for k in (rule_keys or []) if k in known)
    return "+".join(kept) if kept else "none"


def game_config_customized() -> bool:
    """用户是否在 local 层覆盖过 game_config.yaml——覆盖后 cap_pct 口径
    可能失真，让服务端能筛掉这部分样本。"""
    from ....core.config.resolver import get_resolver
    return (get_resolver().local_dir / "yysls" / "game_config.yaml").exists()


def current_season_number() -> int | None:
    from ..config import get_game_config
    season = get_game_config().current_season()
    return season.season_number if season else None
