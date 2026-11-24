"""只读实例的账户页：不自动同步、不做账户操作，但可以手动「立即同步」。"""
from __future__ import annotations

from PyQt6.QtWidgets import QMainWindow

from lvjiang.core import access


def _menu_action(host, text: str):
    menu = host.menuBar().actions()[0].menu()
    return next(action for action in menu.actions() if action.text() == text)


def test_account_manager_stays_reachable_on_readonly_instance(qtbot, monkeypatch):
    """入口不禁用：只读窗口也要能点开账户页手动同步一轮。"""
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
    assert action.isEnabled()
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
    from lvjiang.ui.users.account_dialog import AccountHolder

    monkeypatch.setattr(access, "_readonly", True)
    holder = AccountHolder()

    holder.restore_async()

    assert holder.done is True
    assert holder.summary is None
    assert holder._worker is None
    assert not holder._sync_timer.isActive()
    assert not holder._heartbeat_timer.isActive()


def test_readonly_instance_allows_manual_restore(qtbot, monkeypatch):
    """手动打开账户页时允许一次只读恢复，否则「立即同步」拿不到会话。"""
    from lvjiang.ui.users.account_dialog import AccountHolder

    monkeypatch.setattr(access, "_readonly", True)
    holder = AccountHolder()
    started: list[str] = []
    monkeypatch.setattr(holder, "_start", lambda action, kind: started.append(kind) or True)
    monkeypatch.setattr(holder.session, "has_stored_session", lambda: True)

    holder.restore_async(manual=True)

    assert started == ["operation"]


def test_readonly_instance_refuses_account_actions(qtbot, monkeypatch):
    """登录/改密/登出/清除同步照旧禁止；同步本身不被实例类型拦。"""
    from lvjiang.ui.users.account_dialog import AccountHolder

    monkeypatch.setattr(access, "_readonly", True)
    holder = AccountHolder()

    assert holder.login_async("someone", "secret") is False
    assert holder.logout_async() is False
    assert holder.logout_all_async() is False
    assert holder.change_password_async("old", "new-secret") is False
    assert holder.initial_sync_async() is False
    assert holder.clear_sync_async("secret") is False
    assert holder._worker is None
    assert holder._sync_worker is None


def test_readonly_instance_can_run_manual_sync(qtbot, monkeypatch):
    """只读实例拿到会话后，手动同步这一轮允许启动（分钟节拍仍然不起）。"""
    from lvjiang.ui.users.account_dialog import AccountHolder

    monkeypatch.setattr(access, "_readonly", True)
    monkeypatch.setattr("lvjiang.core.sync.initial.sync_established", lambda: True)
    holder = AccountHolder()
    holder.session.access_token = "token"
    started: list[str] = []
    monkeypatch.setattr(holder, "_start", lambda action, kind: started.append(kind) or True)

    assert holder.sync_async() is True

    assert started == ["sync"]
    assert not holder._sync_timer.isActive()
