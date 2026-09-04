"""基础属性来源建模的求值、反解与解析校验测试。"""

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.attr_model import (
    DIMENSION_JIN,
    DIMENSION_MIN,
    DIMENSION_SHI,
    SCOPE_COMBAT,
    SOURCE_KINDS,
    AttrModelError,
    Formula,
    FullAffix,
    StatEffect,
    diff_against_panel,
    dimension_effects,
    get_attr_model_manager,
    parse_source_file,
    resolve,
    solve_residual,
    split_affix_cap,
)
from lvjiang.apps.yysls.core.combat.combat_attrs import (
    JIN_TO_MIN_OUTER,
    MIN_TO_MIN_OUTER,
    CombatAttributes,
    convert_five_dims,
)

#: 测试用满值表：只覆盖用到的两个词条，避免依赖真实 game_config
_CAPS = {
    (110, "外功攻击"): 121.4,
    (110, "属性攻击"): 68.8,
    (96, "外功攻击"): 77.8,
}


def _caps(level: int, category: str) -> float | None:
    return _CAPS.get((level, category))


def _effect(source_id: str, **kwargs) -> StatEffect:
    kwargs.setdefault("label", source_id)
    kwargs.setdefault("kind", "inner_way")
    return StatEffect(source_id=source_id, **kwargs)


def _resolve(effects, *, level=110, school_attr="牵丝", residual=None):
    return resolve(
        effects,
        level=level,
        school_attr=school_attr,
        caps_lookup=_caps,
        residual=residual,
    )


# ── 整条词条的拆分 ────────────────────────────────────────

@pytest.mark.parametrize("level,expected", [(110, (40.5, 80.9)), (96, (25.9, 51.9))])
def test_full_affix_splits_into_the_level_cap(level: int, expected: tuple) -> None:
    """一整条词条拆成最小/最大，两者之和回到该等级满值。

    这是 full_affix 能替代手填数值的前提：只要拆分不变，换赛季改
    affix_caps 一处即可，几十个心法条目不用重填。
    """
    result = _resolve(
        [_effect("易水歌·二重", full_affix=FullAffix("外功攻击"))], level=level
    )

    low, high = expected
    assert result.combat_attrs.min_outer == pytest.approx(low)
    assert result.combat_attrs.max_outer == pytest.approx(high)
    assert low + high == pytest.approx(_CAPS[(level, "外功攻击")], abs=0.05)


def test_attribute_attack_full_affix_follows_the_school() -> None:
    """属性攻击是词组，同一条目在不同流派落到不同字段。"""
    effects = [_effect("某心法·二重", full_affix=FullAffix("属性攻击"))]

    qiansi = _resolve(effects, school_attr="牵丝").combat_attrs
    lieshi = _resolve(effects, school_attr="裂石").combat_attrs

    assert qiansi.min_qiansi > 0 and qiansi.min_lieshi == 0
    assert lieshi.min_lieshi > 0 and lieshi.min_qiansi == 0
    assert qiansi.min_qiansi == pytest.approx(lieshi.min_lieshi)


def test_missing_cap_for_the_level_is_rejected() -> None:
    with pytest.raises(AttrModelError):
        _resolve([_effect("x", full_affix=FullAffix("外功攻击"))], level=105)


def test_split_ratio_is_configurable() -> None:
    assert split_affix_cap(121.4, (1, 2)) == (40.5, 80.9)
    assert split_affix_cap(100.0, (1, 1)) == (50.0, 50.0)


# ── 两趟求值 ──────────────────────────────────────────────

def test_formula_reads_the_final_source_regardless_of_declaration_order() -> None:
    """公式在第二趟求值，所以书写顺序不影响结果。

    武学天赋「敏 → 外功攻击」如果先于五维来源求值，就会只读到一半
    的敏；两趟求值消除这种顺序依赖。
    """
    talent = _effect(
        "武学·外功增幅",
        kind="martial_art",
        stats={"min_outer": Formula(source="dim_min", multiplier=2.0)},
    )
    dimension = _effect("五维·敏", kind="dimension", stats={"dim_min": 100.0})

    talent_first = _resolve([talent, dimension]).combat_attrs
    dimension_first = _resolve([dimension, talent]).combat_attrs

    assert talent_first.min_outer == pytest.approx(200.0)
    assert talent_first.min_outer == pytest.approx(dimension_first.min_outer)


def test_formula_clamp_applies() -> None:
    effects = [
        _effect("五维·敏", kind="dimension", stats={"dim_min": 1000.0}),
        _effect(
            "武学",
            kind="martial_art",
            stats={"min_outer": Formula(source="dim_min", multiplier=1.0, maximum=73.9)},
        ),
    ]

    assert _resolve(effects).combat_attrs.min_outer == pytest.approx(73.9)


def test_dimension_fields_do_not_leak_into_combat_attributes() -> None:
    """五维只是求值中间量，投影回 CombatAttributes 时丢弃。"""
    result = _resolve([_effect("五维·敏", kind="dimension", stats={"dim_min": 100.0})])

    assert not hasattr(result.combat_attrs, "dim_min")


# ── 双出口 ────────────────────────────────────────────────

def test_combat_only_sources_stay_out_of_the_panel() -> None:
    """吃食只在战斗内生效，不该出现在对账用的面板属性里。"""
    effects = [
        _effect("套装", kind="gear_set", stats={"min_outer": 100.0}),
        _effect("吃食", kind="food", scope=SCOPE_COMBAT, stats={"min_outer": 120.0}),
    ]

    result = _resolve(effects)

    assert result.panel_attrs.min_outer == pytest.approx(100.0)
    assert result.combat_attrs.min_outer == pytest.approx(220.0)


# ── 未建模条目 ────────────────────────────────────────────

def test_unmodeled_sources_contribute_nothing_but_are_reported() -> None:
    """填一半也能跑：未填条目不参与求值，但要能被看见。"""
    effects = [
        _effect("已填", stats={"min_outer": 50.0}),
        _effect("待测量", modeled=False, stats={"min_outer": 999.0}),
    ]

    result = _resolve(effects)

    assert result.combat_attrs.min_outer == pytest.approx(50.0)
    assert [item.source_id for item in result.unmodeled] == ["待测量"]


# ── breakdown ─────────────────────────────────────────────

def test_breakdown_attributes_each_contribution_to_its_source() -> None:
    """面板对不上时靠这份明细定位到具体来源，而不是只知道总数不对。"""
    effects = [
        _effect("心法A", kind="inner_way", stats={"min_outer": 40.5}),
        _effect("套装B", kind="gear_set", stats={"min_outer": 78.0}),
    ]

    result = _resolve(effects)

    assert result.combat.contribution_by_kind("min_outer") == {
        "inner_way": pytest.approx(40.5),
        "gear_set": pytest.approx(78.0),
    }
    assert [m.source_id for m in result.combat.modifiers_for("min_outer")] == ["心法A", "套装B"]


def test_diff_against_panel_lists_only_mismatched_fields() -> None:
    result = _resolve([_effect("心法A", stats={"min_outer": 40.5, "crit_rate": 0.1})])
    panel = CombatAttributes(min_outer=40.5, crit_rate=0.2)

    assert diff_against_panel(result, panel) == {
        "crit_rate": (pytest.approx(0.1), pytest.approx(0.2))
    }


# ── 反解 ──────────────────────────────────────────────────

def test_residual_makes_the_panel_match_even_with_sources_missing() -> None:
    """已建模来源走推导，缺口由反解补齐，总量仍等于实测面板。"""
    effects = [_effect("心法A", stats={"min_outer": 40.5})]

    residual = solve_residual(
        effects, {"min_outer": 2155.4}, level=110, school_attr="牵丝", caps_lookup=_caps
    )
    result = _resolve(effects, residual=residual)

    assert result.panel_attrs.min_outer == pytest.approx(2155.4)
    assert residual["min_outer"] == pytest.approx(2155.4 - 40.5)


def test_residual_converges_when_a_formula_couples_two_targets() -> None:
    """反解目标之间经公式互相影响时，逐轮修正仍要收敛。

    补 min_outer 会经公式改变 max_outer，一次相减解不出来。
    """
    effects = [
        _effect(
            "武学",
            kind="martial_art",
            stats={"max_outer": Formula(source="min_outer", multiplier=0.5)},
        ),
    ]

    residual = solve_residual(
        effects,
        {"min_outer": 2000.0, "max_outer": 5000.0},
        level=110,
        school_attr="牵丝",
        caps_lookup=_caps,
    )
    result = _resolve(effects, residual=residual)

    assert result.panel_attrs.min_outer == pytest.approx(2000.0)
    assert result.panel_attrs.max_outer == pytest.approx(5000.0)


def test_residual_rejects_unknown_target_field() -> None:
    with pytest.raises(AttrModelError):
        solve_residual(
            [], {"不存在的字段": 1.0}, level=110, school_attr="牵丝", caps_lookup=_caps
        )


# ── YAML 解析校验 ─────────────────────────────────────────

def _parse(payload: dict):
    return parse_source_file(payload, filename="t.yaml")


def test_parse_reads_full_affix_and_constants() -> None:
    effects = _parse({
        "kind": "inner_way",
        "entries": {
            "易水歌·二重": {"full_affix": "外功攻击"},
            "易水歌·五重": {"stats": {"direct_crit": 0.046}},
        },
    })

    assert [e.source_id for e in effects] == ["易水歌·二重", "易水歌·五重"]
    assert effects[0].full_affix == FullAffix("外功攻击", (1, 2))
    assert effects[1].stats == {"direct_crit": 0.046}


def test_parse_reads_formula() -> None:
    effects = _parse({
        "kind": "martial_art",
        "entries": {
            "武学": {
                "stats": {
                    "min_outer": {
                        "formula": {"source": "dim_min", "multiplier": 0.26, "max": 73.9}
                    }
                }
            }
        },
    })

    formula = effects[0].stats["min_outer"]
    assert isinstance(formula, Formula)
    assert (formula.source, formula.multiplier, formula.maximum) == ("dim_min", 0.26, 73.9)


def test_empty_entry_counts_as_unmodeled() -> None:
    """空壳条目不该被当成已完成，否则建模进度会虚高。"""
    effects = _parse({"kind": "inner_way", "entries": {"待测量": {}}})

    assert effects[0].modeled is False


@pytest.mark.parametrize("payload", [
    {"kind": "不存在的类别", "entries": {}},
    # 「能保存 = 能求值」：以下全部必须在解析期失败，而不是拖到求值期
    {"kind": "inner_way", "顶层拼错": 1, "entries": {}},
    {"kind": "inner_way", "entries": {"x": {"stats": {"根本没这个字段": 1.0}}}},
    {"kind": "inner_way", "entries": {"x": {"full_affix": "气血最大值"}}},
    {"kind": "martial_art", "entries": {"x": {"stats": {"min_outer": {
        "formula": {"source": "dim_min"}, "多余的键": 1}}}}},
    {"kind": "martial_art", "entries": {"x": {"stats": {"min_outer": {
        "formula": {"source": "不存在的源字段"}}}}}},
    {"kind": "inner_way", "entries": {"x": {"打错的字段": 1}}},
    {"kind": "inner_way", "entries": {"x": {"scope": "既不是面板也不是战斗"}}},
    {"kind": "inner_way", "entries": {"x": {"split": [1, 2]}}},
    {"kind": "inner_way", "entries": {"x": {"stats": {"min_outer": "不是数值"}}}},
    {"kind": "inner_way", "entries": {"x": {"stats": {"min_outer": {"没有formula": 1}}}}},
    {"kind": "martial_art",
     "entries": {"x": {"stats": {"min_outer": {"formula": {"multiplier": 2}}}}}},
])
def test_parse_rejects_malformed_config(payload: dict) -> None:
    """从严校验：静默跳过会让几十个来源里的面板差异无从定位。"""
    with pytest.raises(AttrModelError):
        _parse(payload)


def test_unknown_stat_field_is_rejected_at_resolve_time() -> None:
    with pytest.raises(AttrModelError):
        _resolve([_effect("x", stats={"没有这个字段": 1.0})])


def test_formula_referencing_unknown_source_is_rejected() -> None:
    with pytest.raises(AttrModelError):
        _resolve([_effect("x", stats={"min_outer": Formula(source="不存在")})])


# ── 内建的五维转换 ────────────────────────────────────────

def test_builtin_dimension_conversion_matches_convert_five_dims() -> None:
    """内建转换与 combat_attrs.convert_five_dims 必须给出同一结果。

    装备词条上的五维走 convert_five_dims，基础属性里的五维走本模块。
    两条路径共用 combat_attrs 里的同一组系数，这里守住它们不漂移——
    真出现两份系数时，这个断言是唯一会红的地方。
    """
    jin, shi, agility = 137.0, 96.0, 211.0
    effects = [
        _effect(
            "五维来源",
            kind="breakthrough",
            stats={"dim_jin": jin, "dim_shi": shi, "dim_min": agility},
        ),
        *dimension_effects(),
    ]

    result = _resolve(effects).combat_attrs
    expected = convert_five_dims(jin=jin, shi=shi, min_val=agility)

    for name in ("min_outer", "max_outer", "crit_rate", "intent_rate"):
        assert getattr(result, name) == pytest.approx(getattr(expected, name))


def test_dimension_conversion_is_always_applied_regardless_of_selection() -> None:
    """五维转换是结构性的，用户选的是上哪几门心法，不是要不要转换。"""
    manager = get_attr_model_manager()

    result = manager.resolve(level=110, school_attr="牵丝", selected=())

    assert {m.source_id for m in result.combat.modifiers} >= {
        DIMENSION_JIN, DIMENSION_SHI, DIMENSION_MIN
    }


def test_dimension_breakdown_separates_each_dimension() -> None:
    """最小外功攻击同时来自劲和敏，breakdown 要能分开看。"""
    effects = [
        _effect("底子", kind="base", stats={"dim_jin": 100.0, "dim_min": 100.0}),
        *dimension_effects(),
    ]

    result = _resolve(effects)
    sources = {m.source_id: m.delta for m in result.combat.modifiers_for("min_outer")}

    assert sources[DIMENSION_JIN] == pytest.approx(100.0 * JIN_TO_MIN_OUTER)
    assert sources[DIMENSION_MIN] == pytest.approx(100.0 * MIN_TO_MIN_OUTER)


# ── 随仓库分发的配置 ──────────────────────────────────────

def test_shipped_config_loads_without_errors() -> None:
    """config/system/yysls/attr_model/ 下的文件必须全部解析通过。

    解析失败只会记进 errors 并跳过，不抛异常，所以需要显式断言，
    否则整类来源静默缺失。
    """
    manager = get_attr_model_manager()

    assert manager.errors() == {}
    assert {effect.kind for effect in manager.effects()} <= set(SOURCE_KINDS)


def test_shipped_full_affix_entry_tracks_the_real_affix_caps() -> None:
    """已填的 full_affix 条目走真实 affix_caps，不是测试桩。

    这条把配置、affix_caps 与拆分规律绑在一起：任何一处改动而另外
    两处没跟上，都会在这里红灯。
    """
    manager = get_attr_model_manager()
    cap = get_game_config().get_affix_caps(110, "外功攻击")
    assert cap is not None, "affix_caps 缺少 110 级外功攻击"

    result = manager.resolve(
        level=110, school_attr="牵丝", selected=("易水歌·二重",)
    )

    total = result.combat_attrs.min_outer + result.combat_attrs.max_outer
    assert total == pytest.approx(cap["cap"], abs=0.05)


# ── 写回 ──────────────────────────────────────────────────

@pytest.fixture
def sources_dir(tmp_path):
    """独立的来源目录，避免测试写坏仓库里的配置"""
    from lvjiang.apps.yysls.core.attr_model import AttrModelManager

    (tmp_path / "inner_way.yaml").write_text(
        "# 头部注释要在写回后保留\nkind: inner_way\nentries:\n  甲·二重:\n    modeled: false\n",
        encoding="utf-8",
    )
    return AttrModelManager(tmp_path), tmp_path


def test_saving_an_entry_persists_and_reloads(sources_dir) -> None:
    manager, _ = sources_dir

    manager.save_entry("甲·二重", {"full_affix": "外功攻击"})

    assert manager.raw_entry("甲·二重") == {"full_affix": "外功攻击"}
    assert manager.progress("inner_way") == (1, 1)


def test_saving_keeps_the_file_header(sources_dir) -> None:
    """文件头写着 schema 与游戏事实，yaml.dump 会丢注释，必须补回。"""
    manager, path = sources_dir

    manager.save_entry("甲·二重", {"stats": {"crit_rate": 0.01}})

    assert (path / "inner_way.yaml").read_text(encoding="utf-8").startswith(
        "# 头部注释要在写回后保留")


def test_saving_an_invalid_entry_is_rejected_before_it_reaches_disk(
    sources_dir,
) -> None:
    manager, path = sources_dir
    before = (path / "inner_way.yaml").read_text(encoding="utf-8")

    with pytest.raises(AttrModelError):
        manager.save_entry("甲·二重", {"打错的字段": 1})

    assert (path / "inner_way.yaml").read_text(encoding="utf-8") == before


def test_create_and_delete_entry(sources_dir) -> None:
    manager, _ = sources_dir

    manager.create_entry("inner_way", "乙·五重")
    assert manager.progress("inner_way") == (0, 2)

    manager.delete_entry("乙·五重")
    assert manager.progress("inner_way") == (0, 1)


def test_create_rejects_a_duplicate_id(sources_dir) -> None:
    """同名条目在加载时会被静默跳过，所以必须在新增时就挡住。"""
    manager, _ = sources_dir

    with pytest.raises(AttrModelError):
        manager.create_entry("inner_way", "甲·二重")


def test_confirmed_no_effect_counts_as_done(sources_dir) -> None:
    """心法六重里大量是触发类效果；确认「无静态属性」要能推进进度，
    否则永远有一堆查过、确认没有、却仍显示待填的条目。"""
    manager, _ = sources_dir

    manager.save_entry("甲·二重", {"no_effect": True})

    assert manager.progress("inner_way") == (1, 1)
    assert manager.resolve(level=110, school_attr="牵丝").unmodeled == []


def test_no_effect_with_values_is_rejected(sources_dir) -> None:
    manager, _ = sources_dir

    with pytest.raises(AttrModelError):
        manager.save_entry(
            "甲·二重", {"no_effect": True, "stats": {"crit_rate": 0.01}})


# ── 作用域与明细一致性 ────────────────────────────────────

def test_panel_and_combat_keep_their_own_modifiers() -> None:
    """显示哪个作用域的值，就必须用哪个作用域的明细。

    此前两者共用一份明细且优先取战斗侧，界面显示面板值、breakdown
    却含吃食，两栏加不到一起。
    """
    effects = [
        _effect("套装", kind="gear_set", stats={"min_outer": 5.0}),
        _effect("吃食", kind="food", scope=SCOPE_COMBAT, stats={"min_outer": 10.0}),
    ]

    result = _resolve(effects)

    assert result.panel.attrs.min_outer == pytest.approx(5.0)
    assert sum(result.panel.contribution_by_kind("min_outer").values()) == pytest.approx(5.0)
    assert result.combat.attrs.min_outer == pytest.approx(15.0)
    assert sum(result.combat.contribution_by_kind("min_outer").values()) == pytest.approx(15.0)
    assert "food" not in result.panel.contribution_by_kind("min_outer")


def test_extra_attributes_are_recorded_in_the_breakdown() -> None:
    """指定武学增效这类动态属性最容易填错，却曾是唯一没有对账手段的一类。"""
    effects = [
        _effect("心法A", kind="inner_way", extra={"剑武学增伤": 0.08}),
        _effect("套装B", kind="gear_set", extra={"剑武学增伤": 0.02}),
    ]

    result = _resolve(effects)

    assert result.combat.attrs.extra_attrs["剑武学增伤"] == pytest.approx(0.10)
    assert result.combat.contribution_by_kind("剑武学增伤") == {
        "inner_way": pytest.approx(0.08),
        "gear_set": pytest.approx(0.02),
    }
    assert all(m.is_extra for m in result.combat.modifiers_for("剑武学增伤"))


def test_diff_covers_extra_attributes() -> None:
    """对照里没有的动态属性也要能看出差异，否则填错了无从发现。"""
    result = _resolve([_effect("心法A", extra={"剑武学增伤": 0.08})])
    panel = CombatAttributes()

    assert diff_against_panel(result, panel)["剑武学增伤"] == (
        pytest.approx(0.08), pytest.approx(0.0))


def test_touched_fields_lists_stats_and_extras() -> None:
    effects = [_effect("甲", stats={"min_outer": 1.0}, extra={"剑武学增伤": 0.01})]

    assert _resolve(effects).combat.touched_fields() == ["min_outer", "剑武学增伤"]


# ── 装配状态与展开 ────────────────────────────────────────

def _loadout_manager(tmp_path):
    from lvjiang.apps.yysls.core.attr_model import AttrModelManager

    (tmp_path / "inner_way.yaml").write_text(
        "kind: inner_way\nentries:\n" + "".join(
            f"  {name}·{tier_name}:\n"
            f"    group: {name}\n    tier: {tier}\n"
            f"    stats: {{min_outer: {value}}}\n"
            for name, base in (("易水歌", 10.0), ("长生无相", 100.0))
            for tier, (tier_name, value) in enumerate(
                [(t, base + i) for i, t in enumerate(
                    ["一重", "二重", "三重", "四重", "五重", "六重"])], start=1)
        ), encoding="utf-8")
    (tmp_path / "food.yaml").write_text(
        "kind: food\nentries:\n"
        "  八珍玉食:\n    stats: {min_outer: 1000}\n"
        "  另一种药:\n    stats: {min_outer: 2000}\n", encoding="utf-8")
    (tmp_path / "base.yaml").write_text(
        "kind: base\nentries:\n  等级底子:\n    stats: {max_outer: 7.0}\n",
        encoding="utf-8")
    return AttrModelManager(tmp_path)


def test_selecting_a_tier_applies_every_tier_up_to_it(tmp_path) -> None:
    """选第 N 重则一重至 N 重全部生效——这是游戏规则，不是逐条勾选。"""
    from lvjiang.apps.yysls.core.attr_model import AttrLoadout, InnerWaySlot

    manager = _loadout_manager(tmp_path)
    loadout = AttrLoadout(level=110, school="牵丝·玉",
                          inner_ways=(InnerWaySlot("易水歌", 3),))

    result = manager.resolve_loadout(loadout, school_attr="牵丝")

    # 一重 10 + 二重 11 + 三重 12
    assert result.combat.attrs.min_outer == pytest.approx(33.0)
    assert [
        m.source_id for m in result.combat.modifiers_for("min_outer")
        if m.kind == "inner_way"
    ] == ["易水歌·一重", "易水歌·二重", "易水歌·三重"]


def test_unequipped_inner_ways_contribute_nothing(tmp_path) -> None:
    """没装的心法不能算进来。默认全选时 37 门 222 重会一起相加。"""
    from lvjiang.apps.yysls.core.attr_model import AttrLoadout, InnerWaySlot

    manager = _loadout_manager(tmp_path)
    loadout = AttrLoadout(level=110, school="牵丝·玉",
                          inner_ways=(InnerWaySlot("易水歌", 1),))

    result = manager.resolve_loadout(loadout, school_attr="牵丝")

    assert result.combat.attrs.min_outer == pytest.approx(10.0)


def test_single_choice_sources_take_only_the_selected_one(tmp_path) -> None:
    """吃食只能吃一种；两种都算等于凭空多出一份。"""
    from lvjiang.apps.yysls.core.attr_model import AttrLoadout

    manager = _loadout_manager(tmp_path)
    loadout = AttrLoadout(level=110, school="牵丝·玉",
                          selections={"food": "八珍玉食"})

    result = manager.resolve_loadout(loadout, school_attr="牵丝")

    assert result.combat.attrs.min_outer == pytest.approx(1000.0)


def test_always_on_sources_need_no_selection(tmp_path) -> None:
    """等级底子这类恒生效，不该要求用户去勾。"""
    from lvjiang.apps.yysls.core.attr_model import AttrLoadout

    manager = _loadout_manager(tmp_path)
    result = manager.resolve_loadout(
        AttrLoadout(level=110, school="牵丝·玉"), school_attr="牵丝")

    assert result.combat.attrs.max_outer == pytest.approx(7.0)
    assert result.combat.attrs.min_outer == pytest.approx(0.0)


def test_empty_selection_no_longer_means_everything(tmp_path) -> None:
    """selected=None 曾是「全选」。互斥来源全部相加必然是错的，
    零值反而一眼能看出没配。"""
    from lvjiang.apps.yysls.core.attr_model import AttrLoadout

    manager = _loadout_manager(tmp_path)
    implicit = manager.resolve(level=110, school_attr="牵丝")
    explicit = manager.resolve_loadout(
        AttrLoadout(level=110, school="牵丝·玉"), school_attr="牵丝")

    assert implicit.combat.attrs.min_outer == pytest.approx(0.0)
    assert implicit.combat.attrs.max_outer == pytest.approx(
        explicit.combat.attrs.max_outer)


def test_a_slot_rejects_an_out_of_range_tier() -> None:
    from lvjiang.apps.yysls.core.attr_model import AttrModelError, InnerWaySlot

    with pytest.raises(AttrModelError):
        InnerWaySlot("易水歌", 7)


def test_loadout_rejects_more_slots_than_the_game_allows() -> None:
    from lvjiang.apps.yysls.core.attr_model import (
        INNER_WAY_SLOTS,
        AttrLoadout,
        AttrModelError,
        InnerWaySlot,
    )

    too_many = tuple(
        InnerWaySlot(f"心法{i}", 1) for i in range(INNER_WAY_SLOTS + 1))
    with pytest.raises(AttrModelError):
        AttrLoadout(level=110, school="牵丝·玉", inner_ways=too_many)


def test_loadout_rejects_the_same_inner_way_twice() -> None:
    from lvjiang.apps.yysls.core.attr_model import (
        AttrLoadout,
        AttrModelError,
        InnerWaySlot,
    )

    with pytest.raises(AttrModelError):
        AttrLoadout(level=110, school="牵丝·玉", inner_ways=(
            InnerWaySlot("易水歌", 1), InnerWaySlot("易水歌", 2)))


def test_loadout_round_trips_through_a_plain_dict() -> None:
    """存进 session.json 再读回来必须还是同一份装配。"""
    from lvjiang.apps.yysls.core.attr_model import AttrLoadout, InnerWaySlot

    loadout = AttrLoadout(
        level=110, school="牵丝·玉",
        inner_ways=(InnerWaySlot("易水歌", 6), InnerWaySlot("长生无相", 2)),
        selections={"food": "八珍玉食"},
    )

    assert AttrLoadout.from_dict(loadout.to_dict()) == loadout


# ── 公式依赖 ──────────────────────────────────────────────

def test_formula_chains_are_rejected() -> None:
    """公式引用公式的结果时，结果会随 YAML 里的先后而变。

    与其做拓扑排序，不如直接禁掉——报错会指出是哪两个条目在链，
    真需要时再有意识地引入第三趟，而不是在静默的错误结果上填数据。
    """
    effects = [
        _effect("甲", kind="martial_art",
                stats={"min_outer": Formula(source="dim_min", multiplier=2.0)}),
        _effect("乙", kind="martial_art",
                stats={"outer_bonus": Formula(source="min_outer", multiplier=0.001)}),
    ]

    with pytest.raises(AttrModelError, match="公式不能引用公式的结果"):
        _resolve(effects)


def test_formula_reading_a_constant_target_is_fine() -> None:
    """常数写入的字段可以被公式引用——五维正是这条路。"""
    effects = [
        _effect("五维来源", kind="base", stats={"dim_min": 100.0}),
        _effect("武学", kind="martial_art",
                stats={"min_outer": Formula(source="dim_min", multiplier=0.9)}),
    ]

    assert _resolve(effects).combat.attrs.min_outer == pytest.approx(90.0)


def test_builtin_dimension_conversion_passes_the_dependency_rule() -> None:
    """内建的五维转换本身就是三条公式，不能被自己的规则挡住。"""
    from lvjiang.apps.yysls.core.attr_model import validate_formula_dependencies

    validate_formula_dependencies(list(dimension_effects()))
