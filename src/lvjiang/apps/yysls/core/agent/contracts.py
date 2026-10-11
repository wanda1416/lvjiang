"""MCP 字段说明；领域值及规则合法性仍由现有解析器判定。"""
from typing import Annotated

from pydantic import Field

from ...config.auto_tuning_config import default_auto_tuning_config
from ...config.tune_slots import DEFAULT_SLOTS

_STRING = {"type": "string"}
_STRINGS = {"type": "array", "items": _STRING}
_OVERRIDES = {
    "type": "object", "additionalProperties": False,
    "properties": {key: _STRING for key in ("playstyle", "graduation_scheme", "base_attribute")},
}


def _setting_schema(key: str, value) -> dict:
    if key == "selected_slots":
        return {"type": "array", "items": {"type": "string", "enum": list(DEFAULT_SLOTS)}, "minItems": 1}
    if key == "scroll_strategy":
        return {"type": "string", "enum": ["", "positional"]}
    if key == "rules":
        return {"type": "object", "additionalProperties": {
            "type": "object", "additionalProperties": False,
            "properties": {"enabled": {"type": "boolean"}, "playstyles": _STRINGS}}}
    if isinstance(value, dict):
        return {"type": "object", "additionalProperties": {"type": "boolean"}}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, list):
        return _STRINGS
    if key in {"skip_start", "target_cell"}:
        return {"type": ["array", "null"], "minItems": 2, "maxItems": 2,
                "items": {"type": "integer", "minimum": 1}}
    if value is None:
        return {"type": ["integer", "null"], "minimum": 0}
    return _STRING


_SETTINGS = {key: _setting_schema(key, value)
             for key, value in default_auto_tuning_config().items() if key != "skip_tuning"}
_PAYLOAD = {
    "type": "object", "additionalProperties": False, "required": ["base_group", "rules"],
    "properties": {
        "name": _STRING, "goal": _STRING, "suggestions": _STRINGS, "plan_ids": _STRINGS,
        "plan_targets": {"type": "object", "additionalProperties": _OVERRIDES},
        "base_group": {"type": "object", "description": "完整基础组。读取 get_tuning_config 的 base_groups 与 generation 文档；业务字段由基础组解析器校验。"},
        "rules": {"type": "array", "minItems": 1, "maxItems": 10,
                  "items": {"type": "object"},
                  "description": "完整新规则。先读 get_tuning_config 的 rule_contract、tuning_rules；遵循部位归并和合法开关注册表。"},
        "run_config": {"type": "object", "additionalProperties": False,
                       "properties": {key: value for key, value in _SETTINGS.items()
                                      if key not in {"rules", "base_group", "smart_tuning_enabled"}}},
    },
}

GenerationPayload = Annotated[dict, Field(json_schema_extra=_PAYLOAD)]
PlanOverrides = Annotated[dict, Field(json_schema_extra=_OVERRIDES)]
TuningPatch = Annotated[dict, Field(json_schema_extra={
    "type": "object", "additionalProperties": False, "properties": _SETTINGS,
})]
