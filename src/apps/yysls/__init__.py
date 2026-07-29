"""燕云十六声（YYSLS）插件。

通过 AppHooks 向通用 MainWindow 注入 Tab / 菜单，并登记识别器、
工作流实现、内置函数模块。builder 为顶层轻函数，函数体内延迟
导入实际类，保持「插件 import 不触发 PyQt6」约定。
"""
from __future__ import annotations

from ..base import AppHooks


def _build_tuning_tab(host):
    from .ui.tuning_tab import TuningTab
    return TuningTab(host)


def _build_equip_status_tab(host):
    from .ui.equip_status_tab import EquipStatusTab
    return EquipStatusTab(host)


def _build_menu(host, menubar):
    from .ui.menus import build_menu
    build_menu(host, menubar)


hooks = AppHooks(
    name="燕云十六声",
    window_title="律匠 - 燕云十六声装备调律工具 v0.1.0",

    # 注入通用 MainWindow 的 Tab / 菜单
    left_tab_builders=[("调律", _build_tuning_tab)],
    right_tab_builders=[("装备状态", _build_equip_status_tab)],
    menu_builders=[_build_menu],

    # 识别器：燕云材料识别器（模板匹配 + OCR）
    recognizer_classes=[],  # 阶段 5 暂不注册，留待后续接入

    # 复杂工作流实现
    workflow_implementations={
        "auto_tuning": "src.apps.yysls.workflows.implementations.auto_tuning.AutoTuningWorkflow",
        "single_tuning": "src.apps.yysls.workflows.implementations.single_tuning.SingleTuningWorkflow",
    },

    # 燕云专属内置函数模块（导入即触发 @builtin_func 注册）
    builtin_modules=[
        "src.apps.yysls.workflows.builtins.equipment",
        "src.apps.yysls.workflows.builtins.bag_traversal",
    ],
)
