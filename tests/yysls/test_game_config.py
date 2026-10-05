"""GameConfigManager（属性规则管理器）测试

此前仅被判定器/解析器间接覆盖，本文件针对
词条别名归一、上限查询（含承音值）与品阶推断补充直测。
数值断言与 config/system/yysls/attributes.yaml 保持一致。
"""

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from lvjiang.apps.yysls.config import (
    POOL_DINGYIN,
    AttrRange,
    GameConfigManager,
    LevelRule,
    get_game_config,
    validate_season_configs,
)
from tests.case_matrix import case_matrix


@pytest.fixture(scope="module")
def mgr():
    return get_game_config()


def test_gongjue_bonus_uses_actual_effective_level(mgr):
    assert [mgr.get_gongjue_bonus("会意", level)
            for level in (110, 111, 113, 114, 115)] == [
                3.5, 3.75, 3.75, 3.75, 4.0]


# ─── 词条别名归一 ──────────────────────────────────────────

class TestResolveAffixCategory:
    def test_gongjue_level_falls_back_to_equip_level(self, tmp_path):
        """弓玦等级查找：配了就用配置值，没配或没有该装备等级则等于装备等级。

        原来这里断言的是随包 seasons.yaml 里南吕相和的一堆字面日期与等级，
        赛季配置一更新就失败，却抓不到任何真实回归——查找逻辑本身反而没被测到。
        """
        path = tmp_path / "game_config.yaml"
        path.write_text(
            "season_configs:\n"
            "- season_number: 1\n"
            "  start_date: '2026-01-01'\n"
            "  end_date: '2026-04-01'\n"
            "  equip_level: 110\n"
            "  gongjue_level: 105\n"
            "- season_number: 2\n"
            "  start_date: '2026-04-01'\n"
            "  end_date: '2026-07-01'\n"
            "  equip_level: 115\n",
            encoding="utf-8",
        )
        manager = GameConfigManager(path)

        assert manager.gongjue_level_for(110) == 105
        # 该赛季没写 gongjue_level → 回落到装备等级
        assert manager.gongjue_level_for(115) == 115
        # 没有任何赛季用这个装备等级 → 同样回落
        assert manager.gongjue_level_for(120) == 120

    def test_adjacent_seasons_must_share_boundary_date(self):
        base = [
            {
                "season_number": 1,
                "start_date": "2026-01-01",
                "end_date": "2026-03-01",
            },
            {
                "season_number": 2,
                "start_date": "2026-03-01",
                "end_date": "2026-06-01",
            },
        ]
        validate_season_configs(base)

        for invalid_start in ("2026-02-28", "2026-03-02"):
            invalid = [dict(item) for item in base]
            invalid[1]["start_date"] = invalid_start
            with pytest.raises(ValueError, match="相邻赛季日期必须衔接"):
                validate_season_configs(invalid)

    def test_current_equip_level_ignores_future_season(self, tmp_path):
        path = tmp_path / "game_config.yaml"
        today = date.today()
        path.write_text(
            "level_configs:\n"
            "- level: 105\n"
            "season_configs:\n"
            "- season_number: 1\n"
            f"  start_date: '{today - timedelta(days=30)}'\n"
            f"  end_date: '{today + timedelta(days=30)}'\n"
            "  equip_level: 110\n"
            "- season_number: 99\n"
            f"  start_date: '{today + timedelta(days=31)}'\n"
            f"  end_date: '{today + timedelta(days=90)}'\n"
            "  equip_level: 999\n",
            encoding="utf-8",
        )

        assert GameConfigManager(path).current_equip_level() == 110

    def test_current_equip_level_does_not_fall_back_to_future_config(
            self, tmp_path):
        path = tmp_path / "game_config.yaml"
        tomorrow = date.today() + timedelta(days=1)
        path.write_text(
            "level_configs:\n"
            "- level: 999\n"
            "season_configs:\n"
            "- season_number: 99\n"
            f"  start_date: '{tomorrow}'\n"
            f"  end_date: '{tomorrow + timedelta(days=90)}'\n"
            "  equip_level: 999\n",
            encoding="utf-8",
        )

        assert GameConfigManager(path).current_season() is None
        assert GameConfigManager(path).current_equip_level() == 0

    def test_season_switches_at_five_on_overlapping_boundary(self, tmp_path):
        path = tmp_path / "game_config.yaml"
        path.write_text(
            "season_configs:\n"
            "- season_number: 1\n"
            "  start_date: '2026-01-01'\n"
            "  end_date: '2026-03-01'\n"
            "  equip_level: 110\n"
            "- season_number: 2\n"
            "  start_date: '2026-03-01'\n"
            "  end_date: '2026-06-01'\n"
            "  equip_level: 115\n",
            encoding="utf-8",
        )
        manager = GameConfigManager(path)

        before = manager.season_at(datetime(2026, 3, 1, 4, 59, 59))
        after = manager.season_at(datetime(2026, 3, 1, 5, 0, 0))

        assert before is not None and before.season_number == 1
        assert after is not None and after.season_number == 2


    def test_equipment_cooldown_days_reads_basic_config(self, tmp_path):
        path = tmp_path / "game_config.yaml"
        path.write_text(
            "basic_config:\n"
            "  equipment_cooldown_days: 7\n"
            "level_configs: []\n",
            encoding="utf-8",
        )
        assert GameConfigManager(path).get_equipment_cooldown_days() == 7


    def test_equipment_cooldown_carryover_can_be_disabled(self, tmp_path):
        path = tmp_path / "game_config.yaml"
        path.write_text(
            "basic_config:\n"
            "  equipment_cooldown_carryover: false\n"
            "level_configs: []\n",
            encoding="utf-8",
        )
        assert not GameConfigManager(
            path).is_equipment_cooldown_carryover_enabled()

    def test_invalid_cooldown_carryover_falls_back_to_enabled(self, tmp_path):
        path = tmp_path / "game_config.yaml"
        path.write_text(
            "basic_config:\n"
            "  equipment_cooldown_carryover: 'false'\n"
            "level_configs: []\n",
            encoding="utf-8",
        )
        assert GameConfigManager(
            path).is_equipment_cooldown_carryover_enabled()

    @pytest.mark.parametrize("value", [0, 366, True, "5"])
    def test_invalid_equipment_cooldown_days_falls_back_to_five(
            self, tmp_path, value):
        import yaml

        path = tmp_path / "game_config.yaml"
        path.write_text(yaml.safe_dump({
            "basic_config": {"equipment_cooldown_days": value},
            "level_configs": [],
        }), encoding="utf-8")
        assert GameConfigManager(path).get_equipment_cooldown_days() == 5


    def test_level_115_material_rules_match_110(self):
        path = (Path(__file__).resolve().parents[2]
                / "config/system/yysls/game_config/seasons.yaml")
        levels = {item.level: item for item in GameConfigManager(path).get_level_configs()}
        assert levels[115].min_material_count == levels[110].min_material_count
        assert levels[115].allow_reset == levels[110].allow_reset
        assert levels[115].reset_no_refund == levels[110].reset_no_refund
        assert levels[115].tuning_stones == levels[110].tuning_stones
        for quality in ("gold", "purple"):
            rule = levels[115].tuning_stones[quality]
            for values in (rule.tune_cost, rule.reset_refund, rule.recycle_refund):
                assert set(values) == {1, 2, 3, 4, 5}
                assert values[5] > 0

    def test_level_chengyin_capabilities(self, mgr):
        levels = {item.level: item for item in mgr.get_level_configs()}
        assert levels[91].allow_chengyin
        assert not levels[100].allow_retransfer
        assert levels[105].allow_chengyin
        assert levels[105].allow_retransfer
        assert not levels[105].allow_retransfer_after_chengyin
        assert levels[110].allow_retransfer_after_chengyin

    def test_legacy_level_config_gets_capability_defaults(self, tmp_path):
        path = tmp_path / "game_config.yaml"
        path.write_text(
            "level_configs:\n"
            "- level: 90\n"
            "- level: 91\n"
            "- level: 105\n"
            "- level: 110\n"
            "  allow_retransfer: false\n",
            encoding="utf-8",
        )
        levels = {
            item.level: item
            for item in GameConfigManager(path).get_level_configs()
        }
        assert not levels[90].allow_chengyin
        assert levels[91].allow_chengyin
        assert not levels[91].allow_retransfer
        assert levels[105].allow_retransfer
        assert not levels[110].allow_retransfer
        assert not levels[110].allow_retransfer_after_chengyin

    @case_matrix("name,expected", [
        ("流星云珑", 110),
        ("吴钩霜甲", 110),
        ("踏雪含光", 105),
        ("雁南飞冠", 105),
        ("承音 | 110阶", 0),
        ("未知装备", 0),
        (None, 0),
    ])
    def test_original_level_is_inferred_only_from_tier_name(
            self, mgr, name, expected):
        assert mgr.infer_original_equipment_level(name) == expected


# ─── 词条分组（_aliases dict 形态）──────────────────────


# ─── 词条上限查询 ──────────────────────────────────────────

class TestGetAffixCaps:
    def test_percent_affix_110(self, mgr):
        caps = mgr.get_affix_caps(110, "会心率")
        assert caps["cap"] == 14
        assert caps["unit"] == "%"
        # 承音上限读配置原值（系统配置初值按 94% 一位小数生成，之后按游戏校正）
        assert caps["chengyin"] == 13.2


# ─── 词条部位（顶层 affix_parts）──────────────────────────


class TestExternalAffixAliases:
    def test_exact_alias_lookup(self, mgr):
        assert mgr.get_affix_names_for_alias("拳甲增") == ["手甲武学增伤"]
        assert mgr.get_affix_names_for_alias("首领增") == ["对首领单位增伤"]

    def test_no_fuzzy_fallback(self, mgr):
        assert mgr.get_affix_names_for_alias("蓄力技") == []
        assert mgr.get_affix_names_for_alias("不存在的增") == []


# ─── 词库类型（普通 / 定音）──────────────────────────────

class TestAffixPool:
    @case_matrix("name", ["外功增益", "属攻增益", "指定技能增效"])
    def test_dingyin_categories(self, mgr, name):
        assert mgr.get_affix_pool(name) == POOL_DINGYIN
        assert mgr.is_dingyin_affix(name)


    def test_dingyin_chengyin_equals_cap(self, mgr):
        # 承音装备定音属性无限制，承音值 = cap（不乘 0.94）
        caps = mgr.get_affix_caps(110, "外功增益")
        assert caps["cap"] == 16.8
        assert caps["chengyin"] == 16.8


    def test_chengyin_is_config_value_not_ratio(self, tmp_path):
        """游戏的承音取舍可能比 94% 高/低 0.1，配置写多少就用多少；
        缺失字段只在迁移期按 94% 派生。"""
        import yaml

        from lvjiang.apps.yysls.config import derive_chengyin_cap

        path = tmp_path / "game_config.yaml"
        path.write_text(yaml.safe_dump({
            "affix_caps": {
                "外功攻击": {
                    "_aliases": ["最大外功攻击"],
                    110: {"cap": 121.4, "chengyin": 114.2},   # 比 94% 高 0.1
                    105: {"cap": 105.6},                       # 缺失 → 派生
                },
                "外功增益": {
                    "_pool": "dingyin",
                    110: {"cap": 16.8, "chengyin": 1.0},       # 定音忽略该字段
                },
            },
        }, allow_unicode=True), encoding="utf-8")
        mgr = GameConfigManager(path)
        assert mgr.get_affix_caps(110, "最大外功攻击")["chengyin"] == 114.2
        assert mgr.get_affix_caps(105, "最大外功攻击")["chengyin"] == derive_chengyin_cap(105.6) == 99.3
        assert mgr.get_affix_caps(110, "外功增益")["chengyin"] == 16.8


# ─── 品阶推断 ──────────────────────────────────────────────

class TestInferQuality:
    @case_matrix("value,expected", [
        ([100, 232], "gold"),     # 110 阶武器 gold 区间精确匹配
        ([90, 209], "purple"),    # purple 区间精确匹配
        ([80, 186], "blue"),      # blue 区间精确匹配
        ([100, 233], None),       # 上端不相等 → 不命中
        ([99, 232], None),        # 下端不相等 → 不命中
        (232, None),              # 标量不能命中区间属性
    ])
    def test_weapon_range_exact_match(self, mgr, value, expected):
        # 区间 [a,b] 含义：装备提供 +a 最小、+b 最大外功攻击，
        # 解析出的区间必须两端都相等才算同一品阶，而非“落在区间内”
        assert mgr.infer_quality("剑", 110, value) == expected


    def test_unknown_type_greedy_fallback(self, mgr):
        # 类型未知时贪婪遍历所有部位（防具气血值不重叠可唯一确定）
        assert mgr.infer_quality(None, 110, 19445) == "gold"   # chest
        assert mgr.infer_quality(None, 110, 7778) == "blue"    # head


# ─── 仅凭数值反查等级+品阶（equip_level OCR 缺失兵底）────


# ─── 仅凭数值反查部位（equip_type OCR 缺失回填）─────────

class TestInferTypeByValue:


    def test_ambiguous_head_leg_wrist(self, mgr):
        # 冠胄/胫甲/腕甲 _follow 同值，命中 3 个部位 → 无法区分
        assert mgr.infer_type_by_value(7778) is None


# ─── 武器类型 / 流派配置（顶层 weapon_types / schools）────

class TestWeaponTypesAndSchools:


    def test_equipment_name_mapping_is_config_driven(self, mgr):
        assert mgr.infer_equipment_type_from_name("踏雪含光") == "剑"
        assert mgr.infer_equipment_type_from_name("雁南飞甲") == "胸甲"
        assert mgr.infer_equipment_type_from_name("吴钩缚袴") == "胫甲"
        assert mgr.infer_equipment_type_from_label("武器·鼓") == "舞绫鼓"
        assert mgr.infer_equipment_type_from_label("腕甲") == "腕甲"

    def test_name_series_is_evidence_not_level_inference(self, mgr):
        output = mgr.get_equipment_name_series("output")
        armor = mgr.get_equipment_name_series("armor")
        # 这里只验证已知映射，不限制配置总量；后续新增等级不应破坏用例。
        assert output.items() >= {110: "流星", 105: "踏雪"}.items()
        assert armor.items() >= {110: "吴钩", 105: "雁南飞"}.items()
        # 等阶名称只有显式 getter，解析器的等级仍来自“X 阶”/基础属性。
        assert not hasattr(mgr, "infer_level_from_equipment_name")


# ─── 指定武学增效词条数据源 ──────────────────────────


# ─── 数据结构单元 ──────────────────────────────────────────

class TestAffixCategories:

    def test_returns_copy_not_reference(self, mgr):
        # 返回副本，外部修改不影响内部状态
        first = mgr.get_affix_categories()
        first["外功类"].append("污染项")
        assert "污染项" not in mgr.get_affix_categories()["外功类"]

    @case_matrix("affix,category", [
        ("最大外功攻击", "外功类"),
        ("劲", "外功类"),
        ("势", "外功类"),
        ("最大无相攻击", "属攻类"),
        ("最大牵丝攻击", "属攻类"),
        ("会意率", "三率类"),
        ("全武学增效", "增效类"),
        ("单体类奇术增伤", "增效类"),
        ("剑武学增伤", "武器类"),
        ("扇武学增效", "武器类"),
        ("气血最大值", "生存类"),
    ])
    def test_affix_to_category_mapping(self, mgr, affix, category):
        assert mgr.get_affix_category(affix) == category


class TestLevelRule:


    def test_first_matching_range_wins(self):
        # 精确匹配下相邻品阶区间不再互相遮蔽
        rule = LevelRule(ranges=[
            AttrRange("gold", 100, 232),
            AttrRange("purple", 90, 209),
        ])
        assert rule.infer_quality([100, 232]) == "gold"
        assert rule.infer_quality([90, 209]) == "purple"
        assert rule.infer_quality([100, 209]) is None   # 端点不成对
        assert rule.infer_quality(150) is None           # 标量不匹配区间
