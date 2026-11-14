"""假插件任务页：只用宿主公开给插件页的执行工具，不依赖任何真实插件。

真实插件页（如 yysls 的调律页）用的是同一组宿主 API：
``plugin_runtime.hotkeys`` / ``other_task_running_label`` / ``apply_idle_start`` /
``show_running_feedback`` / ``apply_execution_button_style``。框架 UI 测试用这个
最小实现验证「宿主状态 → 插件页按钮」的契约；插件页自己的实现由插件测试覆盖。
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import QPushButton

from lvjiang.i18n import tr
from lvjiang.ui.execution.feedback import (
    ExecutionFeedback,
    apply_idle_start,
    show_running_feedback,
)
from lvjiang.ui.execution.hotkeys import hotkey_label
from lvjiang.ui.main.run_control import (
    STATE_PAUSING,
    STATE_STOPPING,
    other_task_running_label,
)
from lvjiang.ui.widgets.button_styles import apply_execution_button_style

#: 假插件代码工作流的注册名，同时是其任务页的执行范围。
FAKE_SCOPE = "demo_task"
#: 假插件工作流声明的「运行中」文案。
FAKE_RUNNING_LABEL = "示例任务运行中"


class FakeWorkflow:
    """假插件的代码工作流：占用文案由类自己声明。"""

    RUNNING_LABEL = FAKE_RUNNING_LABEL


class FakePluginPage:
    """插件左侧任务页的最小实现。"""

    execution_scope = FAKE_SCOPE

    def __init__(self, host: Any, username: str = "example"):
        self._host = host
        self.username = username
        self.btn_run = QPushButton()
        self.btn_pause_resume = QPushButton()
        self.execution_feedback = ExecutionFeedback()

    def on_automation_state(self, state: str) -> None:
        hk = self._host.plugin_runtime.hotkeys()
        other_label = other_task_running_label(self._host, self.execution_scope)
        self.btn_run.setEnabled(True)
        if other_label:
            self.btn_run.setText(other_label)
            self.btn_run.setEnabled(False)
            apply_execution_button_style(self.btn_run, "disabled")
        elif state == STATE_STOPPING:
            self.btn_run.setText(tr("停止中"))
            self.btn_run.setEnabled(False)
        elif state in ("running", STATE_PAUSING, "paused"):
            self.btn_run.setText(hotkey_label(tr("停止"), hk.stop))
        else:
            reason = tr("请选择有效的执行用户") if not self.username else ""
            apply_idle_start(self.btn_run, self.execution_feedback, self._host, state,
                             hotkey_label(tr("开始"), hk.start), reason)
        if other_label or state in ("running", STATE_PAUSING, "paused", STATE_STOPPING):
            show_running_feedback(self.execution_feedback, other_label)
        self.btn_pause_resume.setEnabled(not other_label and state in ("running", "paused"))
