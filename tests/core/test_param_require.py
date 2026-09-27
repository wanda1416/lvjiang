"""参数依赖 ``require``：借用 DSL 条件语法的解析、静态校验与可见性求值。"""

import pytest

from lvjiang.core.param_require import (
    RequireError,
    validate_requires,
    visible_parameter_names,
)
from lvjiang.core.task_params import (
    merge_task_params,
    parameters_for_values,
)
from lvjiang.workflows.metadata import WorkflowMetadataError, parse_metadata

_DEFS = [
    {"name": "skip_online_role", "type": "bool", "default": True},
    {"name": "online_role_max_wait", "type": "number", "default": 0,
     "require": "not $skip_online_role"},
    {"name": "mode", "type": "select", "default": "fast",
     "options": [{"value": "fast", "label": "快速"},
                 {"value": "full", "label": "完整"}]},
    {"name": "detail", "type": "text",
     "require": ['$mode in ["fast", "full"]', "not $skip_online_role"]},
]


def _visible(values: dict) -> list[str]:
    return [item["name"] for item in parameters_for_values(_DEFS, values)]


class TestVisibility:
    def test_dependent_parameter_appears_only_when_condition_holds(self):
        assert _visible({"skip_online_role": True, "mode": "fast"}) == [
            "skip_online_role", "mode"]
        assert _visible({"skip_online_role": False, "mode": "fast"}) == [
            "skip_online_role", "online_role_max_wait", "mode", "detail"]

    def test_string_form_of_bool_follows_dsl_truthiness(self):
        """控件与配置里 bool 可能存成字符串，判定要和 DSL 正文一致。"""
        assert "online_role_max_wait" not in _visible(
            {"skip_online_role": "true", "mode": "fast"})
        assert "online_role_max_wait" in _visible(
            {"skip_online_role": "", "mode": "fast"})

    def test_in_list_accepts_numbers_and_their_string_form(self):
        defs = [
            {"name": "level", "type": "number", "default": 1},
            {"name": "extra", "type": "text", "require": "$level in [1, 2, 3, 4]"},
        ]
        for value in (3, "3"):
            assert visible_parameter_names(defs, {"level": value}) == {
                "level", "extra"}
        assert visible_parameter_names(defs, {"level": 9}) == {"level"}

    def test_list_require_is_and(self):
        # mode 不在列表里 → detail 隐藏，即使另一条件成立
        assert "detail" not in _visible(
            {"skip_online_role": False, "mode": "other"})

    def test_hidden_upstream_hides_downstream(self):
        """条件挂在一个看不见的控件上时，依赖它的参数也不展示。"""
        defs = [
            {"name": "a", "type": "bool", "default": False},
            {"name": "b", "type": "bool", "default": True, "require": "$a"},
            {"name": "c", "type": "text", "require": "$b"},
        ]
        assert visible_parameter_names(defs, {"a": False, "b": True}) == {"a"}
        assert visible_parameter_names(defs, {"a": True, "b": True}) == {
            "a", "b", "c"}

    def test_require_does_not_trim_the_runtime_snapshot(self):
        """隐藏只作用于 UI：wf 仍拿到该变量，引用它不会未定义。"""
        effective, _source = merge_task_params(_DEFS, {}, None)
        assert effective["online_role_max_wait"] == 0
        assert "online_role_max_wait" not in _visible(
            {"skip_online_role": True, "mode": "fast"})


class TestStaticValidation:
    def test_accepts_the_shipped_definitions(self):
        validate_requires(_DEFS)

    def test_rejects_unknown_reference(self):
        with pytest.raises(RequireError, match="未声明的参数"):
            validate_requires([
                {"name": "a", "type": "text", "require": "not $nope"}])

    def test_rejects_self_reference(self):
        with pytest.raises(RequireError, match="不能引用自身"):
            validate_requires([
                {"name": "a", "type": "bool", "require": "$a"}])

    def test_rejects_cycles(self):
        with pytest.raises(RequireError, match="循环依赖"):
            validate_requires([
                {"name": "a", "type": "bool", "require": "$b"},
                {"name": "b", "type": "bool", "require": "$a"},
            ])

    @pytest.mark.parametrize("expression", [
        "$skip_online_role == false",
        "$skip_online_role != true",
        "$skip_online_role in [true]",
    ])
    def test_accepts_boolean_equality(self, expression):
        """布尔相等已按类型判等，== false 与 not $flag 等价，不再需要拦。"""
        validate_requires([
            *_DEFS, {"name": "probe", "type": "text", "require": expression}])

    @pytest.mark.parametrize("expression", [
        "$skip_online_role == 1",
        "$skip_online_role == 0",
        '$skip_online_role == "true"',
    ])
    def test_rejects_non_bool_comparison_against_bool(self, expression):
        """布尔只与布尔相等，拿数字或字符串比一定不成立，属于写错。"""
        with pytest.raises(RequireError, match="只能用 true / false"):
            validate_requires([
                *_DEFS, {"name": "probe", "type": "text", "require": expression}])

    def test_boolean_equality_and_truthiness_agree(self):
        """`== false` 与 `not $flag` 必须给出同一个可见性结果。"""
        for expression in ("not $skip_online_role", "$skip_online_role == false"):
            defs = [
                {"name": "skip_online_role", "type": "bool", "default": True},
                {"name": "wait", "type": "number", "default": 0,
                 "require": expression},
            ]
            validate_requires(defs)
            assert visible_parameter_names(defs, {"skip_online_role": False}) == {
                "skip_online_role", "wait"}
            assert visible_parameter_names(defs, {"skip_online_role": True}) == {
                "skip_online_role"}

    def test_rejects_value_outside_select_options(self):
        with pytest.raises(RequireError, match="不在"):
            validate_requires([
                *_DEFS,
                {"name": "probe", "type": "text", "require": '$mode == "zzz"'}])

    def test_rejects_non_numeric_comparison_against_number(self):
        with pytest.raises(RequireError, match="必须用数字"):
            validate_requires([
                *_DEFS,
                {"name": "probe", "type": "text",
                 "require": '$online_role_max_wait == "x"'}])

    def test_rejects_checkgroup_reference(self):
        with pytest.raises(RequireError, match="不能引用 checkgroup"):
            validate_requires([
                {"name": "flags", "type": "checkgroup", "options": ["x"]},
                {"name": "probe", "type": "text", "require": "$flags"},
            ])

    @pytest.mark.parametrize(("expression", "rejected"), [
        ('profile_get("x") == 1', "FuncCall"),
        ('session.user == "a"', "FieldAccess"),
        ('$mode.sub == "a"', "FieldAccess"),
    ])
    def test_rejects_side_effecting_or_runtime_syntax(self, expression, rejected):
        """UI 每次改值都要重算，不允许调用函数或读运行时状态。"""
        with pytest.raises(RequireError, match=rejected):
            validate_requires([
                *_DEFS,
                {"name": "probe", "type": "text", "require": expression}])

    def test_rejects_unparsable_expression(self):
        with pytest.raises(RequireError, match="DSL 条件语法"):
            validate_requires([
                *_DEFS, {"name": "probe", "type": "text", "require": "$mode =="}])


class TestBoolText:
    """布尔字面文本只认 true / false / 1 / 0。"""

    @pytest.mark.parametrize(("text", "expected"), [
        ("true", True), ("TRUE", True), ("1", True),
        ("false", False), ("0", False), (" true ", True),
    ])
    def test_accepted_forms(self, text, expected):
        from lvjiang.workflows.builtins._coerce import is_bool_text, to_bool

        assert is_bool_text(text)
        assert to_bool(text) is expected

    @pytest.mark.parametrize("text", ["yes", "no", "on", "off", "", "maybe"])
    def test_rejected_forms_are_not_bool_text(self, text):
        """yes / no / on / off 既不是 DSL 字面量也不是 JSON/YAML 写法，不再认。"""
        from lvjiang.workflows.builtins._coerce import is_bool_text, to_bool

        assert not is_bool_text(text)
        assert to_bool(text) is False

    def test_real_bool_passes_through(self):
        from lvjiang.workflows.builtins._coerce import to_bool

        assert to_bool(True) is True
        assert to_bool(False) is False

    @pytest.mark.parametrize("default", ["yes", "no", "on", "off"])
    def test_bool_default_rejects_the_old_loose_spellings(self, default):
        with pytest.raises(WorkflowMetadataError, match="true / false / 1 / 0"):
            parse_metadata(
                "#% parameters:\n"
                "#%   - name: flag\n"
                "#%     type: bool\n"
                f"#%     default: '{default}'\n")

    @pytest.mark.parametrize("default", ["true", "false", "1", "0"])
    def test_bool_default_still_accepts_the_four(self, default):
        meta = parse_metadata(
            "#% parameters:\n"
            "#%   - name: flag\n"
            "#%     type: bool\n"
            f"#%     default: '{default}'\n")
        assert meta["parameters"][0]["default"] == default


class TestMetadataIntegration:
    _HEAD = (
        "#% parameters:\n"
        "#%   - name: skip_online_role\n"
        "#%     type: bool\n"
        "#%     default: true\n"
        "#%   - name: wait\n"
        "#%     type: number\n"
        "#%     default: 0\n"
    )

    def test_require_is_kept_in_normalized_metadata(self):
        meta = parse_metadata(self._HEAD + "#%     require: not $skip_online_role\n")
        assert meta["parameters"][1]["require"] == "not $skip_online_role"

    def test_bad_reference_is_a_metadata_error(self):
        """拼错参数名会让控件永远不出现，必须在解析期报出来。"""
        with pytest.raises(WorkflowMetadataError, match="未声明的参数"):
            parse_metadata(self._HEAD + "#%     require: not $skip_onlin_role\n")

    def test_shipped_prepare_workflows_declare_the_dependency(self):
        """最大等待时间只有关掉「角色在线跳过」才有意义。"""
        from pathlib import Path

        from lvjiang.workflows.metadata import parse_metadata_file

        for name in ("prepare_item.wf", "prepare_item_by_attr.wf"):
            defs = parse_metadata_file(
                Path("config/system/workflows/batch") / name)["parameters"]
            wait = next(item for item in defs
                        if item["name"] == "online_role_max_wait")
            assert wait["require"] == "not $skip_online_role"
            shown = {item["name"] for item in parameters_for_values(
                defs, {"skip_online_role": True})}
            assert "online_role_max_wait" not in shown
