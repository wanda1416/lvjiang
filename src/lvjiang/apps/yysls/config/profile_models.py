"""玩家数据模型定义

四种游戏数据模型的数据类：
- daily: 周期任务/限额，周期结束自动清零
- realtime: 实时状态，按规则回复，有上限
- resource: 资源计数，纯数字
- activity: 活动进度，与 daily 结构一致，周期结束自动清零

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
    show_cap: 是否在总览中展示上限（value/cap），默认否。
    """

    key: str = ""
    label: str = ""
    description: str = ""
    show_cap: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KeyDef:
        return cls(
            key=data.get("key", ""),
            label=data.get("label", ""),
            description=data.get("description", ""),
            show_cap=data.get("show_cap", False),
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

    reset_day:
        week 周期: 1=周一 ... 7=周日（0 或未设置 → 默认周一）
        month 周期: 1-31 表示每月第几天（0 或未设置 → 默认 1 号）
    """

    period: str = "week"
    cap: int | None = None
    reset_time: str = "05:00"
    reset_day: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DailyKeyDef:
        return cls(
            key=data.get("key", ""),
            label=data.get("label", ""),
            description=data.get("description", ""),
            show_cap=data.get("show_cap", False),
            period=data.get("period", "week"),
            cap=data.get("cap"),
            reset_time=data.get("reset_time", "05:00"),
            reset_day=data.get("reset_day", 0),
        )


@dataclass
class RealtimeKeyDef(KeyDef):
    """实时数据模型 — 实时状态

    按回复周期和回复数值计算回复，有上限。
    regen_period: 回复周期单位 ("minute" | "hour" | "day")
    regen_value: 每个周期的回复量（允许小数）
    reset_time: 仅 day 周期使用，指定每日重置时刻
    """

    cap: int | None = None
    regen_period: str = "minute"
    regen_value: float = 0.0
    reset_time: str = "05:00"
    alert_above: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RealtimeKeyDef:
        return cls(
            key=data.get("key", ""),
            label=data.get("label", ""),
            description=data.get("description", ""),
            show_cap=data.get("show_cap", False),
            cap=data.get("cap"),
            regen_period=data.get("regen_period", "minute"),
            regen_value=data.get("regen_value", 0.0),
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
            description=data.get("description", ""),
            show_cap=data.get("show_cap", False),
        )


@dataclass
class ActivityKeyDef(KeyDef):
    """活动数据模型 — 活动进度

    与 DailyKeyDef 结构一致：周期结束自动清零。
    value 是当期原始值，user.json 中的 total 记录历史累积（引擎自动累加，无上限）。

    reset_day:
        week 周期: 1=周一 ... 7=周日（0 或未设置 → 默认周一）
        month 周期: 1-31 表示每月第几天（0 或未设置 → 默认 1 号）
    """

    period: str = "week"
    cap: int | None = None
    reset_time: str = "05:00"
    reset_day: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActivityKeyDef:
        return cls(
            key=data.get("key", ""),
            label=data.get("label", ""),
            description=data.get("description", ""),
            show_cap=data.get("show_cap", False),
            period=data.get("period", "week"),
            cap=data.get("cap"),
            reset_time=data.get("reset_time", "05:00"),
            reset_day=data.get("reset_day", 0),
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
