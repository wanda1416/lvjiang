from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from ...config.equipment_slots import EQUIPMENT_SLOTS

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


def _slot_dingyin(raw: object) -> dict[str, str]:
    """只保留已知槽位上的合法定音种类；其余一律当未记录丢弃。"""
    from ..equip_parser.dingyin_parser import DINGYIN_TYPES

    if not isinstance(raw, dict):
        return {}
    return {
        key: str(raw[key])
        for key in EQUIPMENT_SLOTS
        if str(raw.get(key) or "") in DINGYIN_TYPES
    }


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
    # 每个槽位在这套方案下展示哪种定音（normal/zhige）。游戏里备战方案自己
    # 记着这件装备该显示哪种音，切方案就跟着切，所以它属于方案而不是装备。
    # 缺键固定按 normal 读；不参与任何计算。
    dingyin: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(cls, name: str = "默认方案") -> "LoadoutPlan":
        return cls(id=uuid4().hex, name=name)

    def clear_slot(self, slot_key: str) -> None:
        """清空一个槽位：装备和它的定音选择必须一起走。

        只清装备会在磁盘上留下「空槽位仍有定音选择」的自相矛盾状态，下一件
        装进来还可能继承到上一件的定音模式。卸下、删除装备都走这里。
        """
        self.equipment[slot_key] = None
        self.dingyin.pop(slot_key, None)

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
            dingyin=_slot_dingyin(data.get("dingyin")),
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
            "dingyin": dict(self.dingyin),
        }


@dataclass
class LoadoutState:
    revision: int = 0
    active_plan_id: str = ""
    plans: dict[str, LoadoutPlan] = field(default_factory=dict)
    plan_order: list[str] = field(default_factory=list)
    equipment_items: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "LoadoutState":
        # 尚未落盘时，各读取者必须看到同一个默认方案 ID，首次编辑才持久化。
        plan = LoadoutPlan(id="default", name="默认方案")
        return cls(active_plan_id=plan.id, plans={plan.id: plan},
                   plan_order=[plan.id])

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
        raw_order = data.get("plan_order")
        order: list[str] = []
        if isinstance(raw_order, list):
            for value in raw_order:
                pid = str(value)
                if pid in plans and pid not in order:
                    order.append(pid)
        order.extend(pid for pid in plans if pid not in order)
        active = str(data.get("active_plan_id") or "")
        if active not in plans:
            active = order[0]
        items = data.get("equipment_items", {})
        normalized_items = {
            str(fp): normalize_equipment_times(value)
            for fp, value in items.items()
            if isinstance(value, dict)
        } if isinstance(items, dict) else {}
        return cls(
            revision=int(data.get("revision") or 0),
            active_plan_id=active,
            plans=plans,
            plan_order=order,
            equipment_items=normalized_items,
        )

    def to_dict(self) -> dict:
        return {
            "revision": self.revision,
            "active_plan_id": self.active_plan_id,
            "plans": {pid: plan.to_dict() for pid, plan in self.plans.items()},
            "plan_order": self.ordered_plan_ids(),
            "equipment_items": self.equipment_items,
        }

    def ordered_plan_ids(self) -> list[str]:
        """旧数据沿原对象顺序展示；新顺序只引用仍存在的方案 ID。"""
        order: list[str] = []
        for pid in self.plan_order:
            if pid in self.plans and pid not in order:
                order.append(pid)
        return order + [pid for pid in self.plans if pid not in order]

    @property
    def active_plan(self) -> LoadoutPlan:
        return self.plans[self.active_plan_id]

    def active_school(self, schools: dict) -> str:
        """当前激活方案的流派（见 ``plan_school``）。"""
        return plan_school(self.active_plan, schools)

    def resolved_equipment(self, plan_id: str | None = None) -> dict[str, dict]:
        plan = self.plans[plan_id or self.active_plan_id]
        return {
            slot: self.equipment_items[fp]
            for slot, fp in plan.equipment.items()
            if fp and fp in self.equipment_items
        }


def plan_school(plan: LoadoutPlan, schools: dict) -> str:
    """方案的流派：由主副武学无序反查；解析不出时为空串。

    这是「当前流派是什么」的唯一口径——面板下拉、装备页的候选过滤和
    评分上下文都从方案派生，不各自维护一份选择。
    """
    return resolve_school(plan.main_martial_art, plan.sub_martial_art, schools) or ""


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
