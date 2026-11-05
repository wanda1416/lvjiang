"""首屏过渡装清理：当前可见替代品证明与逐件身份复核。"""
from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from lvjiang.apps.shuijiao_tuning.workflows.implementations.tuning.recycler import (
    RecycleOutcome,
)

from lvjiang.apps.yysls.core.equip_parser import EquipmentData


def signature(equip: EquipmentData) -> tuple:
    return (equip.type, equip.level, equip.quality, tuple((a.name, a.value) for a in equip.affixes))


@dataclass
class VisibleEquipment:
    position: int
    equipment: EquipmentData
    graduates: set[str]
    transitions: set[str]


class TransitionCleanup:
    def __init__(self, workflow):
        self.wf = workflow
        self.judge = workflow.judge.profile_judge()
        self.keys = workflow.ctx.build_profiles or []

    def run(self):
        wf = self.wf
        panel = wf._find_panel(wf.GRID_SCENE, wf.GRID_PANEL)
        if panel is None or wf.is_stopped:
            return
        previous_bottom = wf._slot_level_exhausted
        wf._slot_level_exhausted = False
        try:
            self._run(panel.rows, panel.cols)
        finally:
            wf._close_equipment_detail()
            wf._slot_level_exhausted = previous_bottom

    def _read(self, position, cols):
        wf = self.wf
        _, _, raw = wf._read_row("auto", position // cols + 1, position % cols + 1)
        if wf._equipment_read_state(raw) != "valid" or raw.get("is_wuku"):
            return None
        return EquipmentData.from_dict(raw)

    def _run(self, rows, cols):
        wf = self.wf
        wf._emit_operation("transition_review", "复核首屏过渡装及同部位毕业替代品；不滚动、不调律")
        visible: list[VisibleEquipment] = []
        for pos in range(rows * cols):
            if wf.is_stopped:
                return
            equip = self._read(pos, cols)
            wf._close_equipment_detail()
            if equip is None:
                break
            if len(equip.affixes) != 5:
                continue
            graduates = {k for k in self.keys if self.judge.assess(k, equip).viable}
            transitions = set(self.judge.transition_keys(equip, self.keys)) if not graduates else set()
            visible.append(VisibleEquipment(pos, equip, graduates, transitions))

        for item in list(visible):
            if wf.is_stopped:
                return
            equip = item.equipment
            if not item.transitions or equip.lock_status != "unlock" or equip.cooldown_kind:
                continue
            proof = next((g for g in visible if g is not item and g.equipment.type == equip.type
                          and g.graduates.intersection(item.transitions)), None)
            if proof is None:
                continue
            # 不能仅凭曾经见过毕业装便删除：动作前分别重读当前替代品与目标。
            checked = self._read(proof.position, cols)
            wf._close_equipment_detail()
            if checked is None or signature(checked) != signature(proof.equipment) or not any(
                    self.judge.assess(k, checked).viable for k in proof.graduates.intersection(item.transitions)):
                self._mismatch()
                return
            current = self._read(item.position, cols)
            if current is None or signature(current) != signature(equip):
                self._mismatch()
                return
            if current.lock_status != "unlock" or current.cooldown_kind or wf.is_stopped:
                wf._close_equipment_detail()
                continue
            reason = ("过渡装清理：已确认同流派同部位毕业替代品（含至多一次合法转律）：" +
                      "、".join(a.name for a in checked.affixes) + "；当前装备仅满足目标并集、缺少刚需")
            wf._emit_equip_started(current.name or current.type, current.to_dict(), current)
            detail = wf.WEAPON_DETAIL if current.category in ("weapon", "jewelry") else wf.ARMOR_DETAIL
            outcome = wf._recycle_current(current, detail, "tune", reason, current_affix_count=5)
            if outcome is not None and outcome.detail_closed:
                wf._equipment_detail_open = False
            wf._emit_equip_finish(current.name or current.type, current, affix_count=5,
                                 status="recycled" if outcome is RecycleOutcome.RECYCLED else "protected", reason=reason,
                                 tuning_mode="transition_review")
            wf._close_equipment_detail()
            if outcome is RecycleOutcome.STOPPED:
                return
            if outcome is RecycleOutcome.RECYCLED:
                removed = item.position
                visible.remove(item)
                for other in visible:
                    if other.position > removed:
                        other.position -= 1

    def _mismatch(self):
        message = "过渡装复核未匹配到原装备，已停止清理并保留；请检查背包顺序或识别区域"
        logger.warning(message)
        self.wf.output.setdefault("transition_notes", []).append(message)
        self.wf._emit_operation("transition_review", message)
