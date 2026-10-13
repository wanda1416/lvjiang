"""备战方案管理的未保存草稿。

编辑先落在内存草稿上，点「保存」才写回用户数据文件。写回按字段合并：只覆盖
本次真正改过的字段和方案，绝不把打开窗口时的整份快照盖回去——对话框打开的
这段时间里，任务或主界面随时可能写入别的字段或新增方案，那些改动必须原样
保留。这也是不能直接调用 ``configure_plan`` 之外的整份写入的原因。
"""

from __future__ import annotations

from uuid import uuid4

from ...core.loadout import LoadoutPlan, LoadoutRepository
from ...core.loadout.models import (
    COMBAT_TYPE_PVE,
    LoadoutState,
    normalize_combat_type,
)


def _plan_snapshots(state: LoadoutState) -> dict[str, dict]:
    return {plan_id: plan.to_dict() for plan_id, plan in state.plans.items()}


def _drop_plan(state: LoadoutState, plan_id: str) -> None:
    """从状态里删掉一个方案，并修正顺序与活动方案。

    与 ``LoadoutRepository.delete_plan`` 同一套收尾：少了活动方案的空档会让
    ``active_plan`` 直接 KeyError。
    """
    if plan_id not in state.plans or len(state.plans) <= 1:
        return
    order = state.ordered_plan_ids()
    index = order.index(plan_id)
    del state.plans[plan_id]
    state.plan_order = [pid for pid in order if pid != plan_id]
    if state.active_plan_id == plan_id:
        state.active_plan_id = state.plan_order[
            min(index, len(state.plan_order) - 1)]


def _merged_order(state: LoadoutState, order: list[str]) -> list[str]:
    """草稿顺序在前，磁盘上多出来的方案（外部新增）接在后面。"""
    merged = [pid for pid in order if pid in state.plans]
    merged.extend(
        pid for pid in state.ordered_plan_ids() if pid not in merged)
    return merged


def merge_into(state: LoadoutState, draft: "PlanDraft") -> None:
    """把草稿与磁盘状态合并；只动草稿真正改过的字段。"""
    for plan_id, plan in draft.added_plans().items():
        if plan_id not in state.plans:
            state.plans[plan_id] = LoadoutPlan.from_dict(
                plan_id, plan.to_dict())
    for plan_id, fields in draft.changed_fields().items():
        target = state.plans.get(plan_id)
        if target is None:
            # 外部已经删掉这套方案：不复活它，用户的字段编辑随之作废。
            continue
        for key, value in fields.items():
            if key == "name":
                value = str(value).strip() or target.name
            setattr(target, key, value)
    for plan_id in draft.removed_plan_ids():
        _drop_plan(state, plan_id)
    if draft.order_changed():
        state.plan_order = _merged_order(state, draft.ordered_plan_ids())
    if draft.world_level_changed():
        state.world_level = draft.state.world_level


class PlanDraft:
    """一个用户的待保存编辑；``state`` 就是对话框当前展示的工作副本。"""

    def __init__(self, state: LoadoutState) -> None:
        self.state = state
        self._base_plans = _plan_snapshots(state)
        self._base_order = state.ordered_plan_ids()
        self._base_world_level = state.world_level

    @classmethod
    def load(cls, repo: LoadoutRepository) -> "PlanDraft":
        return cls(repo.load())

    # ── 状态查询 ──

    @property
    def dirty(self) -> bool:
        """草稿与上次保存的基线是否有差异。"""
        if self.state.world_level != self._base_world_level:
            return True
        if self.state.ordered_plan_ids() != self._base_order:
            return True
        if any(pid not in self.state.plans for pid in self._base_plans):
            return True
        return any(
            self._base_plans.get(pid) != plan.to_dict()
            for pid, plan in self.state.plans.items())

    def added_plans(self) -> dict[str, LoadoutPlan]:
        return {pid: plan for pid, plan in self.state.plans.items()
                if pid not in self._base_plans}

    def removed_plan_ids(self) -> list[str]:
        return [pid for pid in self._base_plans if pid not in self.state.plans]

    def changed_fields(self) -> dict[str, dict]:
        """只列出与基线不同的字段，供保存时按字段合并。"""
        changed: dict[str, dict] = {}
        for pid, plan in self.state.plans.items():
            base = self._base_plans.get(pid)
            if base is None:
                continue
            fields = {key: value for key, value in plan.to_dict().items()
                      if base.get(key) != value}
            if fields:
                changed[pid] = fields
        return changed

    def ordered_plan_ids(self) -> list[str]:
        return self.state.ordered_plan_ids()

    def order_changed(self) -> bool:
        return self.state.ordered_plan_ids() != self._base_order

    def world_level_changed(self) -> bool:
        return self.state.world_level != self._base_world_level

    # ── 编辑 ──

    def update_fields(self, plan_id: str, updates: dict) -> bool:
        """按字段更新草稿里的一套方案；返回是否真的改了东西。

        名称的兜底与 ``LoadoutRepository.configure_plan`` 一致：去空白后为空
        就保持原名，否则一次误清空会先显示在表格上、再随保存写进磁盘。
        """
        plan = self.state.plans.get(plan_id)
        if plan is None or not updates:
            return False
        for key, value in updates.items():
            if key == "name":
                value = str(value).strip() or plan.name
            setattr(plan, key, value)
        return True

    def set_world_level(self, level: int | None) -> None:
        self.state.world_level = level

    def add_plan(self, *, name: str, main_art: str, sub_art: str,
                 playstyle: str = "",
                 combat_type: str = COMBAT_TYPE_PVE) -> LoadoutPlan:
        """新建方案只进草稿；与仓储同规则，必须同时绑定两门武学。"""
        main_art = main_art.strip()
        sub_art = sub_art.strip()
        if not main_art or not sub_art:
            raise ValueError("新建方案必须同时绑定主武学和副武学")
        plan = LoadoutPlan(
            id=uuid4().hex, name=name.strip() or "未命名方案",
            main_martial_art=main_art, sub_martial_art=sub_art,
            playstyle=playstyle,
            combat_type=normalize_combat_type(combat_type))
        self.state.plans[plan.id] = plan
        self.state.plan_order = self.state.ordered_plan_ids()
        return plan

    def remove_plan(self, plan_id: str) -> None:
        if len(self.state.plans) <= 1:
            raise ValueError("至少保留一个备战方案")
        if plan_id not in self.state.plans:
            raise KeyError(plan_id)
        _drop_plan(self.state, plan_id)

    def move_plan(self, plan_id: str, offset: int) -> None:
        if offset not in (-1, 1):
            raise ValueError("方案只能上移或下移一位")
        if plan_id not in self.state.plans:
            raise KeyError(plan_id)
        order = self.state.ordered_plan_ids()
        index = order.index(plan_id)
        target = index + offset
        if target < 0 or target >= len(order):
            return
        order[index], order[target] = order[target], order[index]
        self.state.plan_order = order

    # ── 保存 ──

    def flush(self, repo: LoadoutRepository) -> None:
        """按字段合并写回磁盘。"""
        def mutate(state: LoadoutState) -> None:
            merge_into(state, self)

        repo.update(mutate)

    def accept(self) -> None:
        """保存成功后把当前内容作为新基线；调用方负责保证写盘已成功。"""
        self._base_plans = _plan_snapshots(self.state)
        self._base_order = self.state.ordered_plan_ids()
        self._base_world_level = self.state.world_level
