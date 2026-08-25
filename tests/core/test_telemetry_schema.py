"""字段白名单机制：EventSchema/FieldSpec 的核心不变量。

两条最关键的断言：
1. 任何 str 字段没有 choices/choices_fn/pattern 之一 → 注册时直接拒绝
   （禁止自由文本字段存在，而不是禁止自由文本值——前者是结构性保证）。
2. validate() 对未声明字段、越界值、类型不符一律抛错，不静默丢弃/放行。
"""
from __future__ import annotations

import pytest

from lvjiang.core.telemetry.schema import (
    EventSchema,
    FieldSpec,
    TelemetrySchemaError,
    ValidatedEvent,
)


class TestFieldSpecRejectsFreeText:
    def test_str_field_without_choices_or_pattern_rejected(self):
        with pytest.raises(TelemetrySchemaError, match="自由文本"):
            FieldSpec("free", str)

    def test_str_field_with_choices_ok(self):
        FieldSpec("x", str, choices=("a", "b"))

    def test_str_field_with_pattern_ok(self):
        FieldSpec("x", str, pattern=r"^[a-z]+$")

    def test_str_field_with_choices_fn_ok(self):
        FieldSpec("x", str, choices_fn=lambda: ["a", "b"])

    def test_int_and_bool_fields_dont_need_choices(self):
        FieldSpec("n", int)
        FieldSpec("b", bool)

    def test_invalid_pattern_rejected_at_registration(self):
        with pytest.raises(Exception):  # noqa: B017 -- re.compile 抛的是 re.error
            FieldSpec("x", str, pattern="[unclosed")


class TestValidate:
    def _schema(self):
        return EventSchema(
            name="t", version=1,
            fields=(
                FieldSpec("a", str, choices=("x", "y")),
                FieldSpec("n", int, minimum=0, maximum=10),
                FieldSpec("opt", str, required=False, choices=("p", "q")),
            ),
        )

    def test_valid_payload_returns_validated_event(self):
        ev = self._schema().validate({"a": "x", "n": 5})
        assert isinstance(ev, ValidatedEvent)
        assert ev.schema_name == "t"
        assert ev.schema_version == 1
        assert dict(ev.values) == {"a": "x", "n": 5}

    def test_unknown_field_rejected(self):
        with pytest.raises(TelemetrySchemaError, match="未声明"):
            self._schema().validate({"a": "x", "n": 5, "extra": "PII"})

    def test_missing_required_field_rejected(self):
        with pytest.raises(TelemetrySchemaError, match="缺少必填"):
            self._schema().validate({"a": "x"})

    def test_optional_field_can_be_omitted(self):
        ev = self._schema().validate({"a": "x", "n": 1})
        assert "opt" not in ev.values

    def test_value_outside_choices_rejected(self):
        with pytest.raises(TelemetrySchemaError):
            self._schema().validate({"a": "not_in_choices", "n": 1})

    def test_int_out_of_range_rejected(self):
        with pytest.raises(TelemetrySchemaError):
            self._schema().validate({"a": "x", "n": 999})

    def test_wrong_type_rejected(self):
        with pytest.raises(TelemetrySchemaError):
            self._schema().validate({"a": "x", "n": "not an int"})

    def test_bool_not_accepted_as_int(self):
        """True/False 是 int 子类，必须显式排除，否则 minimum/maximum 语义会错乱。"""
        with pytest.raises(TelemetrySchemaError):
            self._schema().validate({"a": "x", "n": True})

    def test_none_value_treated_as_absent(self):
        ev = self._schema().validate({"a": "x", "n": 1, "opt": None})
        assert "opt" not in ev.values

    def test_pattern_mismatch_rejected(self):
        schema = EventSchema(name="p", version=1,
                             fields=(FieldSpec("id", str, pattern=r"^[0-9a-f]{32}$"),))
        with pytest.raises(TelemetrySchemaError):
            schema.validate({"id": "not-a-uuid"})
        ev = schema.validate({"id": "0" * 32})
        assert ev.values["id"] == "0" * 32

    def test_max_length_enforced(self):
        schema = EventSchema(name="l", version=1,
                             fields=(FieldSpec("s", str, choices=("ab",), max_length=1),))
        with pytest.raises(TelemetrySchemaError):
            schema.validate({"s": "ab"})


class TestValuesAreImmutable:
    def test_values_mapping_is_read_only(self):
        schema = EventSchema(name="t", version=1,
                             fields=(FieldSpec("a", str, choices=("x",)),))
        ev = schema.validate({"a": "x"})
        with pytest.raises(TypeError):
            ev.values["a"] = "y"  # type: ignore[index]


class TestExample:
    def test_example_keys_match_field_names(self):
        schema = EventSchema(
            name="t", version=1,
            fields=(
                FieldSpec("a", str, choices=("x", "y")),
                FieldSpec("n", int, minimum=0),
                FieldSpec("f", float, minimum=0.0),
                FieldSpec("b", bool),
                FieldSpec("opt", str, required=False, choices=("p",)),
            ),
        )
        example = schema.example()
        assert set(example) == schema.field_names()
        # example() 产出的必须能自己通过 validate（不能给出一份自相矛盾的样例）
        schema.validate(example)
