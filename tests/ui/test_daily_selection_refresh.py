from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtWidgets import QComboBox

import lvjiang.workflows.discovery as discovery
from lvjiang.ui.main.window import MainWindow


def test_environment_refresh_preserves_selected_daily_workflow(qtbot, monkeypatch):
    workflow_combo = QComboBox()
    env_combo = QComboBox()
    qtbot.addWidget(workflow_combo)
    qtbot.addWidget(env_combo)
    env_combo.addItem("桌面", "desktop")
    workflow_combo.addItem("任务一", "task_1")
    workflow_combo.addItem("任务二", "task_2")
    workflow_combo.setCurrentIndex(1)
    configs = [
        {"id": "task_1", "name": "任务一", "env": ["android"]},
        {"id": "task_2", "name": "任务二", "env": ["desktop"]},
    ]
    monkeypatch.setattr(discovery, "list_exposed_scripts", lambda: configs)
    host = SimpleNamespace(
        workflow_combo=workflow_combo,
        _env_combo=env_combo,
        _displayed_script_id="task_2",
        _batch_tab=None,
    )
    host._selected_run_env = lambda: MainWindow._selected_run_env(host)
    host._get_selected_flow_config = lambda: MainWindow._get_selected_flow_config(host)

    MainWindow._load_workflow_configs(host)

    assert workflow_combo.currentData() == "task_2"
    assert host._displayed_script_id == "task_2"
    assert workflow_combo.itemText(0) == "任务一 (环境不支持)"
