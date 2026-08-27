"""燕云十六声（YYSLS）插件。

通过 AppHooks 向通用 MainWindow 注入 Tab / 菜单，并登记识别器、
工作流实现、内置函数模块。builder 为顶层轻函数，函数体内延迟
导入实际类，保持「插件 import 不触发 PyQt6」约定。
"""
from __future__ import annotations

from ..._version import __version__
from ...i18n import tr
from ..base import AppHooks, TelemetryDisclosure


def _build_tuning_tab(host):
    from .ui.tuning import TuningTab
    return TuningTab(host)


def _build_loadout_panel(host):
    from .ui.loadout import LoadoutPanel
    return LoadoutPanel(host)


def _build_profile_overview_tab(host):
    from .ui.profile import ProfileOverviewTab
    _ensure_engine_started(host)
    return ProfileOverviewTab(host)


def _build_profile_tab(host):
    from .ui.profile import ProfileTab
    _ensure_engine_started(host)
    return ProfileTab(host)


def _build_tuning_progress_tab(host):
    from .ui.tuning.progress_widget import TuningProgressWidget
    return TuningProgressWidget()


def _ensure_engine_started(host):
    """确保 ProfileEngine 已启动（首次构建 profile Tab 时调用）"""
    from .core.profile_engine.profile_engine import get_or_create_engine, stop_engine
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


def _quality_stylesheet(tokens):
    from .ui.theme import equipment_quality_stylesheet
    return equipment_quality_stylesheet(tokens)


hooks = AppHooks(
    id="yysls",
    name=tr("燕云十六声"),
    window_title=tr("律匠 - 燕云十六声装备调律工具") + f" v{__version__}",

    # 注入通用 MainWindow 的 Tab / 菜单
    left_tab_builders=[(tr("调律"), _build_tuning_tab)],
    right_tab_builders=[
        (tr("用户总览"), _build_profile_overview_tab),
        (tr("备战方案"), _build_loadout_panel),
        (tr("其他信息"), _build_profile_tab),
        (tr("调律进度"), _build_tuning_progress_tab),
    ],
    menu_builders=[_build_menu],
    theme_stylesheet_builders=[_quality_stylesheet],

    # 通用 ReferenceRecognizer 由 core 提供，无需注册业务识别器。
    recognizer_classes=[],

    # 复杂工作流实现
    workflow_implementations={
        "auto_tuning": "lvjiang.apps.yysls.workflows.implementations.auto_tuning.AutoTuningWorkflow",
    },

    # 燕云专属内置函数模块（导入即触发 @builtin_func 注册）
    builtin_modules=[
        "lvjiang.apps.yysls.workflows.builtins.equipment",
        "lvjiang.apps.yysls.workflows.builtins.bag_funcs",
        "lvjiang.apps.yysls.workflows.builtins.equip_funcs",
        "lvjiang.apps.yysls.workflows.builtins.equipment_ingest",
        "lvjiang.apps.yysls.workflows.builtins.profile_funcs",
        "lvjiang.apps.yysls.workflows.builtins.role_attr_ingest",
    ],

    # 统计事件 schema（导入即触发 register_schema 注册）
    telemetry_modules=[
        "lvjiang.apps.yysls.telemetry.schemas",
    ],
    telemetry_disclosures=[TelemetryDisclosure(
        title=tr("装备调律过程"),
        purpose=tr("用于改进律匠内置的调律规则"),
        collected=(tr(
            "装备部位、等级品阶、启用规则、初始词条、逐轮材料与产出、停止原因和最终评级"
        ),),
        excluded=(tr("游戏账号、角色名、装备名称、装备指纹、截图和日志"),),
        schema_names=("yysls.tuning_session",),
    )],

    # 配置合并策略（导入即触发 register_registry_list_paths /
    # register_protected_list_paths 注册）
    config_policy_modules=[
        "lvjiang.apps.yysls.config.merge_policy",
    ],
)
