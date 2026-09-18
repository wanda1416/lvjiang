"""战斗属性聚合与动态抗性测试。"""

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.combat.combat_attrs import (
    CombatAttributes,
    aggregate_equipment_attrs,
    apply_bonus_resistance,
    apply_penetration_resistance,
    apply_three_rate_resistance,
    build_graduation_attrs,
    calculate_judgment_outcomes,
    convert_five_dims,
    has_resistance,
)
from lvjiang.apps.yysls.ui.loadout.combat.attrs_tab import CombatAttrsTab
from tests.case_matrix import case_matrix


def test_wuxiang_penetration_is_a_fixed_numeric_field() -> None:
    attrs = aggregate_equipment_attrs({
        "head": {"dingyin": {"name": "无相穿透", "value": 14.5}},
    })

    assert attrs.wuxiang_pen == pytest.approx(14.5)
    assert "wuxiang_pen" not in attrs.extra_attrs


@case_matrix("name", [
    "十方破阵武学技增伤",
    "千机索天重击增伤",
    "明川药典治疗技增疗",
])
def test_configured_skill_bonus_is_aggregated_as_dynamic_bonus(name: str) -> None:
    attrs = aggregate_equipment_attrs({
        "head": {"dingyin": {"name": name, "value": 8.0}},
    })

    assert attrs.extra_attrs[name] == pytest.approx(0.08)
    assert has_resistance(name)


def test_unconfigured_skill_like_name_is_not_accepted_by_suffix() -> None:
    name = "不存在的武学技增伤"
    attrs = aggregate_equipment_attrs({
        "head": {"dingyin": {"name": name, "value": 8.0}},
    })

    assert name not in attrs.extra_attrs
    assert not has_resistance(name)


def test_resistance_functions_accept_level_config_values() -> None:
    assert apply_three_rate_resistance("crit_rate", 1.0, 100) == pytest.approx(0.5)
    assert apply_bonus_resistance(0.3, resistance=20) == pytest.approx(0.25)
    assert apply_penetration_resistance(12, 36, 20) == pytest.approx(46)


def test_combat_panel_uses_active_season_resistances() -> None:
    assert CombatAttrsTab._current_resistances() == (145.0, 15.0)


def test_judgment_card_menu_toggles_shared_yellow_display(monkeypatch) -> None:
    labels = []
    toggles = []

    class FakeMenu:
        def __init__(self, _parent):
            self.action = object()

        def addAction(self, label):
            labels.append(label)
            return self.action

        def exec(self, _position):
            return self.action

    class FakeCard:
        @staticmethod
        def mapToGlobal(position):
            return position

    fake = type("FakeCombatTab", (), {})()
    fake._judgment_card = FakeCard()
    fake._resistance_only = False
    fake._set_resistance_only = toggles.append
    monkeypatch.setattr(
        "lvjiang.apps.yysls.ui.loadout.combat.attrs_tab.QMenu", FakeMenu)
    monkeypatch.setattr(
        "lvjiang.apps.yysls.ui.loadout.combat.attrs_tab.tr", lambda text: text)

    CombatAttrsTab._show_judgment_display_menu(fake, object())
    fake._resistance_only = True
    CombatAttrsTab._show_judgment_display_menu(fake, object())

    assert labels == [
        "仅展示黄字三率和增效",
        "展示白字和黄字三率和增效",
    ]
    assert toggles == [True, False]


def test_yellow_display_toggle_refreshes_and_persists_once() -> None:
    calls = []
    fake = type("FakeCombatTab", (), {})()
    fake._resistance_only = False
    fake._refresh_display = lambda: calls.append("refresh")
    fake._save_selection = lambda: calls.append("save")

    CombatAttrsTab._set_resistance_only(fake, True)
    CombatAttrsTab._set_resistance_only(fake, True)

    assert fake._resistance_only is True
    assert calls == ["refresh", "save"]


def test_build_graduation_attrs_is_the_shared_resistance_boundary() -> None:
    base = CombatAttributes(
        precision=0.8, outer_pen=36, lieshi_pen=36, boss_bonus=0.08,
    )
    equipment = CombatAttributes(
        precision=0.2, outer_pen=12, wuxiang_pen=14.5, boss_bonus=0.015,
    )
    result = build_graduation_attrs(base, equipment, "裂石·钧")

    assert result.precision == pytest.approx(
        apply_three_rate_resistance("precision", 1.0, 145))
    assert result.outer_pen == pytest.approx(
        apply_penetration_resistance(12, 36, 15))
    assert result.lieshi_pen == pytest.approx(
        apply_penetration_resistance(14.5, 36, 15))
    assert result.boss_bonus == pytest.approx(
        apply_bonus_resistance(0.095, resistance=15))


def test_fold_wuxiang_pen_is_the_single_place_wuxiang_is_mapped() -> None:
    """面板展示与毕业率输入都用同一份折算：无相穿透只在这里进本流派属攻穿透。"""
    from lvjiang.apps.yysls.core.combat.combat_attrs import fold_wuxiang_pen

    equipment = CombatAttributes(lieshi_pen=3.0, wuxiang_pen=14.5)
    folded = fold_wuxiang_pen(equipment, "lieshi_pen")
    assert folded.lieshi_pen == pytest.approx(17.5)
    assert folded.wuxiang_pen == 0.0                       # 已并入，清零保证幂等
    assert equipment.lieshi_pen == pytest.approx(3.0)     # 不改入参
    assert equipment.wuxiang_pen == pytest.approx(14.5)
    # 流派无属性 / 无无相穿透：原样返回
    assert fold_wuxiang_pen(equipment, None) is equipment
    assert fold_wuxiang_pen(CombatAttributes(lieshi_pen=3.0), "lieshi_pen").lieshi_pen == 3.0
    # 面板先折算再交给 build_graduation_attrs：与直接传原始装备值结果一致，不二次相加
    base = CombatAttributes(lieshi_pen=36)
    once = build_graduation_attrs(base, equipment, "裂石·钧")
    twice = build_graduation_attrs(base, folded, "裂石·钧")
    assert once.lieshi_pen == twice.lieshi_pen == pytest.approx(
        apply_penetration_resistance(17.5, 36, 15))


def test_judgment_outcomes_use_yellow_rates_and_direct_rates() -> None:
    attrs = CombatAttributes(
        precision=1.0,
        crit_rate=1.0,
        intent_rate=0.4,
        direct_crit=0.1,
        direct_intent=0.05,
    )

    rates = calculate_judgment_outcomes(attrs, resistance=100)

    assert rates.precision == pytest.approx(0.825)
    assert rates.crit_chance == pytest.approx(0.6)
    assert rates.intent_chance == pytest.approx(0.25)
    assert rates.crit == pytest.approx(0.495)
    assert rates.intent == pytest.approx(0.25)
    assert rates.normal == pytest.approx(0.12375)
    assert rates.scratch == pytest.approx(0.13125)
    assert sum((rates.crit, rates.intent, rates.normal, rates.scratch)) \
        == pytest.approx(1.0)


def test_judgment_outcomes_prioritize_intent_when_rates_overflow() -> None:
    attrs = CombatAttributes(
        precision=1.0,
        crit_rate=0.8,
        intent_rate=0.4,
        direct_crit=0.2,
        direct_intent=0.2,
    )

    rates = calculate_judgment_outcomes(attrs, resistance=0)

    assert rates.intent == pytest.approx(0.6)
    assert rates.crit == pytest.approx(0.4)
    assert rates.normal == pytest.approx(0.0)
    assert rates.scratch == pytest.approx(0.0)


# ── 五维转换 ──────────────────────────────────────────────

#: 归一容差。当前实测系数下最差 0.986（势/敏），留 2% 余量；
#: 三率侧那 1.4% 缺口查清后应收紧。
_FIVE_DIM_TOLERANCE = 0.02

_DIM_ARG = {"劲": "jin", "势": "shi", "敏": "min_val"}


def _cap(level: int, category: str) -> float:
    entry = get_game_config().get_affix_caps(level, category)
    assert entry is not None, f"affix_caps 缺少 {level} 级的 {category}"
    return float(entry["cap"])


@case_matrix("dimension", ["劲", "势", "敏"])
def test_one_full_dimension_affix_is_worth_exactly_one_affix(dimension: str) -> None:
    """一条满值五维词条产出的各项，按各自词条满值归一后相加应为 1。

    这是判断转换系数对不对的硬标准。早期那组自行拟合的系数
    （敏 1.0小外攻 + 0.075%会心）归一后是 1.044——超过一整条词条，
    不可能成立，正是靠这条不变量认出来的。

    系数与 affix_caps 任一侧改动都会打破它，所以两边漂移都会在这里红灯。
    """
    level = 110
    attrs = convert_five_dims(**{_DIM_ARG[dimension]: _cap(level, "五维属性")})

    normalized = (attrs.min_outer + attrs.max_outer) / _cap(level, "外功攻击")
    normalized += attrs.crit_rate * 100 / _cap(level, "会心率")
    normalized += attrs.intent_rate * 100 / _cap(level, "会意率")

    assert normalized == pytest.approx(1.0, abs=_FIVE_DIM_TOLERANCE), (
        f"{dimension} 归一后为 {normalized:.4f}，偏离一整条词条超过 "
        f"{_FIVE_DIM_TOLERANCE:.0%}；系数或 affix_caps 有一侧不对"
    )


def test_five_dimension_conversion_targets() -> None:
    """每一维只落到它该落的字段上，不串味。"""
    jin = convert_five_dims(jin=100)
    assert (jin.min_outer, jin.max_outer) == pytest.approx((22.5, 136.0))
    assert (jin.crit_rate, jin.intent_rate) == (0.0, 0.0)

    shi = convert_five_dims(shi=100)
    assert (shi.max_outer, shi.intent_rate) == pytest.approx((90.0, 0.038))
    assert (shi.min_outer, shi.crit_rate) == (0.0, 0.0)

    agility = convert_five_dims(min_val=100)
    assert (agility.min_outer, agility.crit_rate) == pytest.approx((90.0, 0.076))
    assert (agility.max_outer, agility.intent_rate) == (0.0, 0.0)


def test_body_and_defence_produce_nothing_trackable() -> None:
    """体/御 只出生命值与防御，CombatAttributes 不追踪，必须是全零。"""
    assert convert_five_dims(ti=100, yu=100) == CombatAttributes()


def test_five_dimension_affixes_are_summed_before_conversion() -> None:
    """多件装备上的同一维先累计再换算，避免逐件取整放大误差。"""
    split = aggregate_equipment_attrs({
        "head": {"affix_1": {"name": "敏", "value": 40.0}},
        "chest": {"affix_1": {"name": "敏", "value": 36.8}},
    })

    assert split.min_outer == pytest.approx(convert_five_dims(min_val=76.8).min_outer)
    assert split.crit_rate == pytest.approx(convert_five_dims(min_val=76.8).crit_rate)


def test_five_dimension_affixes_reach_the_aggregate() -> None:
    """五维词条要真的进聚合结果——它经反推路径决定基础属性，
    静默丢弃会让基础属性整体偏高。"""
    attrs = aggregate_equipment_attrs({
        "head": {"affix_1": {"name": "劲", "value": 76.8}},
    })

    assert attrs.min_outer > 0 and attrs.max_outer > 0


def test_play_style_reverse_derivation_ignores_panel_assumptions(monkeypatch):
    """反推基础属性 = 游戏面板 − 真实装备 − 弓玦。游戏面板对应的是真实穿戴，
    面板上勾着的满承音/满等级/模拟转律不能被扣进去；否则勾选状态一变，
    同一份面板数值反推出不同的基础属性。"""
    from lvjiang.apps.yysls.config import get_game_config
    from lvjiang.apps.yysls.core.graduation.assumptions import Assumptions
    from lvjiang.apps.yysls.core.graduation.scoring import equipment_attrs
    from lvjiang.apps.yysls.ui.loadout.combat.play_style_dialog import (
        PlayStyleDialogMixin,
    )

    gc = get_game_config()
    raw = {"main_weapon": {
        "type": "剑", "name": "剑", "level": 100, "quality": "gold",
        "affix_1": {"name": "最大外功攻击", "value": 80.0},
        "affix_2": {"name": "会心率", "value": 5.0, "unit": "%"},
        "affix_3": {"name": "劲", "value": 40},
    }}
    true_base = CombatAttributes(min_outer=1000, max_outer=2000, crit_rate=0.10)
    panel = true_base + equipment_attrs(raw, gc)      # 游戏面板 = 基础 + 真实装备

    saved: dict = {}

    class _Host(PlayStyleDialogMixin):
        # 面板上勾着满等级 + 满承音：反推必须无视
        def assumptions(self):
            return Assumptions(full_level=110, full_chengyin=True)

        def _equipped_snapshot(self):
            return raw

        def _compute_gongjue_attrs(self):
            return CombatAttributes()

        def _save_play_style(self, school, name, base_attrs):
            saved["base"] = base_attrs

        def _refresh_play_styles(self):
            pass

        _combo_play_style = type("C", (), {"setCurrentText": lambda self, _n: None})()

    monkeypatch.setattr(
        "PyQt6.QtWidgets.QMessageBox.information", lambda *a, **k: None)
    PlayStyleDialogMixin._commit_play_style(
        _Host(), "鸣金·虹", "测试", panel, workflow_triggered=False)

    base = saved["base"]
    assert base.min_outer == pytest.approx(true_base.min_outer)
    assert base.max_outer == pytest.approx(true_base.max_outer)
    assert base.crit_rate == pytest.approx(true_base.crit_rate)
