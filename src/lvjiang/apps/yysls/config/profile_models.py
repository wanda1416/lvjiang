"""玩家数据模型定义

三种游戏数据模型的数据类：
- quota: 配额（周期任务/限额），周期结束自动清零
- regen: 再生（恢复状态），按规则回复，有上限
- stock: 存量（资源计数），纯数字，无颜色告警

任意模型的 key 可通过 sync_targets 配置触发器同步（跨模型、多目标、倍率、方向限定）。

定义与存储完全镜像：profile.yaml 按模型归档，user.json 按模型分节点。
"""

from __future__ import annotations

from dataclasses import MISSING, dataclass, field, fields
from typing import Any

# 合法周期值
VALID_PERIODS = ("day", "week", "month", "season", "half_season")

# 模型类型常量
MODEL_QUOTA = "quota"
MODEL_REGEN = "regen"
MODEL_STOCK = "stock"

ALL_MODELS = (MODEL_QUOTA, MODEL_REGEN, MODEL_STOCK)

MODEL_LABELS = {
    MODEL_QUOTA: "配额",
    MODEL_REGEN: "再生",
    MODEL_STOCK: "库存",
}

# 同步方向限定
DIR_BOTH = "both"  # 全向：任意变动触发
DIR_POS = "pos"    # 正向：仅数值增加触发
DIR_NEG = "neg"    # 负向：仅数值减少触发
VALID_DIRECTIONS = (DIR_BOTH, DIR_POS, DIR_NEG)
DIRECTION_LABELS = {DIR_BOTH: "全向", DIR_POS: "正向", DIR_NEG: "负向"}


# ─── 数据类 ────────────────────────────────────────────────


@dataclass
class SyncTargetDef:
    """同步目标定义

    key 采用 `model_type:key` 命名空间（如 `stock:bugan`），跨模型无歧义。
    ratio 允许负数 / 小数：-1 表示反向同步，0.5 表示减半，0 表示禁用该目标。
    direction 方向限定：both=全向（任意变动触发），pos=正向（仅增加触发），
    neg=负向（仅减少触发）。
    source 可选：覆盖默认来源描述（未填时沿用触发 action 的 source）。
    """

    key: str = ""
    ratio: float = 1.0
    direction: str = DIR_BOTH
    source: str = ""

    @classmethod
    def from_raw(cls, raw) -> "SyncTargetDef":
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, dict):
            direction = str(raw.get("direction", DIR_BOTH)).strip()
            if direction not in VALID_DIRECTIONS:
                raise ValueError(f"无效的同步方向: {direction!r}，应为 {VALID_DIRECTIONS}")
            return cls(
                key=str(raw.get("key", "")).strip(),
                ratio=float(raw.get("ratio", 1.0)),
                direction=direction,
                source=str(raw.get("source", "")).strip(),
            )
        # 兼容裸字符串（隐式 1:1 倍率）
        return cls(key=str(raw).strip())

    def to_dict(self) -> dict[str, Any]:
        """序列化：仅输出非默认字段"""
        result: dict[str, Any] = {"key": self.key}
        if self.ratio != 1.0:
            result["ratio"] = self.ratio
        if self.direction != DIR_BOTH:
            result["direction"] = self.direction
        if self.source:
            result["source"] = self.source
        return result


def parse_sync_targets(raw) -> list[SyncTargetDef]:
    """解析 sync_targets 配置（dict / str / SyncTargetDef 混用列表）"""
    if not isinstance(raw, list):
        return []
    return [SyncTargetDef.from_raw(s) for s in raw if s]


def parse_sync_key(raw: str) -> tuple[str, str]:
    """解析命名空间 key

    'stock:bugan' → ('stock', 'bugan')
    裸 'bugan'   → ('', 'bugan')（隐式模型，由调用方按上下文推断）
    """
    raw = (raw or "").strip()
    if ":" in raw:
        ns, _, key = raw.partition(":")
        return ns.strip(), key.strip()
    return "", raw


def format_sync_label(sync_key: str) -> str:
    """将命名空间 key 渲染为人话标签（UI 用，中文冒号）

    'stock:bugan' → '库存：不肝'
    'quota:dihua' → '配额：地花'
    解析失败降级到原始 ID。
    """
    model_type, key = parse_sync_key(sync_key)
    # 延迟导入避免循环：profile_config 依赖 profile_models
    try:
        from . import get_profile_config
        kd = get_profile_config().get_key(key, model_type=model_type or None)
    except Exception:
        kd = None
    if kd is None:
        return sync_key
    model_label = MODEL_LABELS.get(model_type, model_type or "")
    if model_label:
        return f"{model_label}：{kd.label}"
    return kd.label


@dataclass
class StepDef:
    """快捷增减幅度条目，携带来源描述

    旧格式 int 兼容：仅幅度，source 为空。
    新格式 dict：{value: 幅度, source: 来源描述}。
    """

    value: int = 0
    source: str = ""

    @classmethod
    def from_raw(cls, raw) -> "StepDef":
        if isinstance(raw, dict):
            return cls(
                value=int(raw.get("value", 0)),
                source=str(raw.get("source", "")).strip(),
            )
        return cls(value=int(raw))

    def to_dict(self):
        """序列化：无 source 时退回纯 int，保持 YAML 简洁"""
        if not self.source:
            return self.value
        return {"value": self.value, "source": self.source}


def parse_steps(raw) -> list[StepDef]:
    """解析 steps 配置（int / dict 混用列表）"""
    if not isinstance(raw, list):
        return []
    return [StepDef.from_raw(s) for s in raw]


@dataclass
class KeyDef:
    """所有模型共有的基础字段

    不含 model 字段 — 模型类型由 profile.yaml 的父节点决定。
    cap: 上限值，None 表示无上限。
    soft: True 表示软上限（仅提醒），False 表示硬上限（强制截断）。
    show_cap: 是否在总览中展示上限（value/cap），默认否。
    sources: 来源词表，增加操作时供下拉选择。
    uses: 用途词表，减少操作时供下拉选择。
    sync_targets: 触发器同步目标列表，任意模型类型的 action 动作都会触发；
        每个目标独立倍率（允许负数 / 小数），key 采用 `model_type:key` 命名空间。
    """

    key: str = ""
    label: str = ""
    description: str = ""
    cap: int | None = None
    soft: bool = False
    show_cap: bool = False
    sources: list[str] = field(default_factory=list)
    uses: list[str] = field(default_factory=list)
    sync_targets: list[SyncTargetDef] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KeyDef:
        return cls(
            key=data.get("key", ""),
            label=data.get("label", ""),
            description=data.get("description", ""),
            cap=data.get("cap"),
            soft=data.get("soft", False),
            show_cap=data.get("show_cap", False),
            sources=[str(s).strip() for s in data.get("sources", []) if str(s).strip()],
            uses=[str(s).strip() for s in data.get("uses", []) if str(s).strip()],
            sync_targets=parse_sync_targets(data.get("sync_targets", [])),
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
            # 子 dataclass 列表（StepDef / SyncTargetDef）统一序列化
            if isinstance(val, list) and val and hasattr(val[0], "to_dict"):
                result[f.name] = [v.to_dict() for v in val]
            else:
                result[f.name] = val
        return result


@dataclass
class QuotaKeyDef(KeyDef):
    """配额数据模型 — 周期任务/限额

    周期结束自动清零，无累积概念。
    通过 sync_targets（继承自 KeyDef）配置触发器同步，任意 action 动作都会触发。

    reset_day:
        week 周期: 1=周一 ... 7=周日（0 或未设置 → 默认周一）
        month 周期: 1-31 表示每月第几天（0 或未设置 → 默认 1 号）
    steps:
        自定义增减幅度，正值=增加，负值=减少，每条可携带来源描述。
        例如 [{value: 100, source: 商店}] 右键菜单显示「商店(+100)」。
        纯 int 条目兼容旧格式，菜单标签退回 ±N。
    increment_only:
        单向增加模式。勾选后右键菜单只显示「增加...」，不提供减少功能。
    """

    period: str = "week"
    steps: list[StepDef] = field(default_factory=list)
    reset_time: str = "05:00"
    reset_day: int = 0
    increment_only: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuotaKeyDef:
        base = KeyDef.from_dict(data)
        return cls(
            key=base.key,
            label=base.label,
            description=base.description,
            cap=base.cap,
            soft=base.soft,
            show_cap=base.show_cap,
            sources=base.sources,
            uses=base.uses,
            sync_targets=base.sync_targets,
            period=data.get("period", "week"),
            steps=parse_steps(data.get("steps", [])),
            reset_time=data.get("reset_time", "05:00"),
            reset_day=data.get("reset_day", 0),
            increment_only=data.get("increment_only", False),
        )


@dataclass
class RegenKeyDef(KeyDef):
    """再生数据模型 — 恢复状态

    按回复周期和回复数值计算回复，有上限。
    regen_period: 回复周期单位 ("minute" | "hour" | "day" | "week")
    regen_value: 每个周期的回复量（允许小数）
    reset_time: day/week 周期使用，指定重置时刻
    reset_day: week 周期使用，1=周一 ... 7=周日（0=默认周一）
    steps: 自定义增减幅度，正值=增加，负值=减少，可携带来源描述。
    """

    regen_period: str = "minute"
    regen_value: float = 0.0
    reset_time: str = "05:00"
    reset_day: int = 0
    alert_orange: int | None = None  # 橙色告警阈值（低）
    alert_red: int | None = None     # 红色告警阈值（高）
    steps: list[StepDef] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegenKeyDef:
        base = KeyDef.from_dict(data)
        return cls(
            key=base.key,
            label=base.label,
            description=base.description,
            cap=base.cap,
            soft=base.soft,
            show_cap=base.show_cap,
            sources=base.sources,
            uses=base.uses,
            sync_targets=base.sync_targets,
            regen_period=data.get("regen_period", "minute"),
            regen_value=data.get("regen_value", 0.0),
            reset_time=data.get("reset_time", "05:00"),
            reset_day=data.get("reset_day", 0),
            alert_orange=data.get("alert_orange"),
            alert_red=data.get("alert_red"),
            steps=parse_steps(data.get("steps", [])),
        )


@dataclass
class StockKeyDef(KeyDef):
    """存量数据模型 — 资源计数

    纯计数器，可设上限（软/硬）。
    soft=True 时上限为软上限（仅提醒），soft=False 时为硬上限。
    steps: 自定义增减幅度，正值=增加，负值=减少，可携带来源描述。
    """

    steps: list[StepDef] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StockKeyDef:
        base = KeyDef.from_dict(data)
        return cls(
            key=base.key,
            label=base.label,
            description=base.description,
            cap=base.cap,
            soft=base.soft,
            show_cap=base.show_cap,
            sources=base.sources,
            uses=base.uses,
            sync_targets=base.sync_targets,
            steps=parse_steps(data.get("steps", [])),
        )


# ─── 模型类型 -> 数据类映射 ─────────────────────────────────

MODEL_CLASSES: dict[str, type[KeyDef]] = {
    MODEL_QUOTA: QuotaKeyDef,
    MODEL_REGEN: RegenKeyDef,
    MODEL_STOCK: StockKeyDef,
}


def parse_key_def(model_type: str, data: dict[str, Any]) -> KeyDef:
    """根据模型类型分发到对应数据类"""
    cls = MODEL_CLASSES.get(model_type)
    if cls is None:
        raise ValueError(f"未知模型类型: {model_type}")
    return cls.from_dict(data)
