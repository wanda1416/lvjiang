"""玩家数据模型定义测试

覆盖 profile_models.py 的数据类序列化/反序列化、parse_key_def 分发。
"""

import pytest

from lvjiang.apps.yysls.config.profile_models import (
    ALL_MODELS,
    MODEL_CLASSES,
    MODEL_DAILY,
    MODEL_REALTIME,
    MODEL_RESOURCE,
    VALID_PERIODS,
    DailyKeyDef,
    KeyDef,
    RealtimeKeyDef,
    ResourceKeyDef,
    parse_key_def,
)

# ─── KeyDef 基类 ─────────────────────────────────────────────


class TestKeyDef:
    def test_defaults(self):
        kd = KeyDef()
        assert kd.key == ""
        assert kd.label == ""
        assert kd.description == ""

    def test_from_dict(self):
        kd = KeyDef.from_dict({
            "key": "test_key",
            "label": "测试",
            "description": "描述",
        })
        assert kd.key == "test_key"
        assert kd.label == "测试"
        assert kd.description == "描述"

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


# ─── DailyKeyDef ─────────────────────────────────────────────


class TestDailyKeyDef:
    def test_defaults(self):
        kd = DailyKeyDef()
        assert kd.period == "week"
        assert kd.cap is None
        assert kd.steps == []
        assert kd.sync_to == ""
        assert kd.reset_time == "05:00"

    def test_from_dict(self):
        kd = DailyKeyDef.from_dict({
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
        kd = DailyKeyDef.from_dict({"key": "k", "label": "l"})
        assert kd.period == "week"
        assert kd.cap is None
        assert kd.steps == []
        assert kd.sync_to == ""
        assert kd.reset_time == "05:00"

    def test_from_dict_with_steps_and_sync(self):
        kd = DailyKeyDef.from_dict({
            "key": "zhige",
            "label": "止戈",
            "cap": 4200,
            "steps": [100, 500],
            "sync_to": "zhige_balance",
        })
        assert kd.steps == [100, 500]
        assert kd.sync_to == "zhige_balance"

    def test_from_dict_steps_non_list(self):
        """steps 非 list 时应默认为空列表"""
        kd = DailyKeyDef.from_dict({"key": "k", "label": "l", "steps": "invalid"})
        assert kd.steps == []

    def test_to_dict(self):
        kd = DailyKeyDef(key="k", label="l", period="month", cap=500)
        d = kd.to_dict()
        assert d["period"] == "month"
        assert d["cap"] == 500

    def test_to_dict_with_steps_and_sync(self):
        kd = DailyKeyDef(
            key="k", label="l", cap=100,
            steps=[1, 10], sync_to="res",
        )
        d = kd.to_dict()
        assert d["steps"] == [1, 10]
        assert d["sync_to"] == "res"

    def test_to_dict_default_steps_not_output(self):
        """steps=[] 是默认值，不输出"""
        kd = DailyKeyDef(key="k", label="l")
        d = kd.to_dict()
        assert "steps" not in d
        assert "sync_to" not in d


# ─── RealtimeKeyDef ──────────────────────────────────────────


class TestRealtimeKeyDef:
    def test_defaults(self):
        kd = RealtimeKeyDef()
        assert kd.cap is None
        assert kd.regen_period == "minute"
        assert kd.regen_value == 0.0
        assert kd.alert_above is None

    def test_from_dict(self):
        kd = RealtimeKeyDef.from_dict({
            "key": "tili",
            "label": "体力",
            "cap": 2500,
            "regen_period": "day",
            "regen_value": 450,
            "alert_above": 2150,
        })
        assert kd.cap == 2500
        assert kd.regen_period == "day"
        assert kd.regen_value == 450
        assert kd.alert_above == 2150

    def test_to_dict(self):
        kd = RealtimeKeyDef(key="tili", label="体力", cap=2500, regen_period="day", regen_value=450)
        d = kd.to_dict()
        assert d["cap"] == 2500
        assert d["regen_period"] == "day"
        assert d["regen_value"] == 450
        # regen_period="minute" 是默认值，不输出
        kd2 = RealtimeKeyDef(key="xinli", label="心力", regen_value=0.125)
        d2 = kd2.to_dict()
        assert "regen_period" not in d2


# ─── ResourceKeyDef ──────────────────────────────────────────


class TestResourceKeyDef:
    def test_from_dict(self):
        kd = ResourceKeyDef.from_dict({
            "key": "baoqian",
            "label": "宝钱",
        })
        assert kd.key == "baoqian"
        assert kd.label == "宝钱"

    def test_to_dict_minimal(self):
        kd = ResourceKeyDef(key="k", label="l")
        d = kd.to_dict()
        assert d == {"key": "k", "label": "l"}


# ─── parse_key_def ───────────────────────────────────────────


class TestParseKeyDef:
    def test_daily(self):
        kd = parse_key_def("daily", {"key": "k", "label": "l", "period": "week"})
        assert isinstance(kd, DailyKeyDef)
        assert kd.period == "week"

    def test_realtime(self):
        kd = parse_key_def("realtime", {"key": "k", "label": "l", "cap": 2500})
        assert isinstance(kd, RealtimeKeyDef)
        assert kd.cap == 2500

    def test_resource(self):
        kd = parse_key_def("resource", {"key": "k", "label": "l"})
        assert isinstance(kd, ResourceKeyDef)

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError, match="未知模型类型"):
            parse_key_def("unknown", {"key": "k"})


# ─── 常量 ────────────────────────────────────────────────────


class TestConstants:
    def test_all_models(self):
        assert len(ALL_MODELS) == 3
        assert MODEL_DAILY in ALL_MODELS
        assert MODEL_REALTIME in ALL_MODELS
        assert MODEL_RESOURCE in ALL_MODELS

    def test_model_classes(self):
        assert MODEL_CLASSES[MODEL_DAILY] is DailyKeyDef
        assert MODEL_CLASSES[MODEL_REALTIME] is RealtimeKeyDef
        assert MODEL_CLASSES[MODEL_RESOURCE] is ResourceKeyDef

    def test_valid_periods(self):
        assert "day" in VALID_PERIODS
        assert "week" in VALID_PERIODS
        assert "month" in VALID_PERIODS
        assert "season" in VALID_PERIODS
        assert "half_season" in VALID_PERIODS
