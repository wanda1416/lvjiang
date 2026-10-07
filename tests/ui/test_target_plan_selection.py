"""新连接的方案适配与各目标编辑态隔离。"""
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QComboBox

from lvjiang.core.config.plans import Plan
from lvjiang.ui.main.execution_targets import (
    ExecutionTarget,
    ExecutionTargetRegistry,
    LaunchDraft,
)
from lvjiang.ui.main.run_control import RunControlMixin
from lvjiang.ui.main.window_ops import WindowOpsMixin


@pytest.fixture
def host(qtbot, monkeypatch):
    class Host(RunControlMixin, WindowOpsMixin):
        @property
        def _running(self):
            return self.running

        def _capture_launch_draft(self, target_id):
            self._execution_targets.get(target_id).launch_draft = LaunchDraft(
                plan_id=self.plan_combo.currentData())

    host = Host()
    host.running = False
    host._execution_targets = ExecutionTargetRegistry()
    host._execution_targets.put(ExecutionTarget('window', 'windows', '窗口'))
    plans = [Plan('mobile', '移动方案', modes=['adb']),
             Plan('missing', '缺失布局', layout='deleted', modes=['windows']),
             Plan('desktop', '桌面方案', space='PC', env='pc', layout='pc',
                  modes=['windows'])]
    monkeypatch.setattr('lvjiang.core.config.plans.load_plans', lambda: plans)
    # 自动选择不得改变持久化默认方案。
    def reject_persist(value):
        raise AssertionError('自动适配写入全局默认')
    monkeypatch.setattr('lvjiang.core.config.plans.set_active_plan_id', reject_persist)
    for name in ('plan_combo', 'reference_space_combo', '_env_combo', 'layout_combo'):
        combo = QComboBox()
        qtbot.addWidget(combo)
        setattr(host, name, combo)
    host.plan_combo.addItem('自定义', '')
    for plan in plans:
        host.plan_combo.addItem(plan.name, plan.id)
    host.plan_combo.setCurrentIndex(1)
    host.reference_space_combo.addItems(['mobile', 'PC'])
    host._env_combo.addItem('移动', 'mobile')
    host._env_combo.addItem('桌面', 'pc')
    host.layout_combo.addItem('移动', 'mobile')
    host.layout_combo.addItem('桌面', 'pc')
    host.log_text = SimpleNamespace(append=lambda message: None)
    host._set_context_controls_locked = lambda *args: None
    host._refresh_run_button = lambda: None
    return host


def test_incompatible_plan_switches_with_its_full_context(host):
    host._ensure_target_plan()
    assert host.plan_combo.currentData() == 'desktop'
    assert host.reference_space_combo.currentText() == 'PC'
    assert host._env_combo.currentData() == 'pc'
    assert host.layout_combo.currentData() == 'pc'
    assert host._execution_targets.active().launch_draft.plan_id == 'desktop'
    # 已适配方案不被后续切换覆盖。
    host._ensure_target_plan()
    assert host.plan_combo.currentData() == 'desktop'


def test_running_target_and_custom_context_are_preserved(host):
    host.running = True
    host._ensure_target_plan()
    assert host.plan_combo.currentData() == 'mobile'
    host.running = False
    host.plan_combo.setCurrentIndex(0)
    host._ensure_target_plan()
    assert host.plan_combo.currentData() == ''


def test_no_compatible_plan_keeps_selection_and_explains(host, monkeypatch):
    monkeypatch.setattr('lvjiang.core.config.plans.load_plans',
                        lambda: [Plan('mobile', '移动方案', modes=['adb'])])
    messages = []
    host.log_text.append = messages.append
    host._ensure_target_plan()
    assert host.plan_combo.currentData() == 'mobile'
    assert len(messages) == 1
    assert host._execution_targets.active().launch_draft is None


def test_new_connection_selects_target_and_keeps_previous_draft(host):
    registry = host._execution_targets
    window = registry.active()
    host.plan_combo.setCurrentIndex(3)
    phone = ExecutionTarget('android:test', 'adb', '设备')
    old = registry.put(phone)
    restored = []
    host._restore_active_target_view = lambda: restored.append(registry.active_target_id)
    host._activate_connected_target(phone, old)
    assert registry.active() is phone
    assert window.launch_draft.plan_id == 'desktop'
    assert restored == [phone.id]
    replacement = ExecutionTarget(phone.id, 'adb', '设备重连')
    phone.launch_draft = LaunchDraft(plan_id='mobile')
    registry.select(window.id)
    old = registry.put(replacement)
    host._sync_active_target_compat = lambda: None
    host._activate_connected_target(replacement, old)
    assert registry.active() is window
    assert replacement.launch_draft.plan_id == 'mobile'
    assert restored == [phone.id]
