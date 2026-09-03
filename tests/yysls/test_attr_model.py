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

    assert result.contribution_by_kind("min_outer") == {
        "inner_way": pytest.approx(40.5),
        "gear_set": pytest.approx(78.0),
    }
    assert [m.source_id for m in result.modifiers_for("min_outer")] == ["心法A", "套装B"]


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

    assert {m.source_id for m in result.modifiers} >= {
        DIMENSION_JIN, DIMENSION_SHI, DIMENSION_MIN
    }


def test_dimension_breakdown_separates_each_dimension() -> None:
    """最小外功攻击同时来自劲和敏，breakdown 要能分开看。"""
    effects = [
        _effect("底子", kind="base", stats={"dim_jin": 100.0, "dim_min": 100.0}),
        *dimension_effects(),
    ]

    result = _resolve(effects)
    sources = {m.source_id: m.delta for m in result.modifiers_for("min_outer")}

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
    """心法六重里大量是触发类效果；确认无贡献要能推进进度，
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
