"""Optional tuning subplugin; no Qt or user-state imports at registration time."""
from lvjiang.apps.base import AppHooks


def _build_tuning(host):
    from .ui.tuning.tuning_tab import TuningTab
    return TuningTab(host)


def _build_history(host):
    from .ui.tuning.management_widget import TuningManagementWidget
    return TuningManagementWidget()


hooks = AppHooks(
    id="shuijiao_tuning",
    name="水饺直观调律",
    requires_app_ids=("yysls",),
    left_tab_builders=[("水饺调律", _build_tuning)],
    right_tab_builders=[("水饺记录与统计", _build_history)],
    workflow_implementations={
        "shuijiao_tuning": "lvjiang.apps.shuijiao_tuning.workflows.implementations.auto_tuning.AutoTuningWorkflow",
    },
)
