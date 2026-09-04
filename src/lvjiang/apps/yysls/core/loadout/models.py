from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

EQUIPMENT_SLOTS = (
    "main_weapon", "sub_weapon", "ring", "pendant",
    "head", "chest", "leg", "wrist",
)

EQUIPMENT_CREATED_AT = "created_at"
EQUIPMENT_UPDATED_AT = "updated_at"


def normalize_equipment_times(equip: dict) -> dict:
    """复制装备并把历史数据缺失的时间字段规范为空字符串。"""
    value = dict(equip)
    for key in (EQUIPMENT_CREATED_AT, EQUIPMENT_UPDATED_AT):
        timestamp = value.get(key)
        value[key] = timestamp if isinstance(timestamp, str) else ""
    return value


def _empty_slots() -> dict[str, str | None]:
    return {key: None for key in EQUIPMENT_SLOTS}


@dataclass
class LoadoutPlan:
    id: str
    name: str
    main_martial_art: str = ""
    sub_martial_art: str = ""
    # 玩法：决定这套方案的调律方向（要什么增伤、定什么音）。
    # 候选由两个武学**无序**匹配出来——主副只是顺序标签，判别式是「要谁的
    # 增伤」。武学没登记进任何玩法时留空，此时只是算不出定音目标，方案照常可用。
    playstyle: str = ""
    # 战斗属性页“当前配置”中随备战方案切换的三项选择。流派由主副武学
    # 派生，不重复存储；显示选项属于用户偏好，仍保存在 session settings。
    base_attribute: str = ""
    gongjue: str = ""
    graduation_scheme: str = ""
    equipment: dict[str, str | None] = field(default_factory=_empty_slots)

    @classmethod
    def create(cls, name: str = "默认方案") -> "LoadoutPlan":
        return cls(id=uuid4().hex, name=name)

    @classmethod
    def from_dict(cls, plan_id: str, data: dict) -> "LoadoutPlan":
        slots = _empty_slots()
        raw_slots = data.get("equipment", {})
        if isinstance(raw_slots, dict):
            for key in EQUIPMENT_SLOTS:
                fp = raw_slots.get(key)
                slots[key] = str(fp) if fp else None
        return cls(
            id=plan_id,
            name=str(data.get("name") or "未命名方案"),
            main_martial_art=str(data.get("main_martial_art") or ""),
            sub_martial_art=str(data.get("sub_martial_art") or ""),
            playstyle=str(data.get("playstyle") or ""),
            base_attribute=str(data.get("base_attribute") or ""),
            gongjue=str(data.get("gongjue") or ""),
            graduation_scheme=str(data.get("graduation_scheme") or ""),
            equipment=slots,
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "main_martial_art": self.main_martial_art,
            "sub_martial_art": self.sub_martial_art,
            "playstyle": self.playstyle,
            "base_attribute": self.base_attribute,
            "gongjue": self.gongjue,
            "graduation_scheme": self.graduation_scheme,
            "equipment": dict(self.equipment),
        }


@dataclass
class LoadoutState:
    revision: int = 0
    active_plan_id: str = ""
    plans: dict[str, LoadoutPlan] = field(default_factory=dict)
    equipment_items: dict[str, dict] = field(default_factory=dict)
    ui_state: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "LoadoutState":
        plan = LoadoutPlan.create()
        return cls(active_plan_id=plan.id, plans={plan.id: plan})

    @classmethod
    def from_dict(cls, data: dict) -> "LoadoutState":
        if not isinstance(data, dict):
            return cls.empty()
        raw_plans = data.get("plans", {})
        plans = {
            str(pid): LoadoutPlan.from_dict(str(pid), value)
            for pid, value in raw_plans.items()
            if isinstance(value, dict)
        } if isinstance(raw_plans, dict) else {}
        if not plans:
            return cls.empty()
        active = str(data.get("active_plan_id") or "")
        if active not in plans:
            active = next(iter(plans))
        items = data.get("equipment_items", {})
        normalized_items = {
            str(fp): normalize_equipment_times(value)
            for fp, value in items.items()
            if isinstance(value, dict)
        } if isinstance(items, dict) else {}
        ui = data.get("ui_state", {})
        return cls(
            revision=int(data.get("revision") or 0),
            active_plan_id=active,
            plans=plans,
            equipment_items=normalized_items,
            ui_state=dict(ui) if isinstance(ui, dict) else {},
        )

    def to_dict(self) -> dict:
        return {
            "revision": self.revision,
            "active_plan_id": self.active_plan_id,
            "plans": {pid: plan.to_dict() for pid, plan in self.plans.items()},
            "equipment_items": self.equipment_items,
            "ui_state": self.ui_state,
        }

    @property
    def active_plan(self) -> LoadoutPlan:
        return self.plans[self.active_plan_id]

    def resolved_equipment(self, plan_id: str | None = None) -> dict[str, dict]:
        plan = self.plans[plan_id or self.active_plan_id]
        return {
            slot: self.equipment_items[fp]
            for slot, fp in plan.equipment.items()
            if fp and fp in self.equipment_items
        }


def resolve_school(main_art: str, sub_art: str, schools: dict) -> str | None:
    """Resolve a school by matching two selected arts as an unordered pair."""
    if not main_art or not sub_art:
        return None
    selected_arts = {str(main_art).strip(), str(sub_art).strip()}
    if len(selected_arts) != 2:
        return None
    for school, config in schools.items():
        main = config.get("main") or {}
        sub = config.get("sub") or {}
        school_arts = {
            str(main.get("martial_art") or "").strip(),
            str(sub.get("martial_art") or "").strip(),
        }
        if school_arts == selected_arts:
            return school
    return None
