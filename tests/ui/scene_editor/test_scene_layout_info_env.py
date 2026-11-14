"""布局信息对话框的「适配环境」下拉。

`env` 只有布局清单里有，内存 Layout 不携带，所以它必须在对话框里显式可编辑：
此前既没有入口，保存坐标时还会被顺手抹掉——手机下发因此把桌面布局也列出来。
"""

from unittest.mock import Mock

from PyQt6.QtWidgets import QComboBox, QDialog, QWidget

from lvjiang.core.layout.models import Layout
from lvjiang.ui.scene_editor import layout_ops
from lvjiang.ui.scene_editor.layout_ops import LayoutOpsMixin
from lvjiang.ui.widgets.combo_box import AutoWidthComboBox


def _doc(env_value: str | None) -> dict:
    entry = {"name": "安卓布局", "canvas": {}}
    if env_value is not None:
        entry["env"] = env_value
    return {"schema_version": 2, "layouts": {"android": entry}}


class _Host(QWidget):
    """对话框的宿主：只要是 QWidget 就够（真实宿主是场景编辑器窗口）。"""

    def __init__(self):
        super().__init__()
        self._current_layout = Layout(key="android", name="安卓布局")
        self._manager = Mock()
        self._manager.load_layout.return_value = Layout(
            key="android", name="安卓布局")
        self._layout_combo = QComboBox(self)
        self._layout_combo.addItem("安卓布局", "android")
        self._status_bar = Mock()
        self.refreshed = 0
        self.updated = 0

    def _refresh_combo(self):
        self.refreshed += 1

    def _update_ui_state(self):
        self.updated += 1


def _approve_with_env(monkeypatch, choice: str, *, expected_current: str):
    """打开对话框后把「适配环境」切成 choice，再按确定。"""

    def fake_exec(dialog):
        combo = next(c for c in dialog.findChildren(AutoWidthComboBox)
                     if c.findData("android") >= 0)
        assert combo.currentData() == expected_current
        combo.setCurrentIndex(combo.findData(choice))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", fake_exec)


def _setup(monkeypatch, doc: dict):
    monkeypatch.setattr(layout_ops, "load_layout_doc", lambda: doc)
    monkeypatch.setattr(layout_ops, "save_layout_doc", lambda d: None)
    monkeypatch.setattr(
        layout_ops, "load_available_envs",
        lambda: [("android", "安卓"), ("desktop", "桌面")])


def test_dialog_preselects_env_and_writes_new_choice(monkeypatch, qapp):
    doc = _doc("android")
    _setup(monkeypatch, doc)
    _approve_with_env(monkeypatch, "desktop", expected_current="android")

    LayoutOpsMixin._on_edit_layout_info(_Host())

    assert doc["layouts"]["android"]["env"] == "desktop"


def test_dialog_empty_env_clears_declaration(monkeypatch, qapp):
    doc = _doc("android")
    _setup(monkeypatch, doc)
    _approve_with_env(monkeypatch, "", expected_current="android")

    LayoutOpsMixin._on_edit_layout_info(_Host())

    assert "env" not in doc["layouts"]["android"]


def test_dialog_keeps_env_value_missing_from_app_envs(monkeypatch, qapp):
    """清单里的值已不在 app.yaml envs 里时不能顺手改成「不限」。"""
    doc = _doc("legacy")
    _setup(monkeypatch, doc)
    _approve_with_env(monkeypatch, "legacy", expected_current="legacy")

    LayoutOpsMixin._on_edit_layout_info(_Host())

    assert doc["layouts"]["android"]["env"] == "legacy"
