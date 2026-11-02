"""总览右键菜单的「锁定 / 解锁」入口。

锁定后引擎完全忽略该值（读空、写拒绝、不再重置），所以必须走二次确认，
并且操作成功后刷新总览，用户能立刻看到那一格变成深灰空白。
"""
from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtWidgets import QMessageBox

from lvjiang.ui.profile.cell_editing import ProfileCellEditingMixin


def _fake_tab(calls: list):
    tab = SimpleNamespace(refresh=lambda: calls.append(("refresh",)))
    tab._toggle_profile_lock = (
        ProfileCellEditingMixin._toggle_profile_lock.__get__(tab))
    return tab


def test_lock_requires_confirmation(monkeypatch):
    import lvjiang.core.profile.repository as repo

    calls: list = []
    monkeypatch.setattr(repo, "db_set_locked",
                        lambda *args: calls.append(("lock", args)) or True)
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No))

    tab = _fake_tab(calls)
    tab._toggle_profile_lock("u1", "quota", "k", "配额", False)

    assert not [c for c in calls if c[0] == "lock"]
    assert not [c for c in calls if c[0] == "refresh"]


def test_lock_then_refresh(monkeypatch):
    import lvjiang.core.profile.events as events
    import lvjiang.core.profile.repository as repo

    calls: list = []
    monkeypatch.setattr(repo, "db_set_locked",
                        lambda *args: calls.append(("lock", args)) or True)
    monkeypatch.setattr(events, "notify_updated",
                        lambda user: calls.append(("notify", user)))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes))

    tab = _fake_tab(calls)
    tab._toggle_profile_lock("u1", "quota", "k", "配额", False)

    assert calls[0] == ("lock", ("u1", "quota", "k"))
    assert ("notify", "u1") in calls
    assert ("refresh",) in calls


def test_unlock_uses_unlock_api(monkeypatch):
    import lvjiang.core.profile.events as events
    import lvjiang.core.profile.repository as repo

    calls: list = []
    monkeypatch.setattr(repo, "db_unlock",
                        lambda *args: calls.append(("unlock", args)) or True)
    monkeypatch.setattr(events, "notify_updated", lambda user: None)
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes))

    tab = _fake_tab(calls)
    tab._toggle_profile_lock("u1", "quota", "k", "配额", True)

    assert calls[0] == ("unlock", ("u1", "quota", "k"))


def test_detail_page_marks_locked_value():
    """用户详情页不显示冻结值，而要给一个明确的「已锁定」提示。"""
    from lvjiang.core.profile.models import KeyDef
    from lvjiang.ui.profile.user_info import (
        _format_detail_label,
        _format_detail_value,
    )

    kd = KeyDef(key="k", label="配额")
    data = {"quota": {"k": {"value": 2, "locked": True, "updated_at": ""}}}
    # 纯格式化层仍是空串（值不可信），界面层负责换成提示
    assert _format_detail_value(kd, "quota", data) == ""
    text, locked = _format_detail_label(kd, "quota", data)
    assert (text, locked) == ("已锁定", True)


def test_detail_page_formats_unlocked_value():
    """未锁定项照常显示数值，且不带锁定标记。"""
    from lvjiang.core.profile.models import KeyDef
    from lvjiang.ui.profile.user_info import _format_detail_label

    kd = KeyDef(key="k", label="配额")
    data = {"quota": {"k": {"value": 2, "updated_at": "2026-11-01T00:00:00"}}}
    text, locked = _format_detail_label(kd, "quota", data)
    assert locked is False
    assert text == "2"


def test_locked_cell_uses_pause_button_grey_background():
    """锁定单元格必须填**底色**，且用的是「暂停按钮默认那档灰」。

    只有前景色的话，空文本等于什么都没显示；色值单独抄一个还会和按钮不一致。
    """
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QApplication, QTableWidgetItem

    from lvjiang.ui.button_styles import DISABLED_BUTTON_BG
    from lvjiang.ui.profile.cell_formatting import apply_cell_style

    app = QApplication.instance() or QApplication([])  # noqa: F841 - 供控件使用
    item = QTableWidgetItem("")
    apply_cell_style(item, "locked")
    assert item.background().color() == QColor(DISABLED_BUTTON_BG)
    assert DISABLED_BUTTON_BG.upper() == "#9E9E9E"   # 暂停按钮默认态
    assert item.text() == ""
