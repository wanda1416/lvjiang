"""导航与装备操作 — TuningNavigator

页面导航（主界面→背包、详情页→调律页）与词条收集。
导航序列的唯一事实来源是 DSL subcall 文件（config/system/workflows/
subcall/），经引擎 load_subcalls / call_subcall 桥调用，避免 Python 与
DSL 两处重复维护同一操作序列。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from lvjiang.apps.yysls.core.equip_parser import EquipmentData, get_equipment_parser

if TYPE_CHECKING:
    from lvjiang.apps.yysls.workflows.implementations.auto_tuning import (
        AutoTuningWorkflow,
    )

# 导航 subcall 文件（相对 workflows 根）与过程名
_NAV_FILE = "subcall/navigation.wf"
_NAV_MAIN_TO_EQUIP = (_NAV_FILE, "nav_main_to_equip")
_NAV_EQUIP_TO_TUNE = (_NAV_FILE, "nav_equip_to_tune")
_NAV_BACK_TO_MAIN = (_NAV_FILE, "nav_back_to_main")


class TuningNavigator:
    """导航与装备操作：页面跳转、调律入口、词条收集"""

    def __init__(self, wf: AutoTuningWorkflow):
        self._wf = wf

    def load_dependencies(self) -> None:
        """加载导航所需的 DSL subcall 文件

        在工作流启动时调用一次，确保后续导航操作能正确执行。
        每次运行都重新加载，保证文件修改立即生效。
        """
        engine = self._wf.engine
        if engine is None:
            raise RuntimeError(
                "TuningNavigator.load_dependencies: 未注入 WorkflowEngine，"
                "无法加载导航 subcall")
        engine.load_subcalls(_NAV_FILE)

    def _call_subcall(self, proc_name: str, args: list | None = None):
        """经引擎桥调用已加载的 DSL 子过程"""
        engine = self._wf.engine
        if engine is None:
            raise RuntimeError(
                f"TuningNavigator.{proc_name}: 未注入 WorkflowEngine，"
                "导航 subcall 无法执行")
        return engine.call_subcall(proc_name, args)

    def navigate_to_equip(self):
        """从主界面导航到背包装备页（DSL subcall nav_main_to_equip）"""
        self._call_subcall(_NAV_MAIN_TO_EQUIP[1])

    def navigate_back(self):
        """从背包详情页返回主界面（DSL subcall nav_back_to_main）"""
        self._call_subcall(_NAV_BACK_TO_MAIN[1])

    def nav_to_tune(self) -> bool:
        """从装备详情页进入调律页（DSL subcall nav_equip_to_tune）

        失败时停留在详情页（弹窗已收起）并返回 False；返回值语义遵循
        DSL 约定：子过程 return < 0 表示错误。
        """
        result = self._call_subcall(_NAV_EQUIP_TO_TUNE[1])
        return not (isinstance(result, (int, float)) and result < 0)

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
