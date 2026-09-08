"""鸣金·虹内置资料与角色求值契约，所有写入使用临时配置层。"""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from lvjiang.apps.yysls.core.attr_model.character import (
    REL_DIR,
    CharacterProfileManager,
    evaluate_profile,
    parse_profile,
)
from lvjiang.apps.yysls.core.attr_model.models import AttrModelError, Formula
from lvjiang.core.config.resolver import ConfigResolver


@pytest.fixture
def manager(tmp_path):
    root = Path(__file__).resolve().parents[2]
    system = tmp_path / "system"
    target = system / REL_DIR
    target.mkdir(parents=True)
    (target / "鸣金·虹.yaml").write_bytes((root / "config/system" / REL_DIR / "鸣金·虹.yaml").read_bytes())
    return CharacterProfileManager(ConfigResolver(system_dir=system, local_dir=tmp_path / "local", dev_mode=False))


@pytest.fixture
def profile(manager):
    return manager.load("鸣金·虹")


def test_shipped_data_records_confirmed_growth_and_unknown_details(profile):
    assert profile.growth["character_level"] == 23
    assert profile.growth["solo_level"] == 23
    assert set(profile.growth["martial_arts"].values()) == {19}
    assert set(profile.growth["inner_ways"].values()) == {6}
    assert len(profile.growth["oddities"]) == 7
    assert all(v is None for v in profile.growth["oddities"].values())
    result = evaluate_profile(profile)
    for name in ("dim_jin", "dim_shi", "dim_min", "dim_ti", "dim_yu"):
        assert result.resolved.panel.values[name] == 283
    assert result.resolved.panel.values["health_max"] == 26200
    assert result.resolved.panel.values["outer_defense"] == 51
    assert result.resolved.panel.values["mastery"] == 4951
    assert result.resolved.panel.values["precision"] == .256
    assert "基础天赋·已知五维估计" in result.missing
    assert all("蹊跷·" + name in result.missing for name in profile.growth["oddities"])
    assert profile.raw["sources"]["基础天赋·已知五维估计"]["status"] == "partial"


def test_equipment_participates_before_threshold_formulas(profile):
    # 原始五维与原始属攻，而非直接塞最终面板。283+363=646势。
    result = evaluate_profile(profile, equipment={"dim_shi": 363, "max_mingjin": 600, "intent_rate": .5})
    panel = result.resolved.panel
    assert panel.values["dim_shi"] == 646
    assert panel.values["max_mingjin"] == pytest.approx(1253.3)
    assert panel.values["mingjin_pen"] == 36  # 武学30 + 千山法6
    assert panel.values["mingjin_bonus"] == .15
    assert panel.values["stamina_max"] == 20
    increases = [m.delta for m in panel.modifiers if m.source_id == "无名剑法·外功攻击提升"]
    assert increases == [122]
    assert profile.observations["max_outer"] == 5832.5
    assert panel.values["max_outer"] != 5832.5  # 观测不参与加总


def test_formula_chain_uses_all_contributions_and_is_order_independent(profile):
    raw = deepcopy(profile.raw)
    raw["sources"] = dict(reversed(list(raw["sources"].items())))
    reversed_profile = parse_profile(raw)
    assert evaluate_profile(reversed_profile).resolved.panel.values == pytest.approx(
        evaluate_profile(profile).resolved.panel.values)


def test_shared_declarations_count_once_and_do_not_change_static_damage(profile):
    result = evaluate_profile(profile)
    shared = [d["shared_key"] for _, d in result.declarations if d.get("shared_key")]
    assert len(shared) == len(set(shared)) == 4
    # 条件会意18%与共享鸣金50%不能混入无条件面板。
    assert result.resolved.panel.values["intent_dmg"] == .052
    assert result.resolved.panel.values["mingjin_bonus"] < .15
    third = next(d for _, d in result.declarations if d["id"] == "third_intent")
    assert third["condition"] == {"all": [
        {"field": "target.exhausted", "op": "eq", "value": True},
        {"field": "target.kind", "op": "eq", "value": "non_player"}]}


def test_growth_selects_exact_level_snapshot_and_accumulates_inner_tiers(profile):
    growth = deepcopy(profile.growth)
    growth["inner_ways"]["无名心法"] = 2
    result = evaluate_profile(profile, growth=growth)
    assert result.resolved.panel.values["direct_intent"] == 0
    assert any(m.source_id == "无名心法·2重属性" for m in result.resolved.panel.modifiers)
    assert not any(d["id"] == "qi_surge_cast" for _, d in result.declarations)
    growth["character_level"] = 22
    result = evaluate_profile(profile, growth=growth)
    assert result.resolved.panel.values["health_max"] == 0
    assert "个人等级·23级累计" in result.missing
    growth["solo_level"] = 22
    other_level = evaluate_profile(profile, growth=growth)
    assert "千山法·2重属性" in other_level.missing
    assert any(m.source_id == "千山法·5重属性" for m in other_level.resolved.panel.modifiers)


def test_two_cumulative_levels_never_add_together(profile):
    raw = deepcopy(profile.raw)
    other = deepcopy(raw["sources"]["个人等级·23级累计"])
    other["basis"]["character_level"] = 22
    other["label"] = "个人等级·22级累计"
    other["static"]["stats"]["health_max"] = 20000
    raw["sources"]["个人等级·22级累计"] = other
    result = evaluate_profile(parse_profile(raw))
    assert result.resolved.panel.values["health_max"] == 26200


def test_talent_and_oddity_can_be_completed_without_changing_schema(profile):
    raw = deepcopy(profile.raw)
    source = raw["sources"]["蹊跷·清河"]
    source.update(status="complete", static={"stats": {"min_wuxiang": 12, "health_max": 100}})
    source["basis"]["progress"] = 5
    raw["growth"]["oddities"]["清河"] = 5
    result = evaluate_profile(parse_profile(raw))
    assert "蹊跷·清河" not in result.missing
    assert result.resolved.panel.values["min_wuxiang"] == 12
    assert result.resolved.panel.values["health_max"] == 26300


@pytest.mark.parametrize("bad", ["cycle", "condition", "shared", "empty", "tier", "field"])
def test_invalid_edits_do_not_write_any_layer(manager, profile, bad):
    raw = deepcopy(profile.raw)
    if bad == "cycle":
        raw["sources"]["无名剑法·外功攻击提升"]["static"]["stats"] = {
            "dim_shi": {"formula": {"source": "max_outer"}}}
    elif bad == "condition":
        raw["sources"]["无名剑法·剑气会意强化"]["declarations"][0]["condition"] = {
            "field": "target.typo", "op": "eq", "value": 1}
    elif bad == "shared":
        raw["sources"]["无名枪法·共享强化"]["declarations"][1]["value"] = .6
    elif bad == "empty":
        raw["sources"]["蹊跷·清河"]["status"] = "complete"
    elif bad == "tier":
        raw["growth"]["inner_ways"]["无名心法"] = 7
    else:
        raw["sources"]["个人等级·23级累计"]["static"]["stats"]["unknown"] = 1
    with pytest.raises(AttrModelError):
        manager.save(profile.school, raw)
    assert manager.load(profile.school).raw == profile.raw


def test_user_edit_uses_local_override_and_keeps_bundled_data(manager, profile):
    raw = deepcopy(profile.raw)
    raw["sources"]["基础天赋·已知五维估计"]["static"]["stats"]["dim_jin"] = 42
    manager.save(profile.school, raw)
    assert manager.load(profile.school).raw == raw
    # 用户层编辑不会写回版本内置文件。
    path = manager.resolver.system_dir / REL_DIR / "鸣金·虹.yaml"
    assert yaml.safe_load(path.read_text())["sources"]["基础天赋·已知五维估计"]["static"]["stats"]["dim_jin"] == 41


def test_observed_thresholds_match_supplied_game_descriptions(profile):
    source = next(s for s in profile.sources if s.source_id == "无名剑法·外功攻击提升")
    formula = source.effect.stats["max_outer"]
    assert isinstance(formula, Formula)
    assert formula.apply(profile.observations) == 122
    assert formula.apply({"dim_shi": 231}) == 61
    formula = next(s for s in profile.sources if s.source_id == "无名枪法·会意率提升").effect.stats["intent_rate"]
    assert formula.apply({"dim_shi": 231}) == pytest.approx(.035)


@pytest.mark.parametrize("tier", [2, 6])
@pytest.mark.parametrize("equipment", [
    {},
    {"dim_shi": 100, "max_mingjin": 100, "intent_rate": .1},
    {"dim_shi": 363, "max_mingjin": 600, "intent_rate": .5},
])
def test_attribute_catalog_matches_character_instance(profile, tmp_path, tier, equipment):
    """属性页实际读取的武学/心法配置与实例同值，覆盖公式未封顶及封顶。"""
    from lvjiang.apps.yysls.core.attr_model.manager import AttrModelManager
    from lvjiang.apps.yysls.core.attr_model.models import AttrLoadout, InnerWaySlot
    from lvjiang.apps.yysls.core.attr_model.resolver import resolve

    root = Path(__file__).resolve().parents[2] / "config/system/yysls/attr_model"
    sources_dir = tmp_path / "catalog"
    sources_dir.mkdir()
    for filename in ("martial_art.yaml", "inner_way.yaml"):
        (sources_dir / filename).write_bytes((root / filename).read_bytes())
    catalog = AttrModelManager(
        sources_dir, martial_art_roster=lambda: profile.growth["martial_arts"])
    assert not catalog.errors()
    loadout = AttrLoadout(
        level=110, school=profile.school,
        inner_ways=tuple(InnerWaySlot(name, tier) for name in profile.growth["inner_ways"]),
    )
    effects = catalog.effects_for_loadout(
        loadout, martial_arts=tuple(profile.growth["martial_arts"]))
    # 等级、天赋等沿用实例，只替换本次应回写的两个类别。
    effects.extend(source.effect for source in profile.sources
                   if source.kind not in ("martial_art", "inner_way"))
    result = resolve(effects, level=110, school_attr="鸣金",
                     caps_lookup=lambda *_: None, residual=equipment)
    growth = deepcopy(profile.growth)
    growth["inner_ways"] = dict.fromkeys(growth["inner_ways"], tier)
    expected = evaluate_profile(profile, growth=growth, equipment=equipment)
    assert result.panel.values == pytest.approx(expected.resolved.panel.values)
    assert result.combat.values == pytest.approx(expected.resolved.combat.values)
    # 未提供的重数仍待填；无名心法纯机制重数已确认无静态属性。
    assert catalog.progress("martial_art") == (2, 2)
    inner_effects = catalog.effects(("inner_way",))
    assert all(not e.pending for e in inner_effects if e.group == "无名心法")
    assert all(e.pending for e in inner_effects
               if e.group in ("千山法", "威猛歌", "凝神章") and e.tier in (1, 3, 4, 6))


def test_character_panel_displays_records_and_responds_to_growth(qapp, manager):
    from lvjiang.apps.yysls.ui.game_settings.character_profile_panel import (
        CharacterProfilePanel,
    )
    widget = CharacterProfilePanel(manager=manager)
    assert widget._sources.rowCount() == 36
    assert "未求值" in widget._notice.text()
    widget._growth_inputs["inner_ways", "无名心法"].setValue(2)
    rows = {widget._attrs.item(i, 0).text(): widget._attrs.item(i, 1).text()
            for i in range(widget._attrs.rowCount())}
    assert rows["直接会意率"] == "0%"
    widget._sources.selectRow(0)
    assert "23" in widget._detail.toPlainText()
    widget.deleteLater()


def test_shared_static_contributions_are_not_added_twice(profile):
    raw = deepcopy(profile.raw)
    source = raw["sources"]["个人等级·23级累计"]
    source["shared_key"] = "同一份等级总量"
    raw["sources"]["同源副本"] = deepcopy(source)
    result = evaluate_profile(parse_profile(raw))
    assert result.resolved.panel.values["health_max"] == 26200
    assert result.resolved.panel.values["dim_shi"] == 283


def test_unknown_map_progress_is_not_a_completed_zero(profile):
    result = evaluate_profile(profile)
    assert "蹊跷·清河" in result.missing
    assert "蹊跷·清河" not in result.excluded
    source = next(s for s in profile.sources if s.source_id == "蹊跷·清河")
    assert source.status == "pending" and not source.effect.modeled


@pytest.mark.parametrize("invalid", [float("nan"), float("inf")])
def test_nonfinite_formula_parameters_are_rejected(profile, invalid):
    raw = deepcopy(profile.raw)
    raw["sources"]["无名剑法·外功攻击提升"]["static"]["stats"]["max_outer"]["formula"]["multiplier"] = invalid
    with pytest.raises(AttrModelError, match="有限数值"):
        parse_profile(raw)


def test_recent_event_window_is_structured_and_validated(profile):
    raw = deepcopy(profile.raw)
    condition = raw["sources"]["无名心法·一重基础增益"]["declarations"][0]["condition"]
    assert condition["any"][1] == {"recent_event": {"name": "退亦有方剑气释放", "seconds": 5}}
    condition["any"][1]["recent_event"]["seconds"] = -1
    with pytest.raises(AttrModelError, match="时间窗口"):
        parse_profile(raw)


def test_common_base_matches_profile_dimensions_and_converts_once(profile):
    from lvjiang.apps.yysls.core.attr_model.manager import AttrModelManager
    from lvjiang.apps.yysls.core.attr_model.models import AttrLoadout

    root = Path(__file__).resolve().parents[2]
    manager = AttrModelManager(root / "config/system/yysls/attr_model")
    result = manager.resolve_loadout(
        AttrLoadout(level=110, school="鸣金·虹"), school_attr="鸣金")
    profile_result = evaluate_profile(profile)
    for name in ("dim_jin", "dim_shi", "dim_min", "dim_ti", "dim_yu"):
        assert result.panel.values[name] == profile_result.resolved.panel.values[name] == 283
    assert result.panel.values["min_outer"] == pytest.approx(318.375)
    assert result.panel.values["max_outer"] == pytest.approx(639.58)
    assert result.panel.values["intent_rate"] == pytest.approx(.10754)
    assert result.panel.values["crit_rate"] == pytest.approx(.21508)
    assert result.combat_attrs.min_outer == pytest.approx(318.375)
