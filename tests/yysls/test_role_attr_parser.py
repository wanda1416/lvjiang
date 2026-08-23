"""RoleAttrParser（角色基础属性 OCR 数据转换器）测试

用真实滚动识别样例（7 屏 detail_1 + 3 段 detail_2）覆盖：
- merge_scroll_snapshots 的跨屏去重拼接（含边界跳跃噪声 token、原地重复幂等）
- parse_detail1 的已知字段提取（区间、百分号取白字、通用兜底 key）
- parse_detail2_attack / outer_pen / attr_pen 的分流派数值提取
- RoleAttrParser.parse 整链（detail_2 覆盖 detail_1 兜底值）
"""

import pytest

from lvjiang.apps.yysls.core.role_attr_parser.parser import (
    RoleAttrParser,
    merge_scroll_snapshots,
    parse_detail1,
    parse_detail2_attack,
    parse_detail2_attr_pen,
    parse_detail2_outer_pen,
)

# 真实滚动识别样例（角色详情页，属性面板_左，向上拖拽 0.5 屏/次）
LEFT_SNAPSHOTS = [
    "武林造诣 | 2.445鹅 | ～24452造 | 五维属性 | 体劲御敏势 | 150 | 200 | 150 | 150 | 279 | 基础属性 | 气血最大值 | 132895(159474)",
    "敏势 | 150 | 279 | 基础属性 | 气血最大值 | 132895(159474) | 真气最大值 | 100 | 外功攻击 | 900-2604 | 属性攻击 | 366-527 | 外功防御 | 550 | 判定属性 | 精准率 | 114.2%(94.8%)",
    "判定属性 | 精准率 | 114.2%(94.8%) | 会心率 | 23.5%(14.2%) | 会意率 | 36.4%(22.1%) | 直接会心率 | 0.0% | 直接会意率 | 2.3% | 擦伤转化率 | 0.0% | 最终会心会意率 | 37.9% | 属性抗性 | 判定抗性 | 65%",
    "擦伤转化率 | 0.0% | 最终会心会意率 | 37.9% | 属性抗性 | 判定抗性 | 65% | 增益抗性 | 0% | 增益效果 | 外功穿透 | 0.0 | 外功抗性 | 0.0 | 属攻穿透 | 10.3 | 全部武学增效 | 0.0%",
    "外功穿透 | 0.0 | 外功抗性 | 0.0 | 属攻穿透 | 10.3 | 全部武学增效 | 0.0% | 指定武学增效 | 0.0% | 指定技能增效 | 0.0% | 对首领单位增伤 | 3.6% | 对玩家单位增效 | 0.0% | 单体类奇术增伤 | 0.0% | 群体类奇术增伤 | 0.0%",
    "对首领单位增伤 | 3.6% | 对玩家单位增效 | 0.0% | 单体类奇术增伤 | 0.0% | 群体类奇术增伤 | 0.0% | 增减伤效果 | 会心伤害加成 | 40.0% | 会意伤害加成 | 30.2% | 外功伤害加成 | 0.0% | 外功伤害减免 | 0.0% | 属攻伤害加成 | 4.1%",
    "会意伤害加成 | 30.2% | 外功伤害加成 | 0.0% | 外功伤害减免 | 0.0% | 属攻伤害加成 | 4.1% | 属攻伤害减免 | 0.0% | 增疗效果 | 属性治疗 | 123-123 | 会心治疗加成 | 50.0% | 外功治疗加成 | 0.0% | 属攻治疗加成 | 4.1%",
]

RIGHT_ATTACK = (
    "220-443 | 属性攻击 | 在对应的武学天赋解锁后，就可以基于全部 | 属性攻击造成额外伤害。 | "
    "每门武学还可以额外提升一种属性攻击的伤 | 害效果。 | 当前各属攻生效数值 | "
    "鸣金攻击：170-343(170-343) | 裂石攻击：0-0(0-0) | 牵丝攻击：50-100(50-100) | 破竹攻击：0-0(0-0) | "
    "无相攻击：0-0(可根据当前使用武学，提升 | 相应流派属性攻击) | 除无相攻击以外的任意类型属攻，在不计算 | "
    "无相攻击动态提升数值的前提下，如果最小"
)
RIGHT_OUTER_PEN = (
    "0.0 | 外功穿透 | 提升外功攻击造成伤害和治疗的能力。 | 伤害提升会受外功抗性影响 | "
    "当前外功穿透(定音部分)：0.0 | 当前外功穿透(非定音部分)：0.0 | 同等级段对抗生效外功穿透：0.0 | "
    "在与关联模式等级达到<二十二级守静>的 | 玩家或怪物对抗时，定音获取部分会受增益 | "
    "抗性影响而生效降低。 | 获取途径 | 装备定音-攻具"
)
RIGHT_ATTR_PEN = (
    "10.3 | 属攻穿透 | 提升属攻攻击造成伤害和治疗的能力。 | 伤害提升会受属攻抗性影响 | "
    "在与关联模式等级达到<二十二级守静>的 | 玩家或怪物对抗时，定音获取部分会受增益 | "
    "抗性影响而生效降低。 | 同等级段对抗生效属攻穿透 | 鸣金穿透：10.3 | 裂石穿透：0.0 | "
    "牵丝穿透：0.0 | 破竹穿透：0.0 | 无相穿透：0.0(可根据当前使用武学，提升 | 相应流派属性穿透"
)


def _raw_dict() -> dict:
    raw = {f"left_{i + 1}": text for i, text in enumerate(LEFT_SNAPSHOTS)}
    raw["right_attack"] = RIGHT_ATTACK
    raw["right_outer_pen"] = RIGHT_OUTER_PEN
    raw["right_attr_pen"] = RIGHT_ATTR_PEN
    return raw


@pytest.fixture(scope="module")
def parser():
    return RoleAttrParser()


# ─── merge_scroll_snapshots ───────────────────────────────

class TestMergeScrollSnapshots:
    def test_merges_without_duplicates(self):
        merged = merge_scroll_snapshots(LEFT_SNAPSHOTS)
        # 每个已知字段标签只出现一次
        assert merged.count("精准率") == 1
        assert merged.count("外功穿透") == 1
        assert merged.count("属攻伤害加成") == 1

    def test_handles_boundary_ocr_noise(self):
        """滚动边界上被裁出的表头残片（如"敏势"）不应破坏重叠匹配、也不重复保留"""
        merged = merge_scroll_snapshots(LEFT_SNAPSHOTS[:2])
        assert merged.count("基础属性") == 1
        assert merged.count("气血最大值") == 1

    def test_repeated_snapshot_is_idempotent(self):
        """原地没滚动/多滚一次导致同一屏被重复喂入 → 不产生新增内容"""
        once = merge_scroll_snapshots(LEFT_SNAPSHOTS)
        twice = merge_scroll_snapshots(LEFT_SNAPSHOTS + [LEFT_SNAPSHOTS[-1], LEFT_SNAPSHOTS[-1]])
        assert once == twice

    def test_empty_input(self):
        assert merge_scroll_snapshots([]) == []


# ─── parse_detail1 ────────────────────────────────────────

class TestParseDetail1:
    # 纯函数、无副作用，直接算成模块级常量即可，不需要走 fixture
    _tokens = merge_scroll_snapshots(LEFT_SNAPSHOTS)

    def test_range_field(self):
        result = parse_detail1(self._tokens)
        assert result["min_outer"] == 900.0
        assert result["max_outer"] == 2604.0

    def test_constant_value_arrow_notation(self):
        """min > max 时游戏改用"箭头 + 单值"展示恒定值（"← 3713"），
        而不是常规的 "min-max" 区间格式 —— 之前会被 _split_range 判定为
        不匹配、整个字段丢失，现在应解析为 min=max=该值。"""
        snapshot = (
            "战斗属性 | 角色状态 | 280 | 基础属性 | 气血最大值 | 251548(301858) | "
            "真气最大值 | 100 | 外功攻击 | ← 3713 | 属性攻击 | 763-1551 | "
            "外功防御 | 877 | 判定属性 | 精准率 | 139.6%(95.5%)"
        )
        tokens = merge_scroll_snapshots([snapshot])
        result = parse_detail1(tokens)
        assert result["min_outer"] == 3713.0
        assert result["max_outer"] == 3713.0

    def test_percent_takes_white_text_not_parenthetical(self):
        """"114.2%(94.8%)" 取括号外的白字数值 114.2，不是括号内的 94.8"""
        result = parse_detail1(self._tokens)
        assert result["precision"] == 114.2
        assert result["crit_rate"] == 23.5
        assert result["intent_rate"] == 36.4

    def test_simple_percent_fields(self):
        result = parse_detail1(self._tokens)
        assert result["direct_crit"] == 0.0
        assert result["direct_intent"] == 2.3
        assert result["crit_dmg"] == 40.0
        assert result["intent_dmg"] == 30.2
        assert result["outer_bonus"] == 0.0

    def test_generic_current_fallback_keys(self):
        result = parse_detail1(self._tokens)
        assert result["min_attr_current"] == 366.0
        assert result["max_attr_current"] == 527.0
        assert result["attr_pen_current"] == 10.3
        assert result["attr_bonus_current"] == 4.1

    def test_unknown_labels_ignored(self):
        result = parse_detail1(self._tokens)
        # 武林造诣/五维属性/基础属性/气血最大值等非 PLAY_STYLE_FIELD_GROUPS
        # 字段一律不解析
        assert "武林造诣" not in result
        assert not any(k.startswith("qixue") for k in result)


# ─── parse_detail2_* ──────────────────────────────────────

class TestParseDetail2Attack:
    def test_extracts_all_schools(self):
        result = parse_detail2_attack(RIGHT_ATTACK)
        assert result["min_mingjin"] == 170.0
        assert result["max_mingjin"] == 343.0
        assert result["min_lieshi"] == 0.0
        assert result["max_lieshi"] == 0.0
        assert result["min_qiansi"] == 50.0
        assert result["max_qiansi"] == 100.0
        assert result["min_pozhu"] == 0.0
        assert result["max_pozhu"] == 0.0
        assert result["min_wuxiang"] == 0.0
        assert result["max_wuxiang"] == 0.0

    def test_empty_text(self):
        assert parse_detail2_attack("") == {}

    def test_constant_value_arrow_notation_mixed_with_ranges(self):
        """某一门武学 min>max（恒定值 "← 数字"）与其余正常区间混在一起时，
        恒定值那一门应解析为 min=max=该值，不影响其余武学的正常区间解析"""
        text = (
            "220-443 | 属性攻击 | ... | 当前各属攻生效数值 | "
            "鸣金攻击：← 1500 | 裂石攻击：0-0(0-0) | 牵丝攻击：50-100(50-100) | "
            "破竹攻击：0-0(0-0) | 无相攻击：0-0(可根据当前使用武学，提升 | 相应流派属性攻击)"
        )
        result = parse_detail2_attack(text)
        assert result["min_mingjin"] == 1500.0
        assert result["max_mingjin"] == 1500.0
        assert result["min_lieshi"] == 0.0
        assert result["max_lieshi"] == 0.0
        assert result["min_qiansi"] == 50.0
        assert result["max_qiansi"] == 100.0


class TestParseDetail2OuterPen:
    def test_takes_non_dingyin_value(self):
        """取"(非定音部分)"数值，不是"(定音部分)"或"同等级段对抗生效"那两行"""
        result = parse_detail2_outer_pen(RIGHT_OUTER_PEN)
        assert result == {"outer_pen": 0.0}

    def test_missing_pattern(self):
        assert parse_detail2_outer_pen("无关文本") == {}


class TestParseDetail2AttrPen:
    def test_extracts_all_schools_excludes_wuxiang(self):
        result = parse_detail2_attr_pen(RIGHT_ATTR_PEN)
        assert result == {
            "mingjin_pen": 10.3,
            "lieshi_pen": 0.0,
            "qiansi_pen": 0.0,
            "pozhu_pen": 0.0,
        }
        # 无相穿透没有对应 COMBAT_ATTR_FIELDS 字段，不应出现
        assert "wuxiang_pen" not in result


# ─── RoleAttrParser.parse 整链 ────────────────────────────

class TestParseIntegration:
    def test_full_pipeline(self, parser):
        result = parser.parse(_raw_dict())
        assert result["min_outer"] == 900.0
        assert result["max_outer"] == 2604.0
        assert result["precision"] == 114.2
        assert result["min_mingjin"] == 170.0
        assert result["max_mingjin"] == 343.0
        assert result["mingjin_pen"] == 10.3
        assert result["attr_bonus_current"] == 4.1

    def test_detail2_overrides_detail1_fallback(self, parser):
        """outer_pen：detail_1 headline 与 detail_2"(非定音部分)"在样例中恰好相同，
        但 detail_2 存在时应以其为准（此处验证覆盖逻辑不报错、结果正确）"""
        result = parser.parse(_raw_dict())
        assert result["outer_pen"] == 0.0

    def test_missing_detail2_falls_back_to_detail1_outer_pen(self, parser):
        raw = _raw_dict()
        del raw["right_outer_pen"]
        result = parser.parse(raw)
        # 无 detail_2 时用 detail_1 里"外功穿透"headline 兜底
        assert result["outer_pen"] == 0.0

    def test_missing_detail2_no_school_breakdown(self, parser):
        raw = _raw_dict()
        del raw["right_attack"]
        result = parser.parse(raw)
        assert "min_mingjin" not in result
        # 但通用兜底 key 仍来自 detail_1
        assert result["min_attr_current"] == 366.0

    def test_empty_input(self, parser):
        assert parser.parse({}) == {}
        assert parser.parse(None) == {}
