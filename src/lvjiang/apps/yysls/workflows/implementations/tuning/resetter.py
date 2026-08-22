"""调律重置用例。

负责次数、冷却和材料门槛检查，以及重置确认交互；不处理装备回收。
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from lvjiang.apps.yysls.workflows.implementations.tuning.ports import ResetHostPort

from ......i18n import tr


class TuningResetter:
    """执行一次受规则约束的调律重置。"""

    def __init__(self, wf: ResetHostPort):
        self._wf = wf

    def reset_remaining(self) -> int:
        """读取重置按钮并解析剩余次数。"""
        wf = self._wf
        raw = wf.ocr_scene(wf.TUNE_SCENE, ["reset_tune"]).get(
            "reset_tune", ""
        ) or ""
        match = re.search(r"(\d+)\s*[/／]\s*\d+", raw)
        if match:
            return int(match.group(1))
        match = re.search(r"\d+", raw)
        return int(match.group()) if match else 0

    def try_reset_tune(
        self,
        cfg: Any,
        resets_used: int,
        why: str,
        min_material_count: int | None = None,
    ) -> bool | str:
        """检查约束并提交重置。

        返回 True 表示成功，False 表示次数门槛拒绝，字符串表示需要直接
        跳过当前装备的业务原因。
        """
        wf = self._wf
        logger.debug(
            "  [try_reset_tune] min_material_count={}, resets_used={}",
            min_material_count,
            resets_used,
        )
        if resets_used >= 1:
            logger.info("  本件已重置过一次，冷却期内不再重置")
            return "本件已重置过一次，冷却期内不再重置"
        if resets_used >= cfg.max_resets:
            logger.info("  重置次数已达上限（{}），不再重置", cfg.max_resets)
            return False
        remaining = self.reset_remaining()
        if remaining <= 0:
            logger.info("  重置调律按钮无剩余次数，不再重置")
            return False

        logger.info("  执行重置调律（剩余次数 OCR: {}）：{}", remaining, why)
        wf._emit_operation(
            "reset",
            f"准备重置，剩余次数 {remaining}，正在检查冷却状态",
            reason=why,
            resets=resets_used,
        )
        wf.click_region(wf.TUNE_SCENE, "reset_tune")
        wf.wait_stable("page_refresh")
        check_text = wf.ocr_scene(wf.TUNE_SCENE, ["reset_check"]).get(
            "reset_check", ""
        ) or ""
        if tr("可重置") not in check_text:
            logger.info(
                "  冷却期检查未通过（reset_check={!r}），装备在冷却期，降级跳过",
                check_text,
            )
            self._close_dialog()
            return tr("装备重置冷却期，跳过该装备")

        if min_material_count is not None:
            wf._emit_operation(
                "reset",
                "冷却检查通过，正在读取重置材料数量",
                reason=why,
                resets=resets_used,
            )
            reset_info = wf.ocr_scene(wf.TUNE_SCENE, ["reset_info"]).get(
                "reset_info", ""
            ) or ""
            material_count = self.parse_material_count(reset_info)
            if material_count is None:
                logger.error(
                    "  reset_info 未识别到「持有」关键字（reset_info={!r}），跳过",
                    reset_info,
                )
                self._close_dialog()
                return tr("重置材料信息识别失败，跳过该装备")
            if material_count < min_material_count:
                logger.info(
                    "  重置材料不足（持有 {} < 要求 {}），跳过该装备",
                    material_count,
                    min_material_count,
                )
                self._close_dialog()
                return (
                    f"重置材料不足（持有 {material_count} < "
                    f"要求 {min_material_count}），跳过该装备"
                )
            logger.info(
                "  重置材料检查通过（持有 {} >= 要求 {}）",
                material_count,
                min_material_count,
            )

        wf._emit_operation(
            "reset", "检查通过，正在执行两次重置确认", reason=why, resets=resets_used
        )
        wf.click_region(wf.TUNE_SCENE, "reset_confirm")
        wf.wait_delay("secondary_confirm")
        wf.click_region(wf.TUNE_SCENE, "reset_confirm_2")
        wf._emit_operation("reset", "重置已提交，正在读取重置结果")
        wf.wait_stable("page_refresh")
        wf.click_region(wf.TUNE_SCENE, "close_btn")
        wf.wait_delay("step_interval")
        return True

    @staticmethod
    def parse_material_count(text: str) -> int | None:
        """解析「持有 N」；缺少“持有”返回 None，缺少数字返回 0。"""
        if tr("持有") not in text:
            return None
        match = re.search(tr(r"持有\s*(\d+)"), text)
        return int(match.group(1)) if match else 0

    def _close_dialog(self) -> None:
        self._wf.click_region(self._wf.TUNE_SCENE, "back")
        self._wf.wait_delay("step_interval")
