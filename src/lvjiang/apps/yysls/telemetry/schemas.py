"""调律事件 schema 声明——经 ``AppHooks.telemetry_modules`` import 时注册。

一条事件 = **一件装备从进调律页面到离开**，不是一轮调律。

粒度定在"一件"而不是"一轮"，是因为要改进的规则问的是序列问题：
「已经有会心了，再洗出破防的概率是多少」「什么情况下该停手」。逐轮独立
上报的事件之间没有任何关联字段（刻意不带装备 id），这些问题在服务端
永远拼不回来。按件上报还顺带把写入量降了一个量级——一件约 8 轮，
D1 行数同比例下降。

字段集合仍是达成这个目标的最小集：不含装备名、账号、角色名、截图。
词条名一律经 ``vocab.normalize_affix_name`` 精确命中普通词条池
（``get_normal_affix_names()``，当前 37 条）才允许出现；命中不了的整条
丢弃，见 probe.py 的说明。
"""
from __future__ import annotations

from ....core.telemetry.registry import register_schema
from ....core.telemetry.schema import EventSchema, FieldSpec, ListSpec
from . import vocab

_UUID_HEX_PATTERN = r"^[0-9a-f]{32}$"
_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
# 规则 key 是 config/system/yysls/tuning_rules/*.yaml 的文件名 stem（ascii
# 标识符），多条规则联合启用时排序后以 "+" 拼接；"none" 表示未启用任何规则。
_ACTIVE_RULE_PATTERN = r"^(none|[a-z0-9_]+(\+[a-z0-9_]+)*)$"

# 词条条目：初始词条与逐轮产出共用同一份约束。
#
# ⚠️ 两个列表的顺序语义不同，下游分析必须区分：
#   initial_affixes —— **槽位序**（宫商角徵羽，见 parser._parse_affixes），
#                       下标 i 对应第 i+1 格；
#   rolls           —— **时间序**，下标 i 对应本件第 i+1 轮，跨重置连续累加。
# 逐轮产出各自带 slot，所以「第 N 格出什么」与「第 N 轮出什么」都能算；
# 但重置会把 slot 打回 1，跨重置重建终态词条组合必须先按 resets 分段。
_AFFIX_FIELDS = (
    FieldSpec("affix", str, choices_fn=vocab.affix_choices),
    FieldSpec("cap_pct", float, minimum=0.0, maximum=100.0),
    FieldSpec("is_transferred", bool),
)

# 逐轮产出比初始词条多三个随轮变化的上下文。三者都不能提到外层：
# slot 在重置时归零、food 每轮可换、resets 轮内递增。
_ROLL_FIELDS = _AFFIX_FIELDS + (
    FieldSpec("slot", int, minimum=1, maximum=5),
    FieldSpec("food", str, choices_fn=vocab.food_choices),
    FieldSpec("resets", int, minimum=0, maximum=100),
)

# 一件装备最多 5 条词条；轮次上限给足冗余（含重置后累计），超出即视为
# 异常数据不予上报——正常调律不可能上百轮。
_MAX_INITIAL_AFFIXES = 5
_MAX_ROLLS = 64

TUNING_SESSION_SCHEMA = EventSchema(
    name="yysls.tuning_session", version=1,
    fields=(
        FieldSpec("install_id", str, pattern=_UUID_HEX_PATTERN, example="0" * 32),
        FieldSpec("date", str, pattern=_DATE_PATTERN, example="2026-01-01"),
        # ── 装备静态属性（整件不变）──
        FieldSpec("part", str, choices_fn=vocab.part_choices),
        FieldSpec("weapon_type", str, required=False, choices_fn=vocab.weapon_type_choices),
        FieldSpec("level", int, minimum=1, maximum=999),
        FieldSpec("quality", str, required=False, choices_fn=vocab.quality_choices),
        # ── 上下文 ──
        FieldSpec("mode", str, choices_fn=vocab.mode_choices),
        FieldSpec("active_rule", str, pattern=_ACTIVE_RULE_PATTERN, max_length=256,
                  example="none"),
        FieldSpec("season", int, required=False, minimum=0, maximum=9999),
        FieldSpec("game_config_customized", bool),
        # ── 过程 ──
        ListSpec("initial_affixes", _AFFIX_FIELDS, max_items=_MAX_INITIAL_AFFIXES),
        ListSpec("rolls", _ROLL_FIELDS, max_items=_MAX_ROLLS),
        # ── 结果 ──
        FieldSpec("stop_reason", str, choices_fn=vocab.stop_reason_choices),
        FieldSpec("final_rating", str, required=False,
                  choices_fn=vocab.rating_choices),
        FieldSpec("total_rounds", int, minimum=0, maximum=_MAX_ROLLS),
        FieldSpec("resets", int, minimum=0, maximum=100),
    ),
)
register_schema(TUNING_SESSION_SCHEMA)
