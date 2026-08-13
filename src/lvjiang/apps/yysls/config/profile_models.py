"""玩家数据模型定义

四种游戏数据模型的数据类：
- daily: 周期任务/限额，周期结束自动清零
- realtime: 实时状态，按规则回复，有上限
- resource: 资源计数，纯数字
- activity: 活动进度，周期限额 + 账号总上限

定义与存储完全镜像：profile.yaml 按模型归档，user.json 按模型分节点。
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

# 合法周期值
VALID_PERIODS = ("day", "week", "month", "season", "half_season")

# 模型类型常量
MODEL_DAILY = "daily"
MODEL_REALTIME = "realtime"
MODEL_RESOURCE = "resource"
MODEL_ACTIVITY = "activity"

ALL_MODELS = (MODEL_DAILY, MODEL_REALTIME, MODEL_RESOURCE, MODEL_ACTIVITY)

MODEL_LABELS = {
    MODEL_DAILY: "日常",
    MODEL_REALTIME: "实时",
    MODEL_RESOURCE: "资源",
    MODEL_ACTIVITY: "活动",
}


# ─── 数据类 ────────────────────────────────────────────────


@dataclass
class KeyDef:
    """所有模型共有的基础字段

    不含 model 字段 — 模型类型由 profile.yaml 的父节点决定。
    """

    key: str = ""
    label: str = ""
    source: str = ""
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KeyDef:
        return cls(
            key=data.get("key", ""),
            label=data.get("label", ""),
            source=data.get("source", ""),
            description=data.get("description", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（仅输出非默认值）"""
        result: dict[str, Any] = {}
        for f in fields(self):
            val = getattr(self, f.name)
            if val == f.default:
                continue
            result[f.name] = val
        return result


@dataclass
class DailyKeyDef(KeyDef):
    """日常数据模型 — 周期任务/限额

    周期结束自动清零，无累积概念。
    """

    period: str = "week"
    cap: int | None = None
    reset_time: str = "05:00"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DailyKeyDef:
        return cls(
            key=data.get("key", ""),
            label=data.get("label", ""),
            source=data.get("source", ""),
            description=data.get("description", ""),
            period=data.get("period", "week"),
            cap=data.get("cap"),
            reset_time=data.get("reset_time", "05:00"),
        )


@dataclass
class RealtimeKeyDef(KeyDef):
    """实时数据模型 — 实时状态

    按规则回复，有上限。引擎根据 updated_at + regen_rate 计算当前值。
    """

    cap: int | None = None
    regen_rate: float = 0.0
    regen_daily: int = 0
    reset_time: str = "05:00"
    alert_above: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RealtimeKeyDef:
        return cls(
            key=data.get("key", ""),
            label=data.get("label", ""),
            source=data.get("source", ""),
            description=data.get("description", ""),
            cap=data.get("cap"),
            regen_rate=data.get("regen_rate", 0.0),
            regen_daily=data.get("regen_daily", 0),
            reset_time=data.get("reset_time", "05:00"),
            alert_above=data.get("alert_above"),
        )


@dataclass
class ResourceKeyDef(KeyDef):
    """资源数据模型 — 资源计数

    纯计数器，无上限，无回复。
    """

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceKeyDef:
        return cls(
            key=data.get("key", ""),
            label=data.get("label", ""),
            source=data.get("source", ""),
            description=data.get("description", ""),
        )


@dataclass
class ActivityKeyDef(KeyDef):
    """活动数据模型 — 活动进度

    周期限额 + 账号总上限。value 是当期原始值，total 是历史累积。
    周期重置时 total += value，然后 value = 0。
    """

    period: str = "week"
    period_cap: int = 0
    lifetime_cap: int = 0
    reset_time: str = "05:00"
    alert_near_period_cap: float | None = None
    alert_near_lifetime_cap: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActivityKeyDef:
        return cls(
            key=data.get("key", ""),
            label=data.get("label", ""),
            source=data.get("source", ""),
            description=data.get("description", ""),
            period=data.get("period", "week"),
            period_cap=data.get("period_cap", 0),
            lifetime_cap=data.get("lifetime_cap", 0),
            reset_time=data.get("reset_time", "05:00"),
            alert_near_period_cap=data.get("alert_near_period_cap"),
            alert_near_lifetime_cap=data.get("alert_near_lifetime_cap"),
        )


# ─── 模型类型 -> 数据类映射 ─────────────────────────────────

MODEL_CLASSES: dict[str, type[KeyDef]] = {
    MODEL_DAILY: DailyKeyDef,
    MODEL_REALTIME: RealtimeKeyDef,
    MODEL_RESOURCE: ResourceKeyDef,
    MODEL_ACTIVITY: ActivityKeyDef,
}


def parse_key_def(model_type: str, data: dict[str, Any]) -> KeyDef:
    """根据模型类型分发到对应数据类"""
    cls = MODEL_CLASSES.get(model_type)
    if cls is None:
        raise ValueError(f"未知模型类型: {model_type}")
    return cls.from_dict(data)
