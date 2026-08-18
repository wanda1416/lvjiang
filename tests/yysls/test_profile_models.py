"""玩家数据模型定义测试

覆盖 profile_models.py 的数据类序列化/反序列化、parse_key_def 分发。
"""

import pytest

from lvjiang.apps.yysls.config.profile_models import (
    ALL_MODELS,
    DIR_BOTH,
    DIR_NEG,
    DIR_POS,
    MODEL_CLASSES,
    MODEL_QUOTA,
    MODEL_REGEN,
    MODEL_STOCK,
    VALID_PERIODS,
    KeyDef,
    QuotaKeyDef,
    RegenKeyDef,
    StepDef,
    StockKeyDef,
    SyncTargetDef,
    format_sync_label,
    parse_key_def,
    parse_steps,
    parse_sync_key,
    parse_sync_targets,
)

# ─── KeyDef 基类 ─────────────────────────────────────────────


class TestKeyDef:
    def test_defaults(self):
        kd = KeyDef()
        assert kd.key == ""
        assert kd.label == ""
        assert kd.description == ""
        assert kd.sources == []
        assert kd.uses == []

    def test_from_dict(self):
        kd = KeyDef.from_dict({
            "key": "test_key",
            "label": "测试",
            "description": "描述",
            "sources": ["打本", " 商店 ", ""],
            "uses": ["兑换", " 强化 ", ""],
        })
        assert kd.key == "test_key"
        assert kd.label == "测试"
        assert kd.description == "描述"
        # 词表去空白、过滤空项
        assert kd.sources == ["打本", "商店"]
        assert kd.uses == ["兑换", "强化"]

    def test_from_dict_missing_fields(self):
        kd = KeyDef.from_dict({})
        assert kd.key == ""
        assert kd.label == ""

    def test_to_dict_only_non_default(self):
        kd = KeyDef(key="k", label="l")
        d = kd.to_dict()
        assert d == {"key": "k", "label": "l"}
        # description="" 是默认值，不输出
        assert "description" not in d


# ─── QuotaKeyDef ─────────────────────────────────────────────


class TestQuotaKeyDef:
    def test_defaults(self):
        kd = QuotaKeyDef()
        assert kd.period == "week"
        assert kd.cap is None
        assert kd.steps == []
        assert kd.sync_targets == []
        assert kd.reset_time == "05:00"

    def test_from_dict(self):
        kd = QuotaKeyDef.from_dict({
            "key": "niaoniao",
            "label": "袅袅",
            "period": "week",
            "cap": 1000,
            "reset_time": "05:00",
        })
        assert kd.key == "niaoniao"
        assert kd.period == "week"
        assert kd.cap == 1000
        assert kd.reset_time == "05:00"

    def test_from_dict_defaults(self):
        kd = QuotaKeyDef.from_dict({"key": "k", "label": "l"})
        assert kd.period == "week"
        assert kd.cap is None
        assert kd.steps == []
        assert kd.sync_targets == []
        assert kd.reset_time == "05:00"

    def test_from_dict_with_steps_and_sync(self):
        kd = QuotaKeyDef.from_dict({
            "key": "zhige",
            "label": "止戈",
            "cap": 4200,
            "steps": [100, 500],
            "sync_targets": [{"key": "stock:zhige_balance", "ratio": 1.0}],
        })
        assert kd.steps == [StepDef(100), StepDef(500)]
        assert len(kd.sync_targets) == 1
        assert kd.sync_targets[0].key == "stock:zhige_balance"

    def test_from_dict_steps_dict_format_with_source(self):
        """新格式 dict 条目携带来源"""
        kd = QuotaKeyDef.from_dict({
            "key": "tili_qu", "label": "体力", "cap": 100,
            "steps": [
                {"value": -900, "source": "打本消耗"},
                -1100,
            ],
            "sync_targets": [{"key": "stock:res", "ratio": 1.0, "source": "打本掉落"}],
            "sources": ["打本", "商店"],
            "uses": ["打本消耗"],
        })
        assert kd.steps == [StepDef(-900, "打本消耗"), StepDef(-1100)]
        assert kd.sync_targets[0].source == "打本掉落"
        assert kd.sources == ["打本", "商店"]
        assert kd.uses == ["打本消耗"]

    def test_from_dict_steps_non_list(self):
        """steps 非 list 时应默认为空列表"""
        kd = QuotaKeyDef.from_dict({"key": "k", "label": "l", "steps": "invalid"})
        assert kd.steps == []

    def test_to_dict(self):
        kd = QuotaKeyDef(key="k", label="l", period="month", cap=500)
        d = kd.to_dict()
        assert d["period"] == "month"
        assert d["cap"] == 500

    def test_to_dict_with_steps_and_sync(self):
        kd = QuotaKeyDef(
            key="k", label="l", cap=100,
            steps=[StepDef(1), StepDef(10)],
            sync_targets=[SyncTargetDef(key="stock:res")],
        )
        d = kd.to_dict()
        # 无 source 的 StepDef 序列化退回纯 int
        assert d["steps"] == [1, 10]
        assert d["sync_targets"] == [{"key": "stock:res"}]

    def test_to_dict_steps_with_source(self):
        """有 source 的 StepDef 序列化为 dict"""
        kd = QuotaKeyDef(
            key="k", label="l",
            steps=[StepDef(-900, "打本消耗"), StepDef(-1100)],
            sync_targets=[SyncTargetDef(key="stock:res", source="同步来源")],
        )
        d = kd.to_dict()
        assert d["steps"] == [{"value": -900, "source": "打本消耗"}, -1100]
        assert d["sync_targets"] == [{"key": "stock:res", "source": "同步来源"}]

    def test_to_dict_default_steps_not_output(self):
        """steps=[] 是默认值，不输出"""
        kd = QuotaKeyDef(key="k", label="l")
        d = kd.to_dict()
        assert "steps" not in d
        assert "sync_targets" not in d
        assert "sources" not in d
        assert "uses" not in d

    def test_to_dict_with_uses(self):
        """非空 uses 正常序列化"""
        kd = QuotaKeyDef(key="k", label="l", uses=["兑换", "强化"])
        d = kd.to_dict()
        assert d["uses"] == ["兑换", "强化"]


# ─── RegenKeyDef ──────────────────────────────────────────


class TestRegenKeyDef:
    def test_defaults(self):
        kd = RegenKeyDef()
        assert kd.cap is None
        assert kd.regen_type == "realtime"
        assert kd.regen_rate_unit == "minute"
        assert kd.regen_rate_value == 0.0
        assert kd.regen_period == "day"
        assert kd.regen_amount == 0.0
        assert kd.alert_orange is None
        assert kd.alert_red is None

    def test_from_dict(self):
        kd = RegenKeyDef.from_dict({
            "key": "tili",
            "label": "体力",
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

    def test_to_dict(self):
        kd = RegenKeyDef(
            key="tili",
            label="体力",
            cap=2500,
            regen_type="boundary",
            regen_period="day",
            regen_amount=450,
        )
        d = kd.to_dict()
        assert d["cap"] == 2500
        assert d["regen_type"] == "boundary"
        assert "regen_period" not in d
        assert d["regen_amount"] == 450
        # 默认值不输出
        assert "alert_orange" not in d
        assert "alert_red" not in d
        # 非默认值应输出
        kd3 = RegenKeyDef(key="tili", label="体力", alert_orange=2000, alert_red=2300)
        d3 = kd3.to_dict()
        assert d3["alert_orange"] == 2000
        assert d3["alert_red"] == 2300
        # realtime/minute 是默认类型和速率单位，不输出
        kd2 = RegenKeyDef(key="xinli", label="心力", regen_rate_value=0.125)
        d2 = kd2.to_dict()
        assert "regen_type" not in d2
        assert "regen_rate_unit" not in d2
        assert d2["regen_rate_value"] == 0.125


# ─── StockKeyDef ──────────────────────────────────────────


class TestStockKeyDef:
    def test_from_dict(self):
        kd = StockKeyDef.from_dict({
            "key": "baoqian",
            "label": "宝钱",
        })
        assert kd.key == "baoqian"
        assert kd.label == "宝钱"

    def test_to_dict_minimal(self):
        kd = StockKeyDef(key="k", label="l")
        d = kd.to_dict()
        assert d == {"key": "k", "label": "l"}


# ─── parse_key_def ───────────────────────────────────────────


class TestParseKeyDef:
    def test_quota(self):
        kd = parse_key_def("quota", {"key": "k", "label": "l", "period": "week"})
        assert isinstance(kd, QuotaKeyDef)
        assert kd.period == "week"

    def test_regen(self):
        kd = parse_key_def("regen", {"key": "k", "label": "l", "cap": 2500})
        assert isinstance(kd, RegenKeyDef)
        assert kd.cap == 2500

    def test_stock(self):
        kd = parse_key_def("stock", {"key": "k", "label": "l"})
        assert isinstance(kd, StockKeyDef)

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError, match="未知模型类型"):
            parse_key_def("unknown", {"key": "k"})


# ─── 常量 ────────────────────────────────────────────────────


class TestStepDef:
    @pytest.mark.parametrize("raw,expected_value,expected_source", [
        (100, 100, ""),
        ({"value": -900, "source": " 打本消耗 "}, -900, "打本消耗"),
    ])
    def test_from_raw(self, raw, expected_value, expected_source):
        s = StepDef.from_raw(raw)
        assert s.value == expected_value
        assert s.source == expected_source

    @pytest.mark.parametrize("step,expected", [
        (StepDef(10), 10),
        (StepDef(-900, "打本"), {"value": -900, "source": "打本"}),
    ])
    def test_to_dict(self, step, expected):
        assert step.to_dict() == expected

    def test_parse_steps_mixed(self):
        steps = parse_steps([1, {"value": 2, "source": "商店"}])
        assert steps == [StepDef(1), StepDef(2, "商店")]

    @pytest.mark.parametrize("input", ["invalid", None])
    def test_parse_steps_non_list(self, input):
        assert parse_steps(input) == []


class TestConstants:
    def test_all_models(self):
        assert len(ALL_MODELS) == 3
        assert MODEL_QUOTA in ALL_MODELS
        assert MODEL_REGEN in ALL_MODELS
        assert MODEL_STOCK in ALL_MODELS

    def test_model_classes(self):
        assert MODEL_CLASSES[MODEL_QUOTA] is QuotaKeyDef
        assert MODEL_CLASSES[MODEL_REGEN] is RegenKeyDef
        assert MODEL_CLASSES[MODEL_STOCK] is StockKeyDef

    def test_valid_periods(self):
        assert "day" in VALID_PERIODS
        assert "week" in VALID_PERIODS
        assert "month" in VALID_PERIODS
        assert "season" in VALID_PERIODS
        assert "half_season" in VALID_PERIODS


# ─── SyncTargetDef ───────────────────────────────────────────


class TestSyncTargetDef:
    def test_from_raw_dict(self):
        t = SyncTargetDef.from_raw({"key": "stock:bugan", "ratio": 2.0, "source": "打本"})
        assert t.key == "stock:bugan"
        assert t.ratio == 2.0
        assert t.source == "打本"

    def test_from_raw_str(self):
        t = SyncTargetDef.from_raw("stock:bugan")
        assert t.key == "stock:bugan"
        assert t.ratio == 1.0
        assert t.source == ""

    def test_from_raw_instance(self):
        orig = SyncTargetDef(key="stock:x", ratio=0.5)
        t = SyncTargetDef.from_raw(orig)
        assert t is orig

    @pytest.mark.parametrize("kwargs,expected", [
        ({"key": "stock:bugan"}, {"key": "stock:bugan"}),
        ({"key": "stock:bugan", "ratio": 2.0}, {"key": "stock:bugan", "ratio": 2.0}),
        ({"key": "stock:bugan", "ratio": 1.0, "source": "打本"}, {"key": "stock:bugan", "source": "打本"}),
    ])
    def test_to_dict(self, kwargs, expected):
        assert SyncTargetDef(**kwargs).to_dict() == expected

    def test_roundtrip(self):
        orig = SyncTargetDef(key="stock:bugan", ratio=-1.0, source="副本")
        d = orig.to_dict()
        restored = SyncTargetDef.from_raw(d)
        assert restored.key == orig.key
        assert restored.ratio == orig.ratio
        assert restored.source == orig.source

    @pytest.mark.parametrize("raw,expected_dir", [
        ({"key": "stock:x", "direction": "neg"}, DIR_NEG),
        ({"key": "stock:x"}, DIR_BOTH),
    ])
    def test_from_raw_direction(self, raw, expected_dir):
        assert SyncTargetDef.from_raw(raw).direction == expected_dir

    def test_from_raw_direction_invalid_raises(self):
        with pytest.raises(ValueError, match="无效的同步方向"):
            SyncTargetDef.from_raw({"key": "stock:x", "direction": "sideways"})

    @pytest.mark.parametrize("kwargs,has_direction", [
        ({"key": "stock:x", "direction": DIR_POS}, True),
        ({"key": "stock:x", "direction": DIR_BOTH}, False),
    ])
    def test_to_dict_direction(self, kwargs, has_direction):
        d = SyncTargetDef(**kwargs).to_dict()
        if has_direction:
            assert "direction" in d
        else:
            assert "direction" not in d

    def test_roundtrip_with_direction(self):
        orig = SyncTargetDef(key="stock:x", ratio=-1.0, direction=DIR_NEG, source="兑换")
        restored = SyncTargetDef.from_raw(orig.to_dict())
        assert restored == orig


# ─── parse_sync_key ─────────────────────────────────────────


class TestParseSyncKey:
    @pytest.mark.parametrize("input,expected", [
        ("stock:bugan", ("stock", "bugan")),
        ("bugan", ("", "bugan")),
        ("", ("", "")),
        (None, ("", "")),
        ("  stock : bugan  ", ("stock", "bugan")),
    ])
    def test_parse_sync_key(self, input, expected):
        assert parse_sync_key(input) == expected


# ─── parse_sync_targets ─────────────────────────────────────


class TestParseSyncTargets:
    def test_list_of_dicts(self):
        targets = parse_sync_targets([
            {"key": "stock:a", "ratio": 1.0},
            {"key": "stock:b", "ratio": -1.0},
        ])
        assert len(targets) == 2
        assert targets[0].key == "stock:a"
        assert targets[1].ratio == -1.0

    def test_list_of_strings(self):
        targets = parse_sync_targets(["stock:a", "stock:b"])
        assert len(targets) == 2
        assert targets[0].key == "stock:a"

    @pytest.mark.parametrize("input", ["invalid", None])
    def test_non_list_returns_empty(self, input):
        assert parse_sync_targets(input) == []

    def test_filters_empty(self):
        targets = parse_sync_targets([None, "", {"key": "stock:a"}])
        assert len(targets) == 1


# ─── format_sync_label ──────────────────────────────────────


class TestFormatSyncLabel:
    def test_unknown_key_returns_raw(self):
        """解析失败降级到原始值"""
        assert format_sync_label("nonexistent_xyz:key_abc") == "nonexistent_xyz:key_abc"

    def test_unknown_bare_key_returns_raw(self):
        """裸 key 不存在时降级到原始值"""
        assert format_sync_label("totally_nonexistent_key_xyz") == "totally_nonexistent_key_xyz"

    def test_positive_rendering(self, monkeypatch):
        """正向渲染：命名空间 key → '模型标签：字段标签'（中文冒号）"""
        from lvjiang.apps.yysls.config.user_profile import ProfileSchema

        schema = ProfileSchema(keys_by_model={
            MODEL_QUOTA: [],
            MODEL_REGEN: [],
            MODEL_STOCK: [StockKeyDef(key="bugan", label="不肝")],
        })
        monkeypatch.setattr(
            "lvjiang.apps.yysls.config.get_profile_config",
            lambda: schema,
        )
        assert format_sync_label("stock:bugan") == "库存：不肝"


# ─── KeyDef sync_targets ───────────────────────────────────


class TestKeyDefSyncTargets:
    def test_no_sync(self):
        kd = KeyDef.from_dict({"key": "k", "label": "l"})
        assert kd.sync_targets == []

    def test_to_dict_sync_targets(self):
        kd = KeyDef(
            key="k", label="l",
            sync_targets=[SyncTargetDef(key="stock:bugan")],
        )
        d = kd.to_dict()
        assert d["sync_targets"] == [{"key": "stock:bugan"}]

    def test_to_dict_empty_sync_targets_not_output(self):
        kd = KeyDef(key="k", label="l")
        d = kd.to_dict()
        assert "sync_targets" not in d

    def test_regen_from_dict_parses_sync_targets(self):
        """Regen 模型 from_dict 必须解析 sync_targets/uses（回归）"""
        kd = RegenKeyDef.from_dict({
            "key": "tili", "label": "体力",
            "sync_targets": [{"key": "stock:bugan", "ratio": 0.5}],
            "uses": ["打本消耗"],
        })
        assert len(kd.sync_targets) == 1
        assert kd.sync_targets[0].key == "stock:bugan"
        assert kd.sync_targets[0].ratio == 0.5
        assert kd.uses == ["打本消耗"]

    def test_stock_from_dict_parses_sync_targets(self):
        """Stock 模型 from_dict 必须解析 sync_targets/uses（回归）"""
        kd = StockKeyDef.from_dict({
            "key": "bugan", "label": "不肝",
            "sync_targets": [{"key": "quota:dihua"}],
            "uses": ["强化"],
        })
        assert len(kd.sync_targets) == 1
        assert kd.sync_targets[0].key == "quota:dihua"
        assert kd.uses == ["强化"]

    def test_regen_from_dict_roundtrip(self):
        """Regen to_dict → from_dict 往返不丢 sync_targets/uses"""
        kd = RegenKeyDef(
            key="tili", label="体力",
            sync_targets=[SyncTargetDef(key="stock:bugan")],
            uses=["打本消耗"],
        )
        restored = RegenKeyDef.from_dict(kd.to_dict())
        assert restored.sync_targets == kd.sync_targets
        assert restored.uses == kd.uses
