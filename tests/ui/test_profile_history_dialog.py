"""用户总览历史查看：跨用户分页与单用户限定共用同一对话框。"""

from types import SimpleNamespace

from lvjiang.ui.profile import column_management, dialogs


def test_history_dialog_pages_all_users_and_hides_username_for_one_user(
    qtbot, monkeypatch,
) -> None:
    records = [
        {
            "ts": "2026-09-01T12:00:00",
            "username": "u1" if i % 2 == 0 else "u2",
            "type": "quota", "key": "target",
            "old_value": None, "new_value": float(i),
            "change_type": "action", "source": "", "detail": "",
        }
        for i in range(101)
    ]

    def filtered(username):
        return [
            record for record in records
            if username is None or record["username"] == username
        ]

    monkeypatch.setattr(
        dialogs, "db_count_history",
        lambda username, **_: len(filtered(username)),
    )
    monkeypatch.setattr(
        dialogs, "db_get_history",
        lambda username, *, limit, offset, **_: filtered(username)[
            offset:offset + limit
        ],
    )

    all_dialog = dialogs.HistoryDialog(None, "quota", "target", "目标")
    qtbot.addWidget(all_dialog)
    assert all_dialog._table.rowCount() == 100
    assert not all_dialog._table.isColumnHidden(1)
    assert all_dialog._table.item(0, 1).text() == "u1"
    assert "1/2" in all_dialog._page_label.text()
    assert "共 101 条" in all_dialog._page_label.text()
    all_dialog._next_button.click()
    assert all_dialog._table.rowCount() == 1
    assert "2/2" in all_dialog._page_label.text()
    assert not all_dialog._next_button.isEnabled()
    all_dialog._page_size_combo.setCurrentIndex(
        all_dialog._page_size_combo.findData(50))
    assert all_dialog._table.rowCount() == 50
    assert "1/3" in all_dialog._page_label.text()
    all_dialog._last_button.click()
    assert all_dialog._table.rowCount() == 1
    assert "3/3" in all_dialog._page_label.text()

    user_dialog = dialogs.HistoryDialog("u2", "quota", "target", "目标")
    qtbot.addWidget(user_dialog)
    assert user_dialog._table.rowCount() == 50
    assert user_dialog._table.isColumnHidden(1)
    assert "共 50 条" in user_dialog._page_label.text()


def test_column_header_history_opens_shared_dialog_without_user(
    monkeypatch,
) -> None:
    actions = {}

    class FakeMenu:
        def __init__(self, _parent):
            pass

        def addAction(self, label, callback):
            actions[label] = callback

        def addSeparator(self):
            pass

        def exec(self, _position):
            pass

    class FakeHeader:
        def logicalIndexAt(self, _position):
            return 1

        def mapToGlobal(self, position):
            return position

    class FakeTable:
        def horizontalHeader(self):
            return FakeHeader()

    calls = []
    fake = SimpleNamespace(
        _tables={"g": FakeTable()},
        _visible_columns=lambda _group: ["target"],
        _show_history_dialog=lambda *args: calls.append(args),
    )
    monkeypatch.setattr("PyQt6.QtWidgets.QMenu", FakeMenu)
    monkeypatch.setattr(
        column_management, "get_profile_config",
        lambda: SimpleNamespace(
            get_key=lambda _key: SimpleNamespace(label="目标"),
            get_model_type=lambda _key: "quota",
        ),
    )

    column_management.ProfileColumnMixin._on_header_context_menu(fake, None, "g")
    actions["查看历史记录"]()

    assert calls == [(None, "quota", "target", "目标")]


def test_empty_history_has_clear_page_status(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(dialogs, "db_count_history", lambda *_args, **_kw: 0)
    monkeypatch.setattr(dialogs, "db_get_history", lambda *_args, **_kw: [])

    dialog = dialogs.HistoryDialog(None, "quota", "target", "目标")
    qtbot.addWidget(dialog)

    assert dialog._table.rowCount() == 0
    assert "显示 0–0 / 共 0 条" in dialog._page_label.text()
    assert not dialog._previous_button.isEnabled()
    assert not dialog._next_button.isEnabled()
