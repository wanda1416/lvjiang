"""导航与装备操作 — TuningNavigator

页面导航（主界面→背包、详情页→调律页）与词条收集。
通过 wf 引用访问 UI 操作原语。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from lvjiang.apps.yysls.equip_parser import EquipmentData, get_equipment_parser

if TYPE_CHECKING:
    from lvjiang.apps.yysls.workflows.implementations.auto_tuning import (
        AutoTuningWorkflow,
    )


class TuningNavigator:
    """导航与装备操作：页面跳转、调律入口、词条收集"""

    def __init__(self, wf: AutoTuningWorkflow):
        self._wf = wf

    def navigate_to_equip(self):
        """从主界面导航到背包装备页"""
        wf = self._wf
        result = wf.ocr_scene("game_menu_page", ["wulinlu"])
        if result.get("wulinlu") != "武林录":
            wf.click_region("game_main_page", "menu")
            wf.wait_delay("page_refresh_wait")  # 主界面 → 菜单页
        else:
            logger.info("当前在菜单页")

        wf.click_region("game_menu_page", "baoguo")
        wf.wait_delay("page_refresh_wait")  # 菜单页 → 背包页

        bag_scan = wf.ocr_scene("bag_equip_detail", ["sub_equip"])
        if "装备" not in bag_scan.get("sub_equip", ""):
            wf.click_region("bag_equip_detail", "peiyang")
            wf.wait_delay("page_refresh_wait")  # 背包页 → 调律训练页

    def navigate_back(self):
        """从背包详情页返回主界面"""
        wf = self._wf
        wf.click_region("bag_equip_detail", "back")
        wf.wait_delay("page_refresh_wait")  # 背包详情页 → 菜单页
        wf.click_region("game_menu_page", "back")
        wf.wait_delay("page_refresh_wait")  # 菜单页 → 主界面

    def nav_to_tune(self, detail_scene: str) -> bool:
        """从装备详情页进入调律页，失败时停留在详情页并返回 False"""
        wf = self._wf
        wf.click_region(wf.EQUIP_DETAIL, "more_func")
        wf.wait_delay("page_refresh_wait")  # 详情页 → 「更多」弹窗展开
        tune_key = wf.ocr_scene_by(
            wf.EQUIP_DETAIL,
            ["sub_func_1", "sub_func_2", "sub_func_3", "sub_func_4"],
            "调律", "contains")
        if not tune_key:
            logger.info("未找到调律按钮")
            # 「更多」弹窗已开，再点一次 more_func 使其收起，保持背包页干净
            wf.click_region(wf.EQUIP_DETAIL, "more_func")
            wf.wait_delay("step_interval")
            return False
        wf.click_region(wf.EQUIP_DETAIL, tune_key)
        wf.wait_delay("page_refresh_wait")  # 详情页 → 调律页（页面切换）
        return True

    def collect_new_affix(self, equip_data: EquipmentData, text: str) -> str:
        """把调律结果的新词条补充进装备数据，供下一轮判定使用

        Returns:
            新词条展示文本（供说明文档）；无法解析时原样返回 OCR
            文本并标注未能解析。
        """
        affix = get_equipment_parser().parse_affix_text(text, equip_data.level)
        if affix is None:
            logger.warning(f"调律结果词条无法解析: {text!r}，该槽位按未知处理")
            return f"{text}（未能解析）"
        equip_data.affixes.append(affix)
        equip_data.extra_data["affix_count"] = len(equip_data.affixes)
        pct = f"（{affix.cap_pct}%）" if affix.cap_pct is not None else ""
        desc = f"{affix.name} {affix.value}{affix.unit or ''}{pct}"
        logger.info(f"新词条: {desc}")
        return desc
