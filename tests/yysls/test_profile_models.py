"""玩家数据模型定义测试

覆盖 profile_models.py 的数据类序列化/反序列化、parse_key_def 分发。
"""

import pytest

from lvjiang.apps.yysls.config.profile_models import (
    ALL_MODELS,
    MODEL_ACTIVITY,
    MODEL_CLASSES,
    MODEL_DAILY,
    MODEL_REALTIME,
    MODEL_RESOURCE,
    VALID_PERIODS,
    ActivityKeyDef,
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
        assert kd.source == ""
        assert kd.description == ""

    def test_from_dict(self):
        kd = KeyDef.from_dict({
            "key": "test_key",
            "label": "测试",
            "source": "api.path",
            "description": "描述",
        })
        assert kd.key == "test_key"
        assert kd.label == "测试"
        assert kd.source == "api.path"
        assert kd.description == "描述"

    def test_from_dict_missing_fields(self):
        kd = KeyDef.from_dict({})
        assert kd.key == ""
        assert kd.label == ""

    def test_to_dict_only_non_default(self):
        kd = KeyDef(key="k", label="l")
        d = kd.to_dict()
        assert d == {"key": "k", "label": "l"}
        # source="" 和 description="" 是默认值，不输出
        assert "source" not in d
        assert "description" not in d

    def test_to_dict_with_source(self):
        kd = KeyDef(key="k", label="l", source="api.path")
        d = kd.to_dict()
        assert d["source"] == "api.path"


# ─── DailyKeyDef ─────────────────────────────────────────────


class TestDailyKeyDef:
    def test_defaults(self):
        kd = DailyKeyDef()
        assert kd.period == "week"
        assert kd.cap is None
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
        assert kd.reset_time == "05:00"

    def test_to_dict(self):
        kd = DailyKeyDef(key="k", label="l", period="month", cap=500)
        d = kd.to_dict()
        assert d["period"] == "month"
        assert d["cap"] == 500


# ─── RealtimeKeyDef ──────────────────────────────────────────


class TestRealtimeKeyDef:
    def test_defaults(self):
        kd = RealtimeKeyDef()
        assert kd.cap is None
        assert kd.regen_rate == 0.0
        assert kd.regen_daily == 0
        assert kd.alert_above is None

    def test_from_dict(self):
        kd = RealtimeKeyDef.from_dict({
            "key": "tili",
            "label": "体力",
            "cap": 2500,
            "regen_rate": 0.125,
            "regen_daily": 450,
            "alert_above": 2150,
        })
        assert kd.cap == 2500
        assert kd.regen_rate == 0.125
        assert kd.regen_daily == 450
        assert kd.alert_above == 2150

    def test_to_dict(self):
        kd = RealtimeKeyDef(key="tili", label="体力", cap=2500, regen_rate=0.125)
        d = kd.to_dict()
        assert d["cap"] == 2500
        assert d["regen_rate"] == 0.125
        # regen_daily=0 是默认值，不输出
        assert "regen_daily" not in d


# ─── ResourceKeyDef ──────────────────────────────────────────


class TestResourceKeyDef:
    def test_from_dict(self):
        kd = ResourceKeyDef.from_dict({
            "key": "baoqian",
            "label": "宝钱",
            "source": "currencies.baoqian",
        })
        assert kd.key == "baoqian"
        assert kd.source == "currencies.baoqian"

    def test_to_dict_minimal(self):
        kd = ResourceKeyDef(key="k", label="l")
        d = kd.to_dict()
        assert d == {"key": "k", "label": "l"}


# ─── ActivityKeyDef ──────────────────────────────────────────


class TestActivityKeyDef:
    def test_defaults(self):
        kd = ActivityKeyDef()
        assert kd.period == "week"
        assert kd.period_cap == 0
        assert kd.lifetime_cap == 0
        assert kd.alert_near_period_cap is None
        assert kd.alert_near_lifetime_cap is None

    def test_from_dict(self):
        kd = ActivityKeyDef.from_dict({
            "key": "map",
            "label": "地图活动",
            "period": "week",
            "period_cap": 1000,
            "lifetime_cap": 10000,
            "alert_near_period_cap": 0.8,
            "alert_near_lifetime_cap": 0.9,
        })
        assert kd.period_cap == 1000
        assert kd.lifetime_cap == 10000
        assert kd.alert_near_period_cap == 0.8
        assert kd.alert_near_lifetime_cap == 0.9


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

    def test_activity(self):
        kd = parse_key_def("activity", {"key": "k", "label": "l", "period_cap": 1000})
        assert isinstance(kd, ActivityKeyDef)
        assert kd.period_cap == 1000

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError, match="未知模型类型"):
            parse_key_def("unknown", {"key": "k"})


# ─── 常量 ────────────────────────────────────────────────────


class TestConstants:
    def test_all_models(self):
        assert len(ALL_MODELS) == 4
        assert MODEL_DAILY in ALL_MODELS
        assert MODEL_REALTIME in ALL_MODELS
        assert MODEL_RESOURCE in ALL_MODELS
        assert MODEL_ACTIVITY in ALL_MODELS

    def test_model_classes(self):
        assert MODEL_CLASSES[MODEL_DAILY] is DailyKeyDef
        assert MODEL_CLASSES[MODEL_REALTIME] is RealtimeKeyDef
        assert MODEL_CLASSES[MODEL_RESOURCE] is ResourceKeyDef
        assert MODEL_CLASSES[MODEL_ACTIVITY] is ActivityKeyDef

    def test_valid_periods(self):
        assert "day" in VALID_PERIODS
        assert "week" in VALID_PERIODS
        assert "month" in VALID_PERIODS
        assert "season" in VALID_PERIODS
        assert "half_season" in VALID_PERIODS
