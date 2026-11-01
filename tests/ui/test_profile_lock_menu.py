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


def test_detail_page_hides_locked_value():
    """用户详情页与总览同口径：锁定 key 显示空白，不显示冻结值。"""
    from lvjiang.core.profile.models import KeyDef
    from lvjiang.ui.profile.user_info import _format_detail_value

    kd = KeyDef(key="k", label="配额")
    data = {"quota": {"k": {"value": 2, "locked": True, "updated_at": ""}}}
    assert _format_detail_value(kd, "quota", data) == ""
