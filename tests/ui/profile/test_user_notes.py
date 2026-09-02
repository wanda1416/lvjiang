"""User-information sticky-note panel tests."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QInputDialog, QMessageBox, QPushButton

from lvjiang.core.user_notes import UserNotesRepository
from lvjiang.ui.profile.user_notes import UserNotesPanel


def _panel(qtbot, tmp_path):
    repo = UserNotesRepository("测试用户", users_dir=tmp_path)
    panel = UserNotesPanel("测试用户", repository=repo)
    qtbot.addWidget(panel)
    return panel, repo


def test_empty_panel_shows_placeholder(qtbot, tmp_path):
    panel, _repo = _panel(qtbot, tmp_path)

    assert panel.title() == "便利贴"
    assert panel._empty_label.text() == "暂无便利贴"
    assert panel._cards == []
    assert panel.findChildren(QPushButton) == []


def test_add_note_rebuilds_panel_with_new_card(qtbot, tmp_path, monkeypatch):
    panel, repo = _panel(qtbot, tmp_path)
    panel.set_content_font_size(18)
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        staticmethod(lambda *_args, **_kwargs: ("记得清商店", True)),
    )

    panel.add_note()

    assert [note.text for note in repo.list_notes()] == ["记得清商店"]
    assert len(panel._cards) == 1
    assert panel._cards[0].text_label.text() == "记得清商店"
    assert panel._cards[0].text_label.font().pointSize() == 18
    assert not panel._cards[0].text_label.font().bold()
    assert panel._notes_layout.alignment() & Qt.AlignmentFlag.AlignTop


def test_edit_note_updates_existing_card(qtbot, tmp_path, monkeypatch):
    panel, repo = _panel(qtbot, tmp_path)
    note = repo.add("原内容")
    panel.refresh()
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        staticmethod(lambda *_args, **_kwargs: ("修改后", True)),
    )

    qtbot.mouseClick(panel._cards[0].edit_button, Qt.MouseButton.LeftButton)

    saved = repo.list_notes()
    assert [(item.id, item.text) for item in saved] == [(note.id, "修改后")]
    assert panel._cards[0].text_label.text() == "修改后"


def test_delete_note_requires_confirmation(qtbot, tmp_path, monkeypatch):
    panel, repo = _panel(qtbot, tmp_path)
    repo.add("待删除")
    panel.refresh()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes
        ),
    )

    qtbot.mouseClick(panel._cards[0].delete_button, Qt.MouseButton.LeftButton)

    assert repo.list_notes() == []
    assert panel._cards == []
    assert panel._empty_label.text() == "暂无便利贴"


def test_cancelled_add_does_not_write(qtbot, tmp_path, monkeypatch):
    panel, repo = _panel(qtbot, tmp_path)
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        staticmethod(lambda *_args, **_kwargs: ("不保存", False)),
    )

    panel.add_note()

    assert repo.list_notes() == []
    assert not repo.path.exists()
