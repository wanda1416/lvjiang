"""武学、流派与玩法查询；读取统一配置快照。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .constants import (
    WUXUE_CATEGORY,
)

if TYPE_CHECKING:
    from .manager import GameConfigManager

def get_wuxue_affix_names(self: GameConfigManager) -> list[str]:
    """全部指定武学增效词条（affix_caps 这类别的 _aliases）

    调律规则 UI 增伤词条候选的唯一来源。
    """
    return self.get_aliases_for_category(WUXUE_CATEGORY)


def get_martial_arts(self: GameConfigManager) -> dict[str, dict]:
    """武学注册表：武学名 → {"weapon", "attr"}。"""
    return {k: dict(v) for k, v in self._martial_arts.items()}


def get_martial_art(self: GameConfigManager, name: str) -> dict | None:
    entry = self._martial_arts.get(str(name or "").strip())
    return dict(entry) if entry else None


def get_martial_art_weapon(self: GameConfigManager, name: str) -> str:
    """武学对应的武器；未登记返回空串。

    流派/玩法里的武器一律由此派生，不再单独录入。
    """
    return (self._martial_arts.get(str(name or "").strip()) or {}).get(
        "weapon", "")


def get_martial_art_attr(self: GameConfigManager, name: str) -> str:
    """武学对应的属性；未登记返回空串。"""
    return (self._martial_arts.get(str(name or "").strip()) or {}).get(
        "attr", "")


def get_martial_arts_by_weapon(self: GameConfigManager, weapon: str) -> list[str]:
    target = str(weapon or "").strip()
    return sorted(k for k, v in self._martial_arts.items()
                  if v.get("weapon") == target)


def check_school_weapon_consistency(self: GameConfigManager) -> list[str]:
    """流派里录入的 weapon 与武学派生的武器是否一致。

    拆分武学之前这两个字段各录各的，写成「武器=枪 + 武学=无名剑法」也存得
    下来，然后毕业率按枪算、词条按剑法找，全程静默。这里把它变成能查出来的。
    """
    problems: list[str] = []
    for school, cfg in self._schools.items():
        for side in ("main", "sub"):
            entry = (cfg or {}).get(side) or {}
            art = str(entry.get("martial_art") or "").strip()
            if not art:
                continue
            if art not in self._martial_arts:
                problems.append(f"流派 {school}.{side} 的武学 {art} 未登记")
                continue
            derived = self._martial_arts[art]["weapon"]
            recorded = str(entry.get("weapon") or "").strip()
            if recorded and recorded != derived:
                problems.append(
                    f"流派 {school}.{side} 录入武器 {recorded}，"
                    f"但武学 {art} 属于 {derived}")
    return problems


def get_playstyles(self: GameConfigManager) -> dict[str, dict]:
    """玩法注册表：玩法名 → 定义。"""
    return {k: dict(v) for k, v in self._playstyles.items()}


def get_playstyle(self: GameConfigManager, name: str) -> dict | None:
    entry = self._playstyles.get(str(name or "").strip())
    return dict(entry) if entry else None


def get_playstyles_for_arts(self: GameConfigManager, arts) -> list[str]:
    """哪些玩法登记了这组武学。

    主副只是顺序标签，判别式是「要谁的增伤」而不是谁在前，因此按**无序**
    集合匹配：选了 (斩雪刀法, 十方破阵) 时纯唐和双切都应列出来，由用户挑。
    """
    target = {str(a).strip() for a in (arts or []) if str(a).strip()}
    if not target:
        return []
    return sorted(name for name, cfg in self._playstyles.items()
                  if set(cfg.get("arts") or []) == target)


def get_playstyle_dingyin(self: GameConfigManager, name: str, part: str) -> str:
    """该玩法在某部位应有的定音；未配置返回空串。

    攻具/防具的划分沿用词条类别自己的 _parts 声明，不另建分组。
    """
    cfg = self._playstyles.get(str(name or "").strip())
    if not cfg:
        return ""
    defense_parts = self.get_affix_category_parts("指定技能增效")
    key = "defense_dingyin" if part in defense_parts else "output_dingyin"
    return cfg.get(key, "")


def get_schools(self: GameConfigManager) -> dict[str, dict]:
    """流派配置（顶层 schools：流派名 → {main: {武器: 词条}, sub: [武器]}）"""
    return dict(self._schools)


def get_school_attr(self: GameConfigManager, school: str) -> str | None:
    """获取流派的属性类型（鸣金/裂石/破竹/牵丝）"""
    cfg = self._schools.get(school, {})
    return cfg.get("attr") if cfg else None


def get_transmute_pool(self: GameConfigManager, school: str) -> list[str]:
    """获取展开为真实名称的流派转律词条库（顺序即转入优先级）

    本属/外属按该词条库所属流派解析；编辑器直接读写原始配置。
    流派不存在或未配置（含自建未填的流派）时返回空列表，
    由调用方自行决定回退策略。
    """
    cfg = self._schools.get(school, {})
    pool = cfg.get("transmute_pool") if cfg else None
    if not isinstance(pool, list):
        return []
    from ..core.tuning_rules.models import (
        DYNAMIC_AFFIXES,
        dynamic_affix_map,
        expand_affix_names,
    )

    names = [str(name).strip() for name in pool if str(name).strip()]
    aliases = dynamic_affix_map(str(cfg.get("attr") or ""), game_config=self)
    # 保留字面配置；动态项按词条库所属流派展开，不能按使用者流派展开。
    physical = list(dict.fromkeys(
        [name for name in names if name not in DYNAMIC_AFFIXES] + list(aliases)))
    return expand_affix_names(names, physical, aliases)


def get_all_transmute_pools(self: GameConfigManager) -> dict[str, list[str]]:
    """全部流派的转律词条库（流派名 → 词条列表，保持配置声明顺序）"""
    return {school: self.get_transmute_pool(school)
            for school in self._schools}
