from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

EQUIPMENT_SLOTS = (
    "main_weapon", "sub_weapon", "ring", "pendant",
    "head", "chest", "leg", "wrist",
)


def _empty_slots() -> dict[str, str | None]:
    return {key: None for key in EQUIPMENT_SLOTS}


@dataclass
class LoadoutPlan:
    id: str
    name: str
    main_martial_art: str = ""
    sub_martial_art: str = ""
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
            equipment=slots,
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "main_martial_art": self.main_martial_art,
            "sub_martial_art": self.sub_martial_art,
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
        ui = data.get("ui_state", {})
        return cls(
            revision=int(data.get("revision") or 0),
            active_plan_id=active,
            plans=plans,
            equipment_items=dict(items) if isinstance(items, dict) else {},
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
    """Resolve only an exact main/sub martial-art pair."""
    if not main_art or not sub_art:
        return None
    for school, config in schools.items():
        main = config.get("main") or {}
        sub = config.get("sub") or {}
        if (main.get("martial_art") == main_art
                and sub.get("martial_art") == sub_art):
            return school
    return None
