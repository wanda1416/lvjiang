"""用户 Profile 数据模型定义测试

覆盖 profile_models.py 的数据类序列化/反序列化、parse_key_def 分发。
"""

import pytest
from tests.case_matrix import case_matrix

from lvjiang.core.profile.models import (
    DEFAULT_KEY_GROUP,
    DIR_BOTH,
    DIR_NEG,
    MODEL_QUOTA,
    MODEL_REGEN,
    MODEL_STOCK,
    KeyDef,
    NoteKeyDef,
    QuotaKeyDef,
    RegenKeyDef,
    StepDef,
    StockKeyDef,
    SyncTargetDef,
    format_sync_label,
    group_key_definitions,
    parse_key_def,
    parse_steps,
    parse_sync_key,
    parse_sync_targets,
)

# ─── KeyDef 基类 ─────────────────────────────────────────────


class TestKeyDef:

    def test_from_dict(self):
        kd = KeyDef.from_dict({
            "key": "test_key",
            "label": "测试",
            "description": "描述",
            "sources": ["导入", " 同步 ", ""],
            "uses": ["导出", " 扣减 ", ""],
        })
        assert kd.key == "test_key"
        assert kd.label == "测试"
        assert kd.description == "描述"
        # 词表去空白、过滤空项
        assert kd.sources == ["导入", "同步"]
        assert kd.uses == ["导出", "扣减"]


    def test_group_defaults_for_missing_or_blank_value(self):
        assert KeyDef.from_dict({"key": "missing"}).group == DEFAULT_KEY_GROUP
        assert KeyDef.from_dict({"key": "blank", "group": "  "}).group == DEFAULT_KEY_GROUP

    def test_group_roundtrip_and_order(self):
        definitions = [
            KeyDef.from_dict({"key": "a"}),
            KeyDef.from_dict({"key": "b", "group": "资产"}),
            KeyDef.from_dict({"key": "c"}),
        ]
        grouped = group_key_definitions(definitions)

        assert list(grouped) == ["default", "资产"]
        assert [kd.key for kd in grouped["default"]] == ["a", "c"]
        assert definitions[1].to_dict()["group"] == "资产"


# ─── QuotaKeyDef ─────────────────────────────────────────────


class TestQuotaKeyDef:


    def test_from_dict_steps_dict_format_with_source(self):
        """新格式 dict 条目携带来源"""
        kd = QuotaKeyDef.from_dict({
            "key": "energy_qu", "label": "能量", "cap": 100,
            "steps": [
                {"value": -900, "source": "导入消耗"},
                -1100,
            ],
            "sync_targets": [{"key": "stock:res", "ratio": 1.0, "source": "自动导入"}],
            "sources": ["导入", "同步"],
            "uses": ["导入消耗"],
        })
        assert kd.steps == [StepDef(-900, "导入消耗"), StepDef(-1100)]
        assert kd.sync_targets[0].source == "自动导入"
        assert kd.sources == ["导入", "同步"]
        assert kd.uses == ["导入消耗"]


    def test_to_dict_steps_with_source(self):
        """有 source 的 StepDef 序列化为 dict"""
        kd = QuotaKeyDef(
            key="k", label="l",
            steps=[StepDef(-900, "导入消耗"), StepDef(-1100)],
            sync_targets=[SyncTargetDef(key="stock:res", source="同步来源")],
        )
        d = kd.to_dict()
        assert d["steps"] == [{"value": -900, "source": "导入消耗"}, -1100]
        assert d["sync_targets"] == [{"key": "stock:res", "source": "同步来源"}]

    def test_to_dict_does_not_auto_compact_similar_rules(self):
        kd = QuotaKeyDef(
            key="k", label="l",
            steps=[
                StepDef(1, "其他任务"), StepDef(5, "其他任务"),
                StepDef(1, "妙妙喵"), StepDef(5, "妙妙喵"),
                StepDef(1, "限时活动"),
            ],
        )

        assert kd.to_dict()["steps"] == [
            {"value": 1, "source": "其他任务"},
            {"value": 5, "source": "其他任务"},
            {"value": 1, "source": "妙妙喵"},
            {"value": 5, "source": "妙妙喵"},
            {"value": 1, "source": "限时活动"},
        ]

    def test_to_dict_preserves_an_explicit_multi_value_group(self):
        serialized = {
            "values": [1, 5],
            "sources": ["其他任务", "妙妙喵"],
        }
        steps = parse_steps([serialized])
        kd = QuotaKeyDef(key="k", label="l", steps=steps)

        assert kd.to_dict()["steps"] == [serialized]
        assert parse_steps([serialized]) == kd.steps


# ─── RegenKeyDef ──────────────────────────────────────────


class TestRegenKeyDef:

    def test_from_dict(self):
        kd = RegenKeyDef.from_dict({
            "key": "energy",
            "label": "能量",
            "cap": 2500,
            "regen_type": "boundary",
            "regen_amount": 450,
            "regen_period": "day",
            "alert_orange": 2000,
            "alert_red": 2300,
        })
        assert kd.cap == 2500
        assert kd.regen_type == "boundary"
        assert kd.regen_period == "day"
        assert kd.regen_amount == 450
        assert kd.alert_orange == 2000
        assert kd.alert_red == 2300


# ─── StockKeyDef ──────────────────────────────────────────


# ─── parse_key_def ───────────────────────────────────────────


class TestParseKeyDef:
    def test_model_dispatch_preserves_concrete_types(self):
        # quota
        kd = parse_key_def("quota", {"key": "k", "label": "l", "period": "week"})
        assert isinstance(kd, QuotaKeyDef)
        assert kd.period == "week"

        # regen
        kd = parse_key_def("regen", {"key": "k", "label": "l", "cap": 2500})
        assert isinstance(kd, RegenKeyDef)
        assert kd.cap == 2500

        # stock
        kd = parse_key_def("stock", {"key": "k", "label": "l"})
        assert isinstance(kd, StockKeyDef)

        # note
        kd = parse_key_def("note", {"key": "k", "label": "l"})
        assert isinstance(kd, NoteKeyDef)


    def test_unknown_model_raises(self):
        with pytest.raises(ValueError, match="未知模型类型"):
            parse_key_def("unknown", {"key": "k"})


# ─── 常量 ────────────────────────────────────────────────────


class TestStepDef:
    @case_matrix("raw,expected_value,expected_source", [
        (100, 100, ""),
        ({"value": -900, "source": " 导入消耗 "}, -900, "导入消耗"),
    ])
    def test_from_raw(self, raw, expected_value, expected_source):
        s = StepDef.from_raw(raw)
        assert s.value == expected_value
        assert s.source == expected_source

    @case_matrix("step,expected", [
        (StepDef(10), 10),
        (StepDef(-900, "导入"), {"value": -900, "source": "导入"}),
    ])
    def test_to_dict(self, step, expected):
        assert step.to_dict() == expected

    def test_parse_steps_mixed(self):
        steps = parse_steps([1, {"value": 2, "source": "同步"}])
        assert steps == [StepDef(1), StepDef(2, "同步")]

    def test_parse_steps_expands_plural_values_and_sources(self):
        steps = parse_steps([
            {"values": [1, 5], "sources": ["其他任务", "妙妙喵"]},
            {"value": -1, "sources": ["商店", "活动"]},
        ])

        assert steps == [
            StepDef(1, "其他任务"), StepDef(5, "其他任务"),
            StepDef(1, "妙妙喵"), StepDef(5, "妙妙喵"),
            StepDef(-1, "商店"), StepDef(-1, "活动"),
        ]

    @case_matrix("input", ["invalid", None])
    def test_parse_steps_non_list(self, input):
        assert parse_steps(input) == []


# ─── SyncTargetDef ───────────────────────────────────────────


class TestSyncTargetDef:


    def test_sync_target_roundtrip_preserves_source_ratio_and_direction(self):
        # roundtrip
        orig = SyncTargetDef(key="stock:credits", ratio=-1.0, source="外部来源")
        d = orig.to_dict()
        restored = SyncTargetDef.from_raw(d)
        assert restored.key == orig.key
        assert restored.ratio == orig.ratio
        assert restored.source == orig.source

        # roundtrip_with_direction
        orig = SyncTargetDef(key="stock:x", ratio=-1.0, direction=DIR_NEG, source="导出")
        restored = SyncTargetDef.from_raw(orig.to_dict())
        assert restored == orig

    @case_matrix("raw,expected_dir", [
        ({"key": "stock:x", "direction": "neg"}, DIR_NEG),
        ({"key": "stock:x"}, DIR_BOTH),
    ])
    def test_from_raw_direction(self, raw, expected_dir):
        assert SyncTargetDef.from_raw(raw).direction == expected_dir

    def test_from_raw_direction_invalid_raises(self):
        with pytest.raises(ValueError, match="无效的同步方向"):
            SyncTargetDef.from_raw({"key": "stock:x", "direction": "sideways"})


# ─── parse_sync_key ─────────────────────────────────────────


class TestParseSyncKey:
    @case_matrix("input,expected", [
        ("stock:credits", ("stock", "credits")),
        ("credits", ("", "credits")),
        ("", ("", "")),
        (None, ("", "")),
        ("  stock : credits  ", ("stock", "credits")),
    ])
    def test_parse_sync_key(self, input, expected):
        assert parse_sync_key(input) == expected


# ─── parse_sync_targets ─────────────────────────────────────


class TestParseSyncTargets:
    def test_sync_target_lists_accept_both_formats_and_filter_empty(self):
        # list_of_dicts
        targets = parse_sync_targets([
            {"key": "stock:a", "ratio": 1.0},
            {"key": "stock:b", "ratio": -1.0},
        ])
        assert len(targets) == 2
        assert targets[0].key == "stock:a"
        assert targets[1].ratio == -1.0

        # list_of_strings
        targets = parse_sync_targets(["stock:a", "stock:b"])
        assert len(targets) == 2
        assert targets[0].key == "stock:a"

        # filters_empty
        targets = parse_sync_targets([None, "", {"key": "stock:a"}])
        assert len(targets) == 1


# ─── format_sync_label ──────────────────────────────────────


class TestFormatSyncLabel:
    def test_unknown_sync_keys_keep_their_original_label(self):
        # unknown_key_returns_raw
        assert format_sync_label("nonexistent_xyz:key_abc") == "nonexistent_xyz:key_abc"

        # unknown_bare_key_returns_raw
        assert format_sync_label("totally_nonexistent_key_xyz") == "totally_nonexistent_key_xyz"


    def test_positive_rendering(self, monkeypatch):
        """正向渲染：命名空间 key → '模型标签：字段标签'（中文冒号）"""
        from lvjiang.core.profile.schema import ProfileSchema

        schema = ProfileSchema(keys_by_model={
            MODEL_QUOTA: [],
            MODEL_REGEN: [],
            MODEL_STOCK: [StockKeyDef(key="credits", label="积分")],
        })
        monkeypatch.setattr(
            "lvjiang.core.profile.schema.get_profile_config",
            lambda: schema,
        )
        assert format_sync_label("stock:credits") == "库存：积分"


# ─── KeyDef sync_targets ───────────────────────────────────


class TestKeyDefSyncTargets:
    def test_change_script_roundtrip_for_concrete_model(self):
        kd = StockKeyDef.from_dict({
            "key": "credits",
            "label": "积分",
            "change_script": "profile/credits_changed.wf",
        })
        assert kd.change_script == "profile/credits_changed.wf"
        assert kd.to_dict()["change_script"] == "profile/credits_changed.wf"


    def test_concrete_models_keep_sync_targets_and_uses(self):
        # regen_from_dict_parses_sync_targets
        kd = RegenKeyDef.from_dict({
            "key": "energy", "label": "能量",
            "sync_targets": [{"key": "stock:credits", "ratio": 0.5}],
            "uses": ["导入消耗"],
        })
        assert len(kd.sync_targets) == 1
        assert kd.sync_targets[0].key == "stock:credits"
        assert kd.sync_targets[0].ratio == 0.5
        assert kd.uses == ["导入消耗"]

        # stock_from_dict_parses_sync_targets
        kd = StockKeyDef.from_dict({
            "key": "credits", "label": "积分",
            "sync_targets": [{"key": "quota:monthly_task"}],
            "uses": ["扣减"],
        })
        assert len(kd.sync_targets) == 1
        assert kd.sync_targets[0].key == "quota:monthly_task"
        assert kd.uses == ["扣减"]

        # regen_from_dict_roundtrip
        kd = RegenKeyDef(
            key="energy", label="能量",
            sync_targets=[SyncTargetDef(key="stock:credits")],
            uses=["导入消耗"],
        )
        restored = RegenKeyDef.from_dict(kd.to_dict())
        assert restored.sync_targets == kd.sync_targets
        assert restored.uses == kd.uses
