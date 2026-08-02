"""重置与回收 — TuningRecycler

重置调律（次数解析、冷却期检查、二次确认）与装备回收。
通过 wf 引用访问 UI 操作原语。
"""

from __future__ import annotations

import random
import re
from typing import TYPE_CHECKING

from loguru import logger

from lvjiang.apps.yysls.equip_parser import EquipmentData
from lvjiang.apps.yysls.evaluator.tuning_rules import BEHAVIOR_STAGE_LABELS

if TYPE_CHECKING:
    from lvjiang.apps.yysls.workflows.implementations.auto_tuning import (
        AutoTuningWorkflow,
    )


class TuningRecycler:
    """重置调律与装备回收"""

    def __init__(self, wf: AutoTuningWorkflow):
        self._wf = wf

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

    def try_reset_tune(self, cfg, resets_used: int, why: str):
        """在调律页执行一次重置调律（点按钮 + 冷却检查 + 确认 + 二次确认 + 关闭）

        重置清空首词条以外的全部词条；重置后有冷却期不可连重
        → 本件硬限只重置一次。次数门槛：本地计数达配置上限即止；
        按钮文本剩余次数另作硬门（读不到数字 = 用尽，不重置）。
        点击重置按钮后 OCR check_1 区域，无「可调律重置」字样 =
        装备在冷却期，点返回回到调律页并降级为跳过。

        Returns: True=成功；False=正常拒绝（走 reset_exhausted）；
                 str=冷却期拒绝（强制跳过，原因字符串）。
        """
        wf = self._wf
        if resets_used >= 1:
            # 重置后进入冷却期，本次工作内不可再重置该件
            logger.info("  本件已重置过一次，冷却期内不再重置")
            return False
        if resets_used >= cfg.max_resets:
            logger.info(f"  重置次数已达上限（{cfg.max_resets}），不再重置")
            return False
        remaining = self.reset_remaining()
        if remaining <= 0:
            logger.info("  重置调律按钮无剩余次数，不再重置")
            return False
        logger.info(f"  执行重置调律（剩余次数 OCR: {remaining}）：{why}")
        wf.click_region(wf.TUNE_SCENE, "reset_tune")
        wf.wait_delay("page_refresh_wait")  # 重置确认弹窗
        # 冷却期检查：确认弹窗内应含「可调律重置」，否则装备在冷却期
        check_text = wf.ocr_scene(wf.TUNE_SCENE, ["check_1"]).get(
            "check_1", "") or ""
        if "可重置" not in check_text:
            logger.info(f"  冷却期检查未通过（check_1={check_text!r}），"
                        "装备在冷却期，降级跳过")
            wf.click_region(wf.TUNE_SCENE, "back")  # 关闭弹窗回调律页
            wf.wait_delay("step_interval")
            return "装备重置冷却期，跳过该装备"
        wf.click_region(wf.TUNE_SCENE, "reset_confirm")
        # 游戏在两次确认间强制等 5s，二次确认按钮才可点 → 等 6-7s
        wf.wait_seconds(random.uniform(6.0, 7.0))
        wf.click_region(wf.TUNE_SCENE, "reset_confirm_2")
        wf.wait_delay("page_refresh_wait")  # 重置结果弹窗出现
        wf.click_region(wf.TUNE_SCENE, "close_btn")  # 关闭 → 回到调律进度页
        wf.wait_delay("step_interval")
        return True

    def recycle_current(self, equip_data: EquipmentData, detail_scene: str,
                        stage: str, reason: str,
                        report: dict | None = None) -> bool:
        """回收当前详情页选中的装备：更多 → 子菜单「回收」→ 确认弹窗

        进入时背包详情页无弹窗；未找到回收按钮时收起弹窗返回
        False（装备保留原地）。成功后背包刷新、后续装备前移补位。
        """
        wf = self._wf
        label = equip_data.name or equip_data.type
        stage_label = BEHAVIOR_STAGE_LABELS.get(stage, stage)
        logger.info(f"  [{stage_label}] 回收 {label}：{reason}")
        wf.click_region(wf.EQUIP_DETAIL, "more_func")
        wf.wait_delay("page_refresh_wait")  # 详情页 → 「更多」弹窗展开
        key = wf.ocr_scene_by(
            wf.EQUIP_DETAIL,
            ["sub_func_1", "sub_func_2", "sub_func_3", "sub_func_4"],
            "回收", "contains")
        if not key:
            logger.warning("未找到回收按钮，装备保留")
            wf.click_region(wf.EQUIP_DETAIL, "more_func")
            wf.wait_delay("step_interval")
            return False
        wf.click_region(wf.EQUIP_DETAIL, key)
        wf.wait_delay("page_refresh_wait")  # 回收确认弹窗
        wf.click_region(wf.EQUIP_DETAIL, "recycle_confirm")
        wf.wait_delay("page_refresh_wait")  # 回收完成，背包刷新补位
        if report is not None:
            report["recycled"] = True
            report["recycle_reason"] = reason
        wf.output.setdefault("recycled_items", []).append({
            "name": equip_data.name, "type": equip_data.type,
            "quality": equip_data.quality,
            "stage": stage, "reason": reason,
        })
        logger.info(f"  [{stage_label}] {label} 已回收")
        return True
