"""玩家数据模型定义

三种游戏数据模型的数据类：
- daily: 周期任务/限额，周期结束自动清零，可同步到 resource
- realtime: 实时状态，按规则回复，有上限
- resource: 资源计数，纯数字，无颜色告警

定义与存储完全镜像：profile.yaml 按模型归档，user.json 按模型分节点。
"""

from __future__ import annotations

from dataclasses import MISSING, dataclass, field, fields
from typing import Any

# 合法周期值
VALID_PERIODS = ("day", "week", "month", "season", "half_season")

# 模型类型常量
MODEL_DAILY = "daily"
MODEL_REALTIME = "realtime"
MODEL_RESOURCE = "resource"

ALL_MODELS = (MODEL_DAILY, MODEL_REALTIME, MODEL_RESOURCE)

MODEL_LABELS = {
    MODEL_DAILY: "日常",
    MODEL_REALTIME: "实时",
    MODEL_RESOURCE: "资源",
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
            # 处理 default 和 default_factory
            if f.default is not MISSING:
                if val == f.default:
                    continue
            elif f.default_factory is not MISSING:
                if val == f.default_factory():
                    continue
            result[f.name] = val
        return result


@dataclass
class DailyKeyDef(KeyDef):
    """日常数据模型 — 周期任务/限额

    周期结束自动清零，无累积概念。
    可通过 sync_to 单向同步到 Resource 模型（仅 steps 动作触发）。

    reset_day:
        week 周期: 1=周一 ... 7=周日（0 或未设置 → 默认周一）
        month 周期: 1-31 表示每月第几天（0 或未设置 → 默认 1 号）
    steps:
        自定义增减幅度，正值=增加，负值=减少。
        例如 [100, 500] 表示右键菜单显示 +100 和 +500。
        例如 [-1] 表示只显示 -1。
    sync_to:
        同步目标 Resource key。当通过 steps 修改 Daily 值时，
        同步 delta 到该 Resource key 的值。手动编辑不触发同步。
    increment_only:
        单向增加模式。勾选后右键菜单只显示「增加...」，不提供减少功能。
    """

    period: str = "week"
    cap: int | None = None
    soft: bool = False
    steps: list[int] = field(default_factory=list)
    sync_to: str = ""
    reset_time: str = "05:00"
    reset_day: int = 0
    increment_only: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DailyKeyDef:
        steps_raw = data.get("steps", [])
        if isinstance(steps_raw, list):
            steps = [int(s) for s in steps_raw]
        else:
            steps = []
        return cls(
            key=data.get("key", ""),
            label=data.get("label", ""),
            description=data.get("description", ""),
            show_cap=data.get("show_cap", False),
            period=data.get("period", "week"),
            cap=data.get("cap"),
            soft=data.get("soft", False),
            steps=steps,
            sync_to=data.get("sync_to", ""),
            reset_time=data.get("reset_time", "05:00"),
            reset_day=data.get("reset_day", 0),
            increment_only=data.get("increment_only", False),
        )


@dataclass
class RealtimeKeyDef(KeyDef):
    """实时数据模型 — 实时状态

    按回复周期和回复数值计算回复，有上限。
    regen_period: 回复周期单位 ("minute" | "hour" | "day" | "week")
    regen_value: 每个周期的回复量（允许小数）
    reset_time: day/week 周期使用，指定重置时刻
    reset_day: week 周期使用，1=周一 ... 7=周日（0=默认周一）
    steps: 自定义增减幅度，正值=增加，负值=减少。
    """

    cap: int | None = None
    regen_period: str = "minute"
    regen_value: float = 0.0
    reset_time: str = "05:00"
    reset_day: int = 0
    alert_above: int | None = None
    steps: list[int] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RealtimeKeyDef:
        steps_raw = data.get("steps", [])
        if isinstance(steps_raw, list):
            steps = [int(s) for s in steps_raw]
        else:
            steps = []
        return cls(
            key=data.get("key", ""),
            label=data.get("label", ""),
            description=data.get("description", ""),
            show_cap=data.get("show_cap", False),
            cap=data.get("cap"),
            regen_period=data.get("regen_period", "minute"),
            regen_value=data.get("regen_value", 0.0),
            reset_time=data.get("reset_time", "05:00"),
            reset_day=data.get("reset_day", 0),
            alert_above=data.get("alert_above"),
            steps=steps,
        )


@dataclass
class ResourceKeyDef(KeyDef):
    """资源数据模型 — 资源计数

    纯计数器，可设上限（软/硬）。
    soft=True 时上限为软上限（仅提醒），soft=False 时为硬上限。
    """

    cap: int | None = None
    soft: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceKeyDef:
        return cls(
            key=data.get("key", ""),
            label=data.get("label", ""),
            description=data.get("description", ""),
            show_cap=data.get("show_cap", False),
            cap=data.get("cap"),
            soft=data.get("soft", False),
        )


# ─── 模型类型 -> 数据类映射 ─────────────────────────────────

MODEL_CLASSES: dict[str, type[KeyDef]] = {
    MODEL_DAILY: DailyKeyDef,
    MODEL_REALTIME: RealtimeKeyDef,
    MODEL_RESOURCE: ResourceKeyDef,
}


def parse_key_def(model_type: str, data: dict[str, Any]) -> KeyDef:
    """根据模型类型分发到对应数据类"""
    cls = MODEL_CLASSES.get(model_type)
    if cls is None:
        raise ValueError(f"未知模型类型: {model_type}")
    return cls.from_dict(data)
