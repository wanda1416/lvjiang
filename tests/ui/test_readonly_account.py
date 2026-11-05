"""只读实例禁用账户管理：登录态与同步数据只归主实例。"""
from __future__ import annotations

from PyQt6.QtWidgets import QMainWindow

from lvjiang.core import access


def _menu_action(host, text: str):
    menu = host.menuBar().actions()[0].menu()
    return next(action for action in menu.actions() if action.text() == text)


def test_account_manager_disabled_on_readonly_instance(qtbot, monkeypatch):
    from lvjiang.ui.main.menu_ops import MenuOpsMixin

    class Host(MenuOpsMixin, QMainWindow):
        def _open_batch_config(self):
            pass

        def _check_update(self):
            pass

    monkeypatch.setattr(access, "_readonly", True)
    host = Host()
    qtbot.addWidget(host)
    host._setup_menu()

    action = _menu_action(host, "账户管理")
    assert not action.isEnabled()
    assert "只读实例" in action.toolTip()


def test_account_manager_enabled_on_main_instance(qtbot, monkeypatch):
    from lvjiang.ui.main.menu_ops import MenuOpsMixin

    class Host(MenuOpsMixin, QMainWindow):
        def _open_batch_config(self):
            pass

        def _check_update(self):
            pass

    monkeypatch.setattr(access, "_readonly", False)
    host = Host()
    qtbot.addWidget(host)
    host._setup_menu()

    assert _menu_action(host, "账户管理").isEnabled()


def test_readonly_instance_never_auto_restores_login(qtbot, monkeypatch):
    """只读实例不自动登录：不碰凭据库、不起后台线程、不开节拍。"""
    from lvjiang.ui.account_dialog import AccountHolder

    monkeypatch.setattr(access, "_readonly", True)
    holder = AccountHolder()

    holder.restore_async()

    assert holder.done is True
    assert holder.summary is None
    assert holder._worker is None
    assert not holder._sync_timer.isActive()


def test_readonly_instance_refuses_login_and_sync(qtbot, monkeypatch):
    from lvjiang.ui.account_dialog import AccountHolder

    monkeypatch.setattr(access, "_readonly", True)
    holder = AccountHolder()

    assert holder.login_async("someone", "secret") is False
    assert holder.sync_async() is False
    assert holder.initial_sync_async() is False
    assert holder.clear_sync_async("secret") is False
    assert holder._worker is None
    assert holder._sync_worker is None
