"""调律事件 schema 声明——经 ``AppHooks.telemetry_modules`` import 时注册。

字段集合是"改进调律规则"这个目标真正需要的最小集：在什么规则激活、
装备第几次调律（含重置）、部位、加什么材料的情况下，出了什么词条、
数值。不含装备名/完整词条组合/账号/角色名——见
docs/60-userguide/08-feedback-and-issues.md 的 PII 边界与 PRIVACY.md。
"""
from __future__ import annotations

from ....core.telemetry.registry import register_schema
from ....core.telemetry.schema import EventSchema, FieldSpec
from . import vocab

_UUID_HEX_PATTERN = r"^[0-9a-f]{32}$"
_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
# 规则 key 是 config/system/yysls/tuning_rules/*.yaml 的文件名 stem（ascii
# 标识符），多条规则联合启用时排序后以 "+" 拼接；"none" 表示未启用任何规则。
_ACTIVE_RULE_PATTERN = r"^(none|[a-z0-9_]+(\+[a-z0-9_]+)*)$"

TUNING_ROLL_SCHEMA = EventSchema(
    name="yysls.tuning_roll", version=1,
    fields=(
        FieldSpec("install_id", str, pattern=_UUID_HEX_PATTERN, example="0" * 32),
        FieldSpec("date", str, pattern=_DATE_PATTERN, example="2026-01-01"),
        FieldSpec("part", str, choices_fn=vocab.part_choices),
        FieldSpec("weapon_type", str, required=False, choices_fn=vocab.weapon_type_choices),
        FieldSpec("level", int, minimum=1, maximum=999),
        FieldSpec("quality", str, required=False, choices_fn=vocab.quality_choices),
        FieldSpec("food", str, choices_fn=vocab.food_choices),
        FieldSpec("slot", int, minimum=1, maximum=5),
        FieldSpec("roll_index", int, minimum=1, maximum=100_000),
        FieldSpec("resets", int, minimum=0, maximum=100_000),
        FieldSpec("mode", str, choices_fn=vocab.mode_choices),
        FieldSpec("active_rule", str, pattern=_ACTIVE_RULE_PATTERN, max_length=256,
                  example="none"),
        FieldSpec("affix", str, choices_fn=vocab.affix_choices),
        FieldSpec("cap_pct", float, minimum=0.0, maximum=100.0),
        FieldSpec("is_transferred", bool),
        FieldSpec("season", int, required=False, minimum=0, maximum=9999),
        FieldSpec("game_config_customized", bool),
    ),
)
register_schema(TUNING_ROLL_SCHEMA)
