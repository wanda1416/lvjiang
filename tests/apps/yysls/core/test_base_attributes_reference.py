"""预置基础属性（115_s2）的口径回归。

数值是 115 阶满养成快照，口径为「8 件装备、装备套装（两件套）与弓玦都不在快照
里，但四条叠音与默认心法在」。生成流程见
``scripts/yysls/export_reference_base_attributes.py``；这里冻结关键字段，防止有人
手工改 YAML 或改坏生成链路后，毕业率悄悄漂移。
"""
from __future__ import annotations

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.config.base_attributes import get_base_attribute_preset
from lvjiang.apps.yysls.core.graduation.scoring import equipment_attrs

#: 流派 → (最小外功, 最大外功, 精准率, 会心率, 会意率)；三率是抗性前的原始值。
REFERENCE_115_S2 = {
    "鸣金·虹": (1420.76, 3013.677, 1.153, 0.5293, 0.3863),
    "鸣金·影": (1462.66, 3097.377, 1.153, 0.5293, 0.3863),
    "鸣金·霆": (1462.66, 3097.377, 1.153, 0.5293, 0.3863),
    "裂石·威": (1553.857, 2827.777, 1.153, 0.6903, 0.2646),
    "裂石·钧": (1693.357, 2741.18, 1.153, 0.7737, 0.2646),
    "牵丝·霖": (1722.857, 2827.98, 1.153, 0.7897, 0.2646),
    "牵丝·翊": (1693.357, 2741.18, 1.282, 0.7737, 0.2646),
    "牵丝·玉": (1591.157, 2815.48, 1.282, 0.7897, 0.2646),
    "破竹·尘": (1679.457, 2741.18, 1.282, 0.7897, 0.2646),
    "破竹·风": (1679.457, 2741.18, 1.282, 0.7897, 0.2646),
    "破竹·鸢": (1693.357, 2741.18, 1.282, 0.7737, 0.2646),
    "破竹·樽": (1693.357, 2741.18, 1.282, 0.7737, 0.2646),
}

#: 参考站「新建方案」在 8 件 115 金装、玉斗套装、会意弓玦下的面板外功。
REFERENCE_PANEL_WITH_EQUIPMENT = (1800.76, 3770.677)

_GOLD_115 = {
    "main_weapon": "剑", "sub_weapon": "剑", "ring": "环", "pendant": "佩",
    "head": "冠胄", "chest": "胸甲", "leg": "胫甲", "wrist": "腕甲",
}


@pytest.mark.parametrize("school", sorted(REFERENCE_115_S2))
def test_preset_matches_frozen_snapshot(school):
    preset = get_base_attribute_preset(school, "115_s2")
    assert preset is not None, f"{school} 缺少 115_s2 预置基础属性"
    assert (preset.level, preset.stage, preset.playstyle) == (115, 2, None)
    got = (
        preset.stats["min_outer"], preset.stats["max_outer"],
        preset.stats["precision"], preset.stats["crit_rate"],
        preset.stats["intent_rate"],
    )
    assert got == pytest.approx(REFERENCE_115_S2[school], abs=5e-3)


def test_preset_excludes_set_and_bow_but_keeps_dieyin():
    """8 件金装固有（380/757）由装备链路叠加，叠音留在快照里，两件套与弓玦不在。"""
    preset = get_base_attribute_preset("鸣金·虹", "115_s2")
    assert preset is not None
    equipped = {
        slot: {"type": weapon, "level": 115, "quality": "gold"}
        for slot, weapon in _GOLD_115.items()
    }
    gear = equipment_attrs(equipped, get_game_config())
    composed = (preset.stats["min_outer"] + gear.min_outer,
                preset.stats["max_outer"] + gear.max_outer)
    assert composed == pytest.approx(REFERENCE_PANEL_WITH_EQUIPMENT, abs=5e-3)
    # 三率存的是抗性前的原始值；参考站面板显示的是除以 2.8 之后的数（会心 18.90%）。
    assert preset.stats["crit_rate"] / 2.8 == pytest.approx(0.1890, abs=5e-4)

def test_preset_carries_five_dims():
    """预置要带上公共底子五维（五个都是 328），供评分链补算武学天赋。"""
    for school in sorted(REFERENCE_115_S2):
        preset = get_base_attribute_preset(school, "115_s2")
        assert preset is not None
        for key in ("dim_jin", "dim_min", "dim_shi", "dim_ti", "dim_yu"):
            assert preset.stats.get(key) == pytest.approx(328.0), f"{school} 缺少 {key}"


def _gold_loadout(extra: dict[str, list[tuple[str, float]]] | None = None) -> dict:
    equipped = {
        slot: {"type": weapon, "level": 115, "quality": "gold"}
        for slot, weapon in _GOLD_115.items()
    }
    for slot, rows in (extra or {}).items():
        for index, (name, value) in enumerate(rows, start=1):
            equipped[slot][f"affix_{index}"] = {"name": name, "value": value}
    return equipped


def _panel(attrs_base, equipped) -> object:
    from lvjiang.apps.yysls.core.graduation.scoring import graduation_input

    return graduation_input(attrs_base, equipped, "鸣金·虹", get_game_config())


def test_equipment_five_dims_top_up_talent_only_when_snapshot_has_dims():
    """装备带来的五维只补「武学天赋」差额；快照没五维时行为完全不变。"""
    preset = get_base_attribute_preset("鸣金·虹", "115_s2")
    assert preset is not None
    preset_base = _as_attrs(preset.stats)
    legacy_base = _as_attrs({k: v for k, v in preset.stats.items() if not k.startswith("dim_")})

    plain = _gold_loadout()
    with_shi = _gold_loadout({"main_weapon": [("势", 83.1)]})

    linear = 83.1 * 0.00038 / 2.8          # 势 → 会意（线性，装备侧一直这样换算）
    talent = 0.08 * 83.1 / 528 / 2.8       # 新增：武学天赋从 328/528 补到 411.1/528

    legacy_gain = _panel(legacy_base, with_shi).intent_rate - _panel(legacy_base, plain).intent_rate
    # 旧条目没有五维：只有线性换算，没有武学天赋补差。
    assert legacy_gain == pytest.approx(linear, rel=1e-9)

    preset_gain = _panel(preset_base, with_shi).intent_rate - _panel(preset_base, plain).intent_rate
    assert preset_gain == pytest.approx(linear + talent, rel=1e-9)

    # 对手上没有五维词条的装备，两条路径结果必须完全一致。
    assert _panel(preset_base, plain).intent_rate == pytest.approx(
        _panel(legacy_base, plain).intent_rate, rel=1e-12)


def _as_attrs(stats: dict) -> object:
    from lvjiang.apps.yysls.core.combat.combat_attrs import CombatAttributes
    from lvjiang.apps.yysls.core.graduation.context import gongjue_attrs

    attrs = CombatAttributes()
    for key, value in stats.items():
        if hasattr(attrs, key):
            setattr(attrs, key, float(value))
    return attrs + gongjue_attrs("会意", get_game_config())
