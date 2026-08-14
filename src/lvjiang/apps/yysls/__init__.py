"""燕云十六声（YYSLS）插件。

通过 AppHooks 向通用 MainWindow 注入 Tab / 菜单，并登记识别器、
工作流实现、内置函数模块。builder 为顶层轻函数，函数体内延迟
导入实际类，保持「插件 import 不触发 PyQt6」约定。
"""
from __future__ import annotations

from ..._version import __version__
from ...i18n import tr
from ..base import AppHooks


def _build_tuning_tab(host):
    from .ui.tuning_tab import TuningTab
    return TuningTab(host)


def _build_equip_status_tab(host):
    from .ui.equip_status_tab import EquipStatusTab
    return EquipStatusTab(host)


def _build_character_detail_tab(host):
    from .ui.character_detail_tab import CharacterDetailTab
    return CharacterDetailTab(host)


def _build_profile_overview_tab(host):
    from .ui.profile import ProfileOverviewTab
    _ensure_engine_started(host)
    return ProfileOverviewTab(host)


def _build_profile_tab(host):
    from .ui.profile import ProfileTab
    _ensure_engine_started(host)
    return ProfileTab(host)


def _ensure_engine_started(host):
    """确保 ProfileEngine 已启动（首次构建 profile Tab 时调用）"""
    from .profile.profile_engine import get_or_create_engine, stop_engine
    engine = get_or_create_engine(
        user_manager=host.user_manager,
        session_manager=host.session_manager,
    )
    if not engine.isRunning():
        engine.start()
        # 仅在首次启动时注册清理回调，避免重复注册
        host.register_cleanup(stop_engine)


def _build_menu(host, menubar):
    from .ui.menus import build_menu
    build_menu(host, menubar)


hooks = AppHooks(
    name=tr("燕云十六声"),
    window_title=tr("律匠 - 燕云十六声装备调律工具") + f" v{__version__}",

    # 注入通用 MainWindow 的 Tab / 菜单
    left_tab_builders=[(tr("调律"), _build_tuning_tab)],
    right_tab_builders=[
        (tr("用户总览"), _build_profile_overview_tab),
        (tr("角色详情"), _build_character_detail_tab),
        (tr("装备数据"), _build_equip_status_tab),
        (tr("其他信息"), _build_profile_tab),
    ],
    menu_builders=[_build_menu],

    # 识别器：燕云材料识别器（模板匹配 + OCR）
    recognizer_classes=[],  # 阶段 5 暂不注册，留待后续接入

    # 复杂工作流实现
    workflow_implementations={
        "auto_tuning": "lvjiang.apps.yysls.workflows.implementations.auto_tuning.AutoTuningWorkflow",
    },

    # 燕云专属内置函数模块（导入即触发 @builtin_func 注册）
    builtin_modules=[
        "lvjiang.apps.yysls.workflows.builtins.equipment",
        "lvjiang.apps.yysls.workflows.builtins.bag_funcs",
        "lvjiang.apps.yysls.workflows.builtins.equip_funcs",
        "lvjiang.apps.yysls.workflows.builtins.profile_funcs",
    ],
)
