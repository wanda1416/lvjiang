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
    UNAVAILABLE = "unavailable" # 未找到回收入口，当前格仍是原装备

    @property
    def slot_changed(self) -> bool:
        return self is RecycleOutcome.RECYCLED

    @property
    def retry_blocked(self) -> bool:
        return self in (RecycleOutcome.LOCKED, RecycleOutcome.UNAVAILABLE)


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
        进入时背包详情页无弹窗；未找到回收按钮时收起弹窗返回
        UNAVAILABLE（装备保留原地）。成功后背包刷新、后续装备前移补位。
        装备锁定检测：确认弹窗内无「确认」字样 = 装备被锁定，
        收起弹窗返回 LOCKED。
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
        wf = self._wf
        label = equip_data.name or equip_data.type
        # 装备锁定检测：确认弹窗内应含「确认」，否则装备被锁定
        confirm_text = wf.ocr_scene(wf.EQUIP_DETAIL,
                                    ["recycle_confirm"]).get("recycle_confirm", "") or ""
        # confirm_text 是游戏截屏 OCR 结果，恒为中文，不能过 tr()
        # （英文界面下会拿翻译后的英文去匹配中文截屏，永远匹配不上）。
        if "确认" not in confirm_text:
            logger.warning(f"  回收确认弹窗未识别到「确认」"
                           f"（recycle_confirm={confirm_text!r}），"
                           f"装备被锁定，保留")
            self._routes.close_recycle_entry_on_lock()
            return RecycleOutcome.LOCKED
        wf.click_region(wf.EQUIP_DETAIL, "recycle_confirm")
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
