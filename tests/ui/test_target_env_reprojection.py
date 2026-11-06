"""切换执行目标后，任务候选集必须跟随该目标自己的环境。

现场问题：手机（android）与端游（desktop）同时连接时，方案自动适配会把环境
切到 desktop，日常/批量只剩端游任务；再点回手机行，草稿把环境改回 android，
但候选集是按环境算出来的派生视图，没有任何地方跟着重建——于是手机视角继续
显示端游任务，而启动门禁又按 android 判定。

这些用例保护三条可观察契约：

1. 目标切换后日常下拉与批量候选都等于该目标环境可用的脚本；
2. 同一个目标的日常任务选择在来回切换中保持不变（切走时写入的草稿不被
   另一个目标的任务覆盖）；
3. 投影过程不经过「环境变更」那条会写持久化的路径；批量页的勾选在环境变化
   后既不丢失，也不把「当前环境不支持」误报成「脚本不可用」。
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QTreeWidgetItem

from lvjiang.core.batch_config import BatchConfigItem
from lvjiang.core.batch_run import BatchRunDraft, BatchSelection
from lvjiang.ui.batch.batch_tab import BatchTab, _ReorderTreeWidget
from lvjiang.ui.main.execution_targets import (
    ExecutionTarget,
    ExecutionTargetRegistry,
)
from lvjiang.ui.main.run_control import RunControlMixin
from lvjiang.ui.main.window_ops import WindowOpsMixin

PHONE_TASK = "phone_daily"
WINDOW_TASK = "window_daily"
SHARED_TASK = "shared_daily"

SCRIPTS = [
    {"id": PHONE_TASK, "name": "手机日常", "env": ["android"],
     "scope": "daily", "batchable": True},
    {"id": WINDOW_TASK, "name": "端游日常", "env": ["desktop"],
     "scope": "daily", "batchable": True},
    {"id": SHARED_TASK, "name": "通用日常", "env": ["android", "desktop"],
     "scope": "daily", "batchable": True},
]

# 每个用例自己的一份脚本表，避免用例之间互相污染。
_ACTIVE: list[dict] = []


def _exposed(run_env=None, *, device_entry=False):
    from lvjiang.workflows.discovery import script_supports_env
    return [deepcopy(cfg) for cfg in _ACTIVE if script_supports_env(cfg, run_env)]


@pytest.fixture
def discovery(monkeypatch):
    """把发现层换成本地脚本表；返回本用例可修改的那一份。"""
    global _ACTIVE
    _ACTIVE = [dict(cfg) for cfg in SCRIPTS]
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts", _exposed)
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.last_discovery_problems", lambda: [])
    return _ACTIVE


class _FakeBatch:
    """批量页替身：只在 refresh_scripts() 时按当时环境取一次候选快照。"""

    def __init__(self, host):
        self._host = host
        self.refreshes = 0
        self.cached: list[str] = []

    def refresh_scripts(self):
        self.refreshes += 1
        self.cached = [cfg["id"] for cfg in _exposed(self._host._selected_run_env())]


class Host(RunControlMixin, WindowOpsMixin):
    @property
    def _running(self):
        return False

    def __init__(self, qtbot):
        self._execution_targets = ExecutionTargetRegistry()
        for name in ("workflow_combo", "workflow_group_combo", "plan_combo",
                     "reference_space_combo", "_env_combo", "layout_combo"):
            combo = QComboBox()
            qtbot.addWidget(combo)
            setattr(self, name, combo)
        self.workflow_group_combo.addItem("全部分组", None)
        self._env_combo.addItem("安卓", "android")
        self._env_combo.addItem("桌面", "desktop")
        self._workflow_configs: list[dict] = []
        self._displayed_script_id = None
        self.logs: list[str] = []
        self._batch_tab = _FakeBatch(self)

    def _selected_run_env(self):
        value = self._env_combo.currentData()
        return str(value) if value is not None else ""

    def append_log(self, text):
        self.logs.append(text)

    def _show_ignored_scripts(self, problems):
        pass

    def _save_displayed_params(self):
        pass

    def _collect_displayed_params(self, parameters):
        return {}

    def _rebuild_param_panel(self):
        pass

    def _refresh_run_button(self):
        pass

    def _set_context_controls_locked(self, *args, **kwargs):
        pass


@pytest.fixture
def host(qtbot, discovery):
    return Host(qtbot)


@pytest.fixture
def two_targets(host):
    phone = ExecutionTarget("android:test", "adb", "127.0.0.1:7555")
    window = ExecutionTarget("window", "windows", "端游窗口")
    host._execution_targets.put(window)
    host._execution_targets.put(phone)
    return phone, window


def _select_workflow(host, workflow_id):
    index = host.workflow_combo.findData(workflow_id)
    assert index >= 0, f"{workflow_id} 不在当前候选集里"
    host.workflow_combo.setCurrentIndex(index)


def _candidate_ids(host):
    return [cfg["id"] for cfg in host._workflow_configs]


def _connect(host, target_id, env, workflow_id):
    """模拟「连上目标 → 环境就位 → 选任务 → 记草稿」。"""
    host._execution_targets.select(target_id)
    host._restore_launch_draft(target_id)      # 新目标没有草稿时是空投影
    host._env_combo.setCurrentIndex(host._env_combo.findData(env))
    host._load_workflow_configs()
    _select_workflow(host, workflow_id)
    host._capture_launch_draft(target_id)


def _switch_to(host, target_id):
    """模拟点击另一个已连接目标行：先记走前草稿，再投影回来的视图。"""
    host._capture_launch_draft(host._execution_targets.active_target_id)
    host._execution_targets.select(target_id)
    host._restore_launch_draft(target_id)


def test_target_switch_reprojects_task_lists_for_its_env(
        host, two_targets, monkeypatch):
    """手机 → 端游 → 手机：候选集与选中任务都要回到手机自己那份。"""
    phone, window = two_targets
    persisted: list[tuple[str, str]] = []
    monkeypatch.setattr("lvjiang.core.config.save_env",
                        lambda env: persisted.append(("env", env)))
    monkeypatch.setattr("lvjiang.core.config.plans.set_active_plan_id",
                        lambda plan_id: persisted.append(("plan", plan_id)))

    _connect(host, phone.id, "android", PHONE_TASK)
    assert _candidate_ids(host) == [PHONE_TASK, SHARED_TASK]
    android_batch = list(host._batch_tab.cached)

    _switch_to(host, window.id)
    host._env_combo.setCurrentIndex(host._env_combo.findData("desktop"))
    host._load_workflow_configs()
    _select_workflow(host, WINDOW_TASK)
    host._capture_launch_draft(window.id)
    assert _candidate_ids(host) == [WINDOW_TASK, SHARED_TASK]
    refreshes_before = host._batch_tab.refreshes

    # 回到手机：这一直是缺陷所在——环境改了，候选集没重建。
    _switch_to(host, phone.id)
    assert host._selected_run_env() == "android"
    assert _candidate_ids(host) == [PHONE_TASK, SHARED_TASK]
    assert host.workflow_combo.currentData() == PHONE_TASK
    assert host._batch_tab.cached == android_batch
    assert host._batch_tab.refreshes == refreshes_before + 1

    # 再走一轮，手机的选择不能被端游任务覆盖。
    _switch_to(host, window.id)
    assert _candidate_ids(host) == [WINDOW_TASK, SHARED_TASK]
    assert host.workflow_combo.currentData() == WINDOW_TASK
    assert phone.launch_draft.workflow_id == PHONE_TASK
    assert phone.launch_draft.environment == "android"
    assert window.launch_draft.workflow_id == WINDOW_TASK

    _switch_to(host, phone.id)
    assert host.workflow_combo.currentData() == PHONE_TASK
    assert phone.launch_draft.workflow_id == PHONE_TASK
    # 投影是纯视图动作：不得经过会写 interface 的环境变更链路。
    assert persisted == []


def test_same_env_switch_keeps_candidates_and_per_target_selection(
        host, two_targets):
    """同环境目标互切不重建候选集，也不串掉各自的选中任务。"""
    phone, _window = two_targets
    other_phone = ExecutionTarget("android:other", "adb", "other")
    host._execution_targets.put(other_phone)

    _connect(host, phone.id, "android", PHONE_TASK)
    _switch_to(host, other_phone.id)
    _select_workflow(host, SHARED_TASK)
    host._capture_launch_draft(other_phone.id)
    refreshes_before = host._batch_tab.refreshes
    candidates_before = _candidate_ids(host)

    _switch_to(host, phone.id)
    assert _candidate_ids(host) == candidates_before
    assert host.workflow_combo.currentData() == PHONE_TASK
    _switch_to(host, other_phone.id)
    assert host.workflow_combo.currentData() == SHARED_TASK
    # 环境没变就不该重新扫描脚本、也不该刷新批量候选。
    assert host._batch_tab.refreshes == refreshes_before


def test_batch_checked_selection_survives_env_switch_without_false_warning(
        host, discovery, qtbot):
    """批量勾选在环境来回切换后保留，且不把环境过滤误报成脚本不可用。"""
    batch = _make_batch(host, qtbot)
    host._batch_tab = batch

    host._env_combo.setCurrentIndex(host._env_combo.findData("desktop"))
    batch._refresh_script_list()
    assert _visible_ids(batch) == [WINDOW_TASK, SHARED_TASK]
    assert _checked_rows(batch) == [WINDOW_TASK, SHARED_TASK]

    host.logs.clear()
    host._env_combo.setCurrentIndex(host._env_combo.findData("android"))
    batch._refresh_script_list()
    assert _visible_ids(batch) == [PHONE_TASK, SHARED_TASK]
    assert batch._env_filtered_script_ids == [(0, WINDOW_TASK)]
    assert batch._missing_script_ids == []
    assert host.logs == [], "环境不支持的勾选不是脚本缺失，不该报警"

    # 在手机视角里改一次勾选：写回时必须把端游条目原位带上。
    batch._persist_script_order()
    assert set(batch._draft.tasks.checked) == {WINDOW_TASK, SHARED_TASK}

    host._env_combo.setCurrentIndex(host._env_combo.findData("desktop"))
    batch._refresh_script_list()
    # 行序会随「本次顺序」漂移（不可见条目排在可见条目之后），但勾选不能丢。
    assert set(_checked_rows(batch)) == {WINDOW_TASK, SHARED_TASK}
    assert batch._env_filtered_script_ids == []

    # 真缺失仍然必须报警：直接从发现层拿掉端游任务。
    discovery[:] = [cfg for cfg in discovery if cfg["id"] != WINDOW_TASK]
    host.logs.clear()
    host._env_combo.setCurrentIndex(host._env_combo.findData("android"))
    batch._refresh_script_list()
    assert batch._missing_script_ids
    assert any(WINDOW_TASK in line for line in host.logs)


def _make_batch(host, qtbot):
    tree = _ReorderTreeWidget()
    qtbot.addWidget(tree)
    tree.setColumnCount(2)
    batch = SimpleNamespace(
        _host=host,
        _script_list=tree,
        _item=BatchConfigItem(
            id="group", name="测试组",
            task_ids=[PHONE_TASK, WINDOW_TASK, SHARED_TASK]),
        # 端游视角里勾好：端游任务 + 通用任务。
        _draft=BatchRunDraft(tasks=BatchSelection(
            order=[PHONE_TASK, WINDOW_TASK, SHARED_TASK],
            checked=[WINDOW_TASK, SHARED_TASK])),
        _missing_script_ids=[],
        _env_filtered_script_ids=[],
        _updating_script_list=False,
        _group_id="group",
    )
    for name in ("_script_id", "_refresh_script_list", "_warn_missing_scripts",
                 "_refresh_script_order_column", "_merged_script_ids",
                 "_unavailable_script_ids", "_persist_script_order"):
        setattr(batch, name, getattr(BatchTab, name).__get__(batch))
    batch._refresh_start_feedback = lambda: None
    batch._save_draft = lambda: None
    return batch


def _visible_ids(batch) -> list[str]:
    return [batch._script_id(batch._script_list.topLevelItem(index))
            for index in range(batch._script_list.topLevelItemCount())]


def _checked_rows(batch) -> list[str]:
    rows: list[str] = []
    for index in range(batch._script_list.topLevelItemCount()):
        item: QTreeWidgetItem = batch._script_list.topLevelItem(index)
        if item.checkState(0) == Qt.CheckState.Checked:
            rows.append(batch._script_id(item))
    return rows
