"""账户管理对话框：顶部始终展示「当前设备ID」+ 复制。

保护的真实回归：管理员重置密码时唯一能核对的身份凭据就是设备 ID，而这一页是
用户唯一能拿到它的地方。做成「登录后才看得见」「未登录才看得见」或者复制按钮
不真的写剪贴板，整条找回流程就断了。
"""
from __future__ import annotations


class _FakeClipboard:
    def __init__(self) -> None:
        self.text = ""

    def setText(self, value: str) -> None:   # noqa: N802 — Qt 侧同名
        self.text = value


def _open_dialog(qtbot, monkeypatch):
    from lvjiang.ui.users import account_dialog

    monkeypatch.setattr(account_dialog, "readonly_instance", lambda: False)
    # 不联网、不起后台线程：只验顶部那一行，不关心登录恢复
    monkeypatch.setattr(
        account_dialog.AccountHolder, "restore_async",
        lambda self, *, manual=False: None)
    monkeypatch.setattr(account_dialog, "_holder", None)
    dialog = account_dialog.AccountManagerDialog()
    qtbot.addWidget(dialog)
    return account_dialog, dialog


def _fake_clipboard(account_dialog, monkeypatch) -> _FakeClipboard:
    clipboard = _FakeClipboard()
    monkeypatch.setattr(account_dialog, "QApplication",
                        type("_App", (), {"clipboard": staticmethod(lambda: clipboard)}))
    return clipboard


def _device_button(dialog, row: int, label: str):
    """设备表「操作」列里的按钮（构造顺序：复制ID、登出）。"""
    host = dialog.devices.cellWidget(row, 3)
    layout = host.layout()
    for index in range(layout.count()):
        widget = layout.itemAt(index).widget()
        if widget is not None and widget.text() == label:
            return widget
    raise AssertionError(f"设备行 {row} 没有「{label}」按钮")


def test_unlogged_dialog_shows_and_copies_device_id(qtbot, monkeypatch):
    account_dialog, dialog = _open_dialog(qtbot, monkeypatch)
    expected = account_dialog.current_instance_id()

    assert dialog.device_id_row.isVisibleTo(dialog)
    assert expected in dialog.device_id_label.text()
    assert "重置密码" in dialog.device_hint.text()

    clipboard = _fake_clipboard(account_dialog, monkeypatch)
    dialog._copy_device_id()
    assert clipboard.text == expected
    assert dialog.copy_device_button.text() == "已复制"


def test_logged_in_dialog_copies_device_id_from_device_row(qtbot, monkeypatch):
    """登录后不给顶部行占位，但要能在「设备管理」每行复制该设备的 ID。"""
    account_dialog, dialog = _open_dialog(qtbot, monkeypatch)
    dialog._render({"account": {"login": "wang", "display_name": "王某"},
                    "devices": [{"id": "a" * 32, "name": "书房PC",
                                 "platform": "win32", "last_seen": "2026-11-19T13:50:00Z"},
                                {"id": "b" * 32, "name": "Pixel 7",
                                 "platform": "android", "last_seen": "2026-11-19T13:40:00Z"}],
                    "instance_id": "b" * 32})

    assert not dialog.device_id_row.isVisibleTo(dialog)

    clipboard = _fake_clipboard(account_dialog, monkeypatch)
    second = _device_button(dialog, 1, "复制ID")
    second.click()
    assert clipboard.text == "b" * 32
    assert second.text() == "已复制"
