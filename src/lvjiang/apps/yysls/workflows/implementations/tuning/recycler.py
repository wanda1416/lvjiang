"""装备回收用例。"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from loguru import logger

from lvjiang.apps.yysls.core.equip_parser import EquipmentData
from lvjiang.apps.yysls.core.tuning_rules import BEHAVIOR_STAGE_LABELS

if TYPE_CHECKING:
    from lvjiang.apps.yysls.workflows.implementations.tuning.route_strategy import (
        TuningRouteStrategy,
    )

from lvjiang.apps.yysls.workflows.implementations.tuning.ports import RecycleHostPort


class RecycleOutcome(Enum):
    """当前装备回收动作对背包格位产生的结果。"""

    RECYCLED = "recycled"       # 装备已移除，当前格由补位装备/空位占据
    LOCKED = "locked"           # 装备锁定无法回收，当前格仍是原装备
    UNAVAILABLE = "unavailable"  # 未找到回收入口，当前格仍是原装备
    STOPPED = "stopped"         # 用户要求结束任务，不推断装备最终状态

    @property
    def slot_changed(self) -> bool:
        return self is RecycleOutcome.RECYCLED

    @property
    def retry_blocked(self) -> bool:
        return self in (
            RecycleOutcome.LOCKED,
            RecycleOutcome.UNAVAILABLE,
            RecycleOutcome.STOPPED,
        )

    @property
    def detail_closed(self) -> bool:
        """回收动作结束后是否已经离开原装备详情。"""
        return self in (
            RecycleOutcome.RECYCLED,
            RecycleOutcome.LOCKED,
            RecycleOutcome.STOPPED,
        )


class TuningRecycler:
    """执行装备回收并报告格位是否变化。"""

    def __init__(self, wf: RecycleHostPort, routes: TuningRouteStrategy):
        self._wf = wf
        self._routes = routes

    def recycle_current(self, equip_data: EquipmentData, detail_scene: str,
                        stage: str, reason: str,
                        report: dict | None = None) -> RecycleOutcome:
        """回收当前详情页选中的装备

        回收入口路径按环境不同，见 TuningRouteStrategy.open_recycle_dialog。
        进入时背包详情页无弹窗；Android 未找到回收入口时收起
        弹窗返回 UNAVAILABLE（装备保留原地）。桌面端直接按 X，
        再由确认弹窗识别结果判定。成功后背包刷新、后续装备前移补位。
        确认弹窗同时读取「确认」与「取消」，任一按钮命中即说明弹窗正常，
        随后激活确认区域。两者都未命中时先判断是否已在背包页：在背包页
        才判定装备锁定，且仅 Android 收起仍然可见的更多菜单；不在背包页
        则属于异常状态，要求用户手动处理后明确选择「已回收」「保留装备」
        或「结束任务」。这里不复扫确认，也不根据回收入口是否可见推断状态。
        """
        label = equip_data.name or equip_data.type
        stage_label = BEHAVIOR_STAGE_LABELS.get(stage, stage)
        logger.info(f"  [{stage_label}] 回收 {label}：{reason}")

        if not self._routes.open_recycle_dialog():
            return RecycleOutcome.UNAVAILABLE
        return self._handle_recycle_confirm(
            equip_data, stage, stage_label, reason, report)

    def _handle_recycle_confirm(self, equip_data: EquipmentData,
                                stage: str, stage_label: str, reason: str,
                                report: dict | None) -> RecycleOutcome:
        """回收确认弹窗处理（android/desktop 共用）"""
        if not self._routes.try_confirm_recycle_dialog():
            if self._routes.is_in_bag_page():
                logger.warning("  未识别到回收弹窗的确认或取消按钮，"
                               "当前已在背包页，判定装备锁定并保留")
                self._routes.close_recycle_entry_on_lock()
                return RecycleOutcome.LOCKED
            logger.error("  未识别到回收弹窗的确认或取消按钮，"
                         "且当前不在背包页，回收状态异常")
            outcome = self._ask_manual_recycle_outcome()
            if outcome is not RecycleOutcome.RECYCLED:
                return outcome
            logger.info("  用户确认已手动回收装备")
        return self._record_recycled(
            equip_data, stage, stage_label, reason, report)

    def _ask_manual_recycle_outcome(self) -> RecycleOutcome:
        """异常回收现场由用户确认真实结果，禁止默认记成锁定。"""
        engine = self._wf.engine
        callback = getattr(engine, "_ui_callback", None) if engine else None
        if callback is None:
            logger.error("无 UI 回调，无法确认手动回收结果，结束任务")
            return RecycleOutcome.STOPPED
        choice = callback(
            "choose",
            message=(
                "回收装备失败，请手动处理并回到背包页，"
                "然后选择装备的实际处理结果。"
            ),
            choices=[
                {"label": "已回收", "value": "recycled", "role": "accept"},
                {"label": "保留装备", "value": "kept",
                 "role": "destructive"},
                {"label": "结束任务", "value": "stopped", "role": "reject"},
            ],
            cancel_value="stopped",
        )
        outcomes = {
            "recycled": RecycleOutcome.RECYCLED,
            "kept": RecycleOutcome.LOCKED,
            "stopped": RecycleOutcome.STOPPED,
        }
        return outcomes.get(choice, RecycleOutcome.STOPPED)

    def _record_recycled(self, equip_data: EquipmentData,
                         stage: str, stage_label: str, reason: str,
                         report: dict | None) -> RecycleOutcome:
        """提交真实回收结果；自动确认和人工确认共用同一记录出口。"""
        wf = self._wf
        label = equip_data.name or equip_data.type
        wf.wait_stable("page_refresh")  # 回收完成，背包刷新补位
        if report is not None:
            report["recycled"] = True
            report["recycle_reason"] = reason
        wf.output.setdefault("recycled_items", []).append({
            "name": equip_data.name, "type": equip_data.type,
            "quality": equip_data.quality,
            "stage": stage, "reason": reason,
        })
        logger.info(f"  [{stage_label}] {label} 已回收")
        return RecycleOutcome.RECYCLED
