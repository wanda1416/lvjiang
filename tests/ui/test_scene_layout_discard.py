"""场景编辑器一键放弃当前布局的未保存改动。"""

from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtWidgets import QComboBox, QMessageBox

from lvjiang.core.layout_config import LayoutEntry
from lvjiang.core.layout_models import Layout
from lvjiang.ui.scene_editor.layout_ops import LayoutOpsMixin


def _host(saved_layout: Layout):
    host = SimpleNamespace()
    host._current_layout = Layout(key=saved_layout.key, name=saved_layout.name)
    host._dirty_scenes = {"scene_a"}
    host._tabs = {"scene_a": Mock()}
    host._manager = Mock()
    host._manager.load_layout.return_value = saved_layout
    host._status_bar = Mock()
    host._get_dirty_scene_names = Mock(return_value="场景 A")
    host._apply_layout_to_tabs = Mock()
    host._update_ui_state = Mock()
    return host


def test_discard_layout_changes_reloads_and_clears_pending(monkeypatch):
    saved = Layout(key="desktop", name="desktop")
    host = _host(saved)
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    LayoutOpsMixin._on_discard_layout_changes(host)

    host._manager.load_layout.assert_called_once_with("desktop")
    host._tabs["scene_a"].clear_pending_templates.assert_called_once_with()
    host._tabs["scene_a"].clear_pending_versions.assert_called_once_with()
    assert host._current_layout is saved
    host._apply_layout_to_tabs.assert_called_once_with()
    host._update_ui_state.assert_called_once_with()


def test_discard_layout_changes_cancel_keeps_pending(monkeypatch):
    host = _host(Layout(key="desktop", name="desktop"))
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )

    LayoutOpsMixin._on_discard_layout_changes(host)

    host._manager.load_layout.assert_not_called()
    host._tabs["scene_a"].clear_pending_templates.assert_not_called()
    host._apply_layout_to_tabs.assert_not_called()


def test_discard_load_failure_preserves_pending(monkeypatch):
    host = _host(Layout(key="desktop", name="desktop"))
    host._manager.load_layout.return_value = None
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)

    LayoutOpsMixin._on_discard_layout_changes(host)

    host._tabs["scene_a"].clear_pending_templates.assert_not_called()
    host._apply_layout_to_tabs.assert_not_called()


def test_initial_load_falls_back_when_active_layout_is_stale(qapp):
    expected = Layout(key="android", name="安卓布局")
    host = SimpleNamespace()
    host._layout_combo = QComboBox()
    host._manager = Mock()
    host._manager.list_layout_entries.return_value = [
        LayoutEntry("android", "安卓布局", "", {}),
        LayoutEntry("desktop", "桌面布局", "", {}),
    ]
    host._manager.get_active_layout_key.return_value = "默认布局"
    host._manager.load_layout.side_effect = (
        lambda key: expected if key == "android" else None)
    host._current_layout = None
    host._apply_layout_to_tabs = Mock()
    host._update_ui_state = Mock()
    host._refresh_combo = lambda: LayoutOpsMixin._refresh_combo(host)

    LayoutOpsMixin._auto_load_active(host)

    assert host._layout_combo.currentData() == "android"
    assert host._current_layout is expected
    host._manager.load_layout.assert_called_once_with("android")
    host._apply_layout_to_tabs.assert_called_once_with()
