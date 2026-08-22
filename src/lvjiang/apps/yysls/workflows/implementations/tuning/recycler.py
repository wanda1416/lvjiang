"""重置与回收 — TuningRecycler

重置调律（次数解析、冷却期检查、二次确认）与装备回收。
通过 wf 引用访问 UI 操作原语。
"""

from __future__ import annotations

import re
from enum import Enum
from typing import TYPE_CHECKING

from loguru import logger

from lvjiang.apps.yysls.core.equip_parser import EquipmentData
from lvjiang.apps.yysls.core.tuning_rules import BEHAVIOR_STAGE_LABELS

from ......i18n import tr

if TYPE_CHECKING:
    from lvjiang.apps.yysls.workflows.implementations.auto_tuning import (
        AutoTuningWorkflow,
    )
    from lvjiang.apps.yysls.workflows.implementations.tuning.route_strategy import (
        TuningRouteStrategy,
    )


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
    """重置调律与装备回收"""

    def __init__(self, wf: AutoTuningWorkflow, routes: TuningRouteStrategy):
        self._wf = wf
        self._routes = routes

    def reset_remaining(self) -> int:
        """OCR 调律页「重置调律」按钮文本，解析剩余次数

        按钮文本携带次数（如「重置调律(3)」）；兼容 x/y 样式取 x
        （剩余/总数），无斜杠取首个数字；读不到任何数字 =
        次数已用尽（返回 0）。
        """
        wf = self._wf
        raw = wf.ocr_scene(wf.TUNE_SCENE, ["reset_tune"]).get(
            "reset_tune", "") or ""
        m = re.search(r"(\d+)\s*[/／]\s*\d+", raw)
        if m:
            return int(m.group(1))
        m = re.search(r"\d+", raw)
        return int(m.group()) if m else 0

    def try_reset_tune(self, cfg, resets_used: int, why: str,
                       min_material_count: int | None = None):
        """在调律页执行一次重置调律（点按钮 + 冷却检查 + 材料检查 + 确认 + 二次确认 + 关闭）

        重置清空首词条以外的全部词条；重置后有冷却期不可连重
        → 本件硬限只重置一次。次数门槛：本地计数达配置上限即止；
        按钮文本剩余次数另作硬门（读不到数字 = 用尽，不重置）。
        点击重置按钮后：
        - OCR reset_check 区域，无「可调律重置」字样 = 装备在冷却期，降级跳过；
        - OCR reset_info 区域，解析「持有 N」格式获取材料数量，
          与等级配置的 min_material_count 比较，不足则跳过。

        Returns: True=成功；False=正常拒绝（走 reset_exhausted）；
                 str=冷却期/材料不足拒绝（强制跳过，原因字符串）。
        """
        wf = self._wf
        logger.debug(
            f"  [try_reset_tune] min_material_count={min_material_count}, "
            f"resets_used={resets_used}")
        if resets_used >= 1:
            # 重置后进入冷却期，本次工作内不可再重置该件
            logger.info("  本件已重置过一次，冷却期内不再重置")
            return "本件已重置过一次，冷却期内不再重置"
        if resets_used >= cfg.max_resets:
            logger.info(f"  重置次数已达上限（{cfg.max_resets}），不再重置")
            return False
        remaining = self.reset_remaining()
        if remaining <= 0:
            logger.info("  重置调律按钮无剩余次数，不再重置")
            return False
        logger.info(f"  执行重置调律（剩余次数 OCR: {remaining}）：{why}")
        wf._emit_operation(
            "reset", f"准备重置，剩余次数 {remaining}，正在检查冷却状态",
            reason=why, resets=resets_used)
        wf.click_region(wf.TUNE_SCENE, "reset_tune")
        wf.wait_stable("page_refresh")  # 重置确认弹窗
        # 冷却期检查：确认弹窗内应含「可调律重置」，否则装备在冷却期
        check_text = wf.ocr_scene(wf.TUNE_SCENE, ["reset_check"]).get(
            "reset_check", "") or ""
        if tr("可重置") not in check_text:
            logger.info(f"  冷却期检查未通过（reset_check={check_text!r}），"
                        "装备在冷却期，降级跳过")
            wf.click_region(wf.TUNE_SCENE, "back")  # 关闭弹窗回调律页
            wf.wait_delay("step_interval")
            return tr("装备重置冷却期，跳过该装备")
        # 材料检查：读取 reset_info 解析「持有 N」格式
        if min_material_count is not None:
            wf._emit_operation(
                "reset", "冷却检查通过，正在读取重置材料数量",
                reason=why, resets=resets_used)
            reset_info_text = wf.ocr_scene(wf.TUNE_SCENE, ["reset_info"]).get(
                "reset_info", "") or ""
            material_count = self._parse_material_count(reset_info_text)
            if material_count is None:
                # 未识别到「持有」关键字 → 异常
                logger.error(
                    f"  reset_info 未识别到「持有」关键字"
                    f"（reset_info={reset_info_text!r}），视为异常，跳过")
                wf.click_region(wf.TUNE_SCENE, "back")  # 关闭弹窗回调律页
                wf.wait_delay("step_interval")
                return tr("重置材料信息识别失败，跳过该装备")
            if material_count < min_material_count:
                # 材料不足
                logger.info(
                    f"  重置材料不足（持有 {material_count} < "
                    f"要求 {min_material_count}），跳过该装备")
                wf.click_region(wf.TUNE_SCENE, "back")  # 关闭弹窗回调律页
                wf.wait_delay("step_interval")
                return f"重置材料不足（持有 {material_count} < 要求 {min_material_count}），跳过该装备"
            logger.info(f"  重置材料检查通过（持有 {material_count} >= 要求 {min_material_count}）")
        wf._emit_operation(
            "reset", "检查通过，正在执行两次重置确认",
            reason=why, resets=resets_used)
        wf.click_region(wf.TUNE_SCENE, "reset_confirm")
        # 游戏在两次确认间强制等 5s，二次确认按钮才可点
        wf.wait_delay("secondary_confirm")
        wf.click_region(wf.TUNE_SCENE, "reset_confirm_2")
        wf._emit_operation("reset", "重置已提交，正在读取重置结果")
        wf.wait_stable("page_refresh")  # 重置结果弹窗出现
        wf.click_region(wf.TUNE_SCENE, "close_btn")  # 关闭 → 回到调律进度页
        wf.wait_delay("step_interval")
        return True

    def _parse_material_count(self, text: str) -> int | None:
        """解析 reset_info 文本中的材料数量

        期望格式：「持有 N」，N 为数字。
        返回数字；未识别到「持有」返回 None。
        """
        if tr("持有") not in text:
            return None
        # 匹配「持有」后的数字
        m = re.search(tr(r"持有\s*(\d+)"), text)
        if m:
            return int(m.group(1))
        # 有「持有」但没解析到数字，返回 0
        return 0

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
        if tr("确认") not in confirm_text:
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
