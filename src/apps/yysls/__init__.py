"""燕云十六声（YYSLS）插件。

阶段 5：登记完整扩展点 —— 主窗口类、识别器、工作流实现、内置函数模块。
燕云专属的 Tab / 菜单目前由燕云 MainWindow 自建，后续可进一步拆分为
独立的 builder 以注入到通用 MainWindow。
"""
from __future__ import annotations

from ..base import AppHooks

hooks = AppHooks(
    name="燕云十六声",
    window_title="律匠 - 燕云十六声装备调律工具 v0.1.0",
    # 使用字符串路径延迟导入，避免 import 时触发 PyQt6
    main_window_class="src.apps.yysls.ui.main_window.MainWindow",

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
        "src.apps.yysls.workflows.builtins.bag_traverse",
    ],
)
