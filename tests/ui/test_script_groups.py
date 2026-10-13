"""分组只改变候选展示，刷新不重置选择、默认项或批量运行草稿。"""

from copy import deepcopy
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTreeWidget, QWidget

from lvjiang.core.batch_config import BatchConfigItem
from lvjiang.core.batch_run import BatchRunDraft, BatchSelection
from lvjiang.ui.batch.batch_tab import BatchTab
from lvjiang.ui.combo_box import AutoWidthComboBox
from lvjiang.ui.main.run_control import RunControlMixin
from lvjiang.ui.main.ui_state import UiStateMixin
from lvjiang.ui.scripts.metadata_panel import MetadataPanel
from lvjiang.workflows.metadata import parse_metadata


class Host(QWidget, RunControlMixin):
    def __init__(self):
        super().__init__()
        self.workflow_combo = AutoWidthComboBox(self)
        self.workflow_group_combo = AutoWidthComboBox(self)
        self.workflow_group_combo.addItem("全部分组", None)
        self._workflow_configs = []
        self._displayed_script_id = None
        self.default_script = "a"
        self.saved_scripts = []
        self.rebuilt_scripts = []
        self.batch_refreshes = 0
        self._batch_tab = SimpleNamespace(refresh_scripts=self.refresh_batch)
        self.workflow_group_combo.currentIndexChanged.connect(self._on_workflow_group_changed)
        self.workflow_combo.currentIndexChanged.connect(self.persist_selection)

    def _selected_run_env(self):
        return "android"

    def _show_ignored_scripts(self, _problems):
        pass

    def _save_displayed_params(self):
        self.saved_scripts.append(self._displayed_script_id)

    def _rebuild_param_panel(self):
        self.rebuilt_scripts.append(self._displayed_script_id)

    def persist_selection(self, _index):
        self.default_script = self.workflow_combo.currentData()

    def _save_daily_config(self):
        self.persist_selection(0)

    def refresh_batch(self):
        self.batch_refreshes += 1


class DailyHost(Host):
    _save_daily_config = UiStateMixin._save_daily_config
    _restore_daily_config = UiStateMixin._restore_daily_config

    def persist_selection(self, _index):
        self._save_daily_config()


def test_daily_group_and_explicit_script_restore_without_persisting_filter_fallback(qtbot, monkeypatch):
    from lvjiang.core.config.interface import get_interface_store

    scripts = [
        {"id": "a", "name": "甲", "group": "日常"},
        {"id": "b", "name": "乙", "group": "周常"},
        {"id": "c", "name": "丙", "group": "周常"},
    ]
    monkeypatch.setattr("lvjiang.workflows.discovery.list_exposed_scripts", lambda _env: deepcopy(scripts))
    monkeypatch.setattr("lvjiang.workflows.discovery.last_discovery_problems", lambda: [])
    store = get_interface_store()
    store.set_node("daily", {"workflow_id": "a", "group": None})
    first = DailyHost()
    qtbot.addWidget(first)
    first._load_workflow_configs()
    first._restore_daily_config()
    first.workflow_group_combo.setCurrentIndex(first.workflow_group_combo.findData("周常"))
    first._save_daily_config(save_workflow=False)
    assert store.get_node("daily") == {"workflow_id": "a", "group": "周常"}
    first.workflow_combo.setCurrentIndex(first.workflow_combo.findData("c"))
    assert store.get_node("daily") == {"workflow_id": "c", "group": "周常"}

    second = DailyHost()
    qtbot.addWidget(second)
    second._load_workflow_configs()
    second._restore_daily_config()
    assert second.workflow_group_combo.currentData() == "周常"
    assert second.workflow_combo.currentData() == "c"
    second._on_refresh_workflows()
    assert store.get_node("daily") == {"workflow_id": "c", "group": "周常"}


def test_filter_and_refresh_use_stable_ids_without_persisting_fallback(qtbot, monkeypatch):
    scripts = [
        {"id": "a", "name": "甲", "group": "日常", "batchable": True},
        {"id": "b", "name": "乙", "group": "周常", "batchable": True},
    ]
    monkeypatch.setattr("lvjiang.workflows.discovery.list_exposed_scripts", lambda _env: deepcopy(scripts))
    monkeypatch.setattr("lvjiang.workflows.discovery.last_discovery_problems", lambda: [])
    host = Host()
    qtbot.addWidget(host)
    tree = QTreeWidget(host)
    tree.setColumnCount(2)
    batch = SimpleNamespace(
        _host=host, _script_list=tree,
        _item=BatchConfigItem(id="group", name="测试组", task_ids=["a", "b"]),
        _draft=BatchRunDraft(tasks=BatchSelection(order=["b", "a"], checked=["b"])),
    )
    batch._script_id = BatchTab._script_id.__get__(batch)
    batch._warn_missing_scripts = BatchTab._warn_missing_scripts.__get__(batch)
    batch._refresh_script_order_column = BatchTab._refresh_script_order_column.__get__(batch)
    batch._refresh_script_list = BatchTab._refresh_script_list.__get__(batch)
    batch.refresh_scripts = BatchTab.refresh_scripts.__get__(batch)
    host._batch_tab = batch
    host._load_workflow_configs()
    before = deepcopy(batch._draft)
    host.workflow_group_combo.setCurrentIndex(host.workflow_group_combo.findData("周常"))
    assert host._get_selected_flow_config()["id"] == "b"
    assert host.default_script == "a"
    assert host.saved_scripts == ["a"]
    assert host.rebuilt_scripts == ["b"]
    scripts[1]["name"] = "乙更新"
    host._on_refresh_workflows()
    assert host.workflow_combo.currentText() == "乙更新"
    assert host.workflow_group_combo.currentData() == "周常"
    assert host.default_script == "a"
    assert batch._draft == before
    assert [tree.topLevelItem(i).text(0) for i in range(2)] == ["乙更新", "甲"]
    assert tree.topLevelItem(0).checkState(0) == Qt.CheckState.Checked
    assert tree.topLevelItem(1).checkState(0) == Qt.CheckState.Unchecked
    scripts.pop()
    host._on_refresh_workflows()
    assert host.workflow_group_combo.currentData() is None
    assert host._get_selected_flow_config()["id"] == "a"
    assert host.default_script == "a"


def test_refresh_retains_temporary_external_script(qtbot, monkeypatch):
    monkeypatch.setattr("lvjiang.workflows.discovery.list_exposed_scripts", lambda _env: [])
    monkeypatch.setattr("lvjiang.workflows.discovery.last_discovery_problems", lambda: [])
    host = Host()
    qtbot.addWidget(host)
    cfg = {"id": "__loaded__:example", "name": "临时脚本", "group": ""}
    host._workflow_configs = [cfg]
    host.workflow_combo.addItem(cfg["name"], cfg["id"])
    host._on_refresh_workflows()
    assert host._get_selected_flow_config() == cfg
    assert host.workflow_group_combo.findData("") >= 0


def test_metadata_form_roundtrip_and_edit_group(qtbot, monkeypatch):
    monkeypatch.setattr("lvjiang.ui.scripts.metadata_panel.load_available_envs", lambda: [])
    panel = MetadataPanel()
    qtbot.addWidget(panel)
    text = '#% id: example\n#% group: 周常\n#% runnable: true\nlog "正文"\n'
    panel.load_text(text, editable=True, fallback_id="example")
    assert panel.edit_group.isEnabled()
    assert not panel.has_content_draft()
    panel.edit_group.setText("材料")
    with qtbot.waitSignal(panel.text_applied) as signal:
        panel._apply()
    assert parse_metadata(signal.args[0])["group"] == "材料"
    assert 'log "正文"' in signal.args[0]


def test_external_load_replaces_temporary_item_even_when_group_filtered(qtbot, monkeypatch, tmp_path):
    host = Host()
    qtbot.addWidget(host)
    host.log_text = SimpleNamespace(append=lambda _text: None)
    host._workflow_configs = [
        {"id": "a", "name": "甲", "group": "日常"},
        {"id": "__loaded__:old", "name": "旧临时", "group": "周常"},
    ]
    host._refresh_workflow_groups()
    host.workflow_group_combo.setCurrentIndex(host.workflow_group_combo.findData("日常"))
    path = tmp_path / "new.wf"
    path.write_text('#% name: 新临时\n#% group: 材料\nlog "正文"\n', encoding="utf-8")
    monkeypatch.setattr("PyQt6.QtWidgets.QFileDialog.getOpenFileName", lambda *_args: (str(path), ""))
    monkeypatch.setattr("lvjiang.ui.main.run_control.get_resolver",
                        lambda: SimpleNamespace(write_dir=lambda _name: tmp_path))
    host._on_load_workflow()
    assert host._get_selected_flow_config()["id"] == "__loaded__:new"
    assert host._get_selected_flow_config()["group"] == "材料"
    assert [cfg["id"] for cfg in host._workflow_configs] == ["a", "__loaded__:new"]
    assert host.workflow_group_combo.currentData() is None
    assert host.saved_scripts[-1] == "a"
