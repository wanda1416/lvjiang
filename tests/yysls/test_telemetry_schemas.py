"""调律事件 schema 的结构性断言：字段名快照 + 「无自由文本字段」硬约束。

字段名快照是「有人手滑塞了用户数据」的那道闸：改字段必须同步改这里，
review 时一眼能在 diff 里看见。
"""
from __future__ import annotations

from lvjiang.apps.yysls.telemetry.schemas import TUNING_ROLL_SCHEMA

_EXPECTED_FIELDS = frozenset({
    "install_id", "date", "part", "weapon_type", "level", "quality",
    "food", "slot", "roll_index", "resets", "mode", "active_rule",
    "affix", "cap_pct", "is_transferred", "season", "game_config_customized",
})


class TestFieldSnapshot:
    def test_field_names_match_snapshot(self):
        assert TUNING_ROLL_SCHEMA.field_names() == _EXPECTED_FIELDS

    def test_no_pii_named_fields(self):
        """字段名本身就不该出现这些词——即便将来有人想加，命名也会先撞见 review。"""
        forbidden_substrings = ("name", "fp", "account", "role", "user", "path", "log")
        for field_name in TUNING_ROLL_SCHEMA.field_names():
            for bad in forbidden_substrings:
                assert bad not in field_name.lower(), (
                    f"字段名 {field_name!r} 含疑似 PII 词根 {bad!r}")


class TestNoFreeTextFields:
    def test_every_str_field_has_choices_or_pattern(self):
        for spec in TUNING_ROLL_SCHEMA.fields:
            if spec.kind is str:
                assert spec.choices or spec.choices_fn or spec.pattern, (
                    f"字段 {spec.name!r} 是自由文本，禁止")


class TestExamplePayloadIsValid:
    def test_example_round_trips(self):
        example = TUNING_ROLL_SCHEMA.example()
        TUNING_ROLL_SCHEMA.validate(example)  # 不抛即通过
