"""调律事件 schema 的结构性断言：字段名快照 + 「无自由文本字段」硬约束。

字段名快照是「有人手滑塞了用户数据」的那道闸：改字段必须同步改这里，
review 时一眼能在 diff 里看见。嵌套列表（initial_affixes / rolls）里的
键同样纳入快照与自由文本检查——嵌套一层不等于放宽白名单。
"""
from __future__ import annotations

from lvjiang.apps.yysls.telemetry.schemas import TUNING_SESSION_SCHEMA
from lvjiang.core.telemetry.schema import FieldSpec, ListSpec

_EXPECTED_FIELDS = frozenset({
    "install_id", "date", "part", "weapon_type", "level", "quality",
    "mode", "active_rule", "season", "game_config_customized",
    "initial_affixes", "rolls",
    "stop_reason", "final_rating", "total_rounds", "resets",
})

_EXPECTED_NESTED = {
    "initial_affixes": frozenset({"affix", "cap_pct", "is_transferred"}),
    "rolls": frozenset({"affix", "cap_pct", "is_transferred",
                        "slot", "food", "resets"}),
}


def _all_field_specs():
    """展平顶层与嵌套的全部 FieldSpec。"""
    for spec in TUNING_SESSION_SCHEMA.fields:
        if isinstance(spec, ListSpec):
            yield from spec.item_fields
        else:
            yield spec


class TestFieldSnapshot:
    def test_field_names_match_snapshot(self):
        assert TUNING_SESSION_SCHEMA.field_names() == _EXPECTED_FIELDS

    def test_nested_field_names_match_snapshot(self):
        actual = {
            spec.name: frozenset(f.name for f in spec.item_fields)
            for spec in TUNING_SESSION_SCHEMA.fields
            if isinstance(spec, ListSpec)
        }
        assert actual == _EXPECTED_NESTED

    def test_no_pii_named_fields(self):
        """字段名本身就不该出现这些词——即便将来有人想加，命名也会先撞见 review。"""
        forbidden_substrings = ("name", "fp", "account", "role", "user", "path", "log")
        names = set(TUNING_SESSION_SCHEMA.field_names())
        names |= {spec.name for spec in _all_field_specs()}
        for field_name in names:
            for bad in forbidden_substrings:
                assert bad not in field_name.lower(), (
                    f"字段名 {field_name!r} 含疑似 PII 词根 {bad!r}")


class TestNoFreeTextFields:
    def test_every_str_field_has_choices_or_pattern(self):
        """顶层与嵌套一视同仁：任何 str 字段都必须有枚举或正则约束。"""
        for spec in _all_field_specs():
            if spec.kind is str:
                assert spec.choices or spec.choices_fn or spec.pattern, (
                    f"字段 {spec.name!r} 是自由文本，禁止")

    def test_free_text_nested_field_is_rejected_at_declaration(self):
        """嵌套层的自由文本必须在**声明期**就被拒，而不是等运行时校验。"""
        import pytest

        from lvjiang.core.telemetry.schema import TelemetrySchemaError
        with pytest.raises(TelemetrySchemaError):
            ListSpec("bad", (FieldSpec("free", str),))


class TestListBounds:
    def test_lists_declare_max_items(self):
        """列表长度直接决定 payload 体积，必须有上限，否则一次异常会话
        就能把单条事件撑到几百 KB。"""
        for spec in TUNING_SESSION_SCHEMA.fields:
            if isinstance(spec, ListSpec):
                assert spec.max_items > 0

    def test_oversized_list_is_rejected(self):
        import pytest

        from lvjiang.core.telemetry.schema import TelemetrySchemaError
        rolls_spec = next(s for s in TUNING_SESSION_SCHEMA.fields
                          if isinstance(s, ListSpec) and s.name == "rolls")
        too_many = [{"affix": "x", "cap_pct": 0.0, "is_transferred": False,
                     "slot": 1, "food": "none", "resets": 0}] * (rolls_spec.max_items + 1)
        with pytest.raises(TelemetrySchemaError):
            rolls_spec.validate_value(too_many)


class TestExamplePayloadIsValid:
    def test_example_round_trips(self):
        example = TUNING_SESSION_SCHEMA.example()
        TUNING_SESSION_SCHEMA.validate(example)  # 不抛即通过
