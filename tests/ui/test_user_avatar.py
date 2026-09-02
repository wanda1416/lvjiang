"""Avatar display, square crop output, and editor persistence."""

import numpy as np
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QMessageBox

from lvjiang.core.config.session import reset_session_store
from lvjiang.core.user_avatars import UserAvatarStore
from lvjiang.core.user_config import UserConfigManager
from lvjiang.ui.avatar_editor_dialog import AvatarCropCanvas, AvatarEditorDialog
from lvjiang.ui.avatar_widget import AvatarWidget


def test_crop_canvas_always_outputs_512_square(qtbot):
    canvas = AvatarCropCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(420, 390)
    image = QImage(800, 400, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ff3366"))
    canvas.set_image(image)

    cropped = canvas.cropped_image()

    assert cropped.size().width() == 512
    assert cropped.size().height() == 512
    assert QColor(cropped.pixel(256, 256)).red() > 200


def test_avatar_widget_emits_edit_from_keyboard(qtbot):
    widget = AvatarWidget("测试用户", editable=True)
    qtbot.addWidget(widget)

    with qtbot.waitSignal(widget.edit_requested):
        qtbot.keyClick(widget, Qt.Key.Key_Return)


def test_avatar_widget_emits_edit_from_double_click(qtbot):
    widget = AvatarWidget("测试用户", editable=True)
    qtbot.addWidget(widget)

    with qtbot.waitSignal(widget.edit_requested):
        qtbot.mouseDClick(
            widget,
            Qt.MouseButton.LeftButton,
            pos=QPoint(12, 12),
        )


def test_editor_pastes_clipboard_image_into_cropper(qtbot, tmp_path, monkeypatch):
    from lvjiang import constants
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    reset_session_store()
    manager = UserConfigManager()
    dialog = AvatarEditorDialog(
        manager.get_active_user_name(), manager,
        store=UserAvatarStore(tmp_path / "avatars"),
    )
    qtbot.addWidget(dialog)
    image = QImage(320, 240, QImage.Format.Format_ARGB32)
    image.fill(QColor("#44aa77"))
    QApplication.clipboard().setImage(image)

    dialog._paste_image()

    assert dialog._canvas.has_image()
    assert dialog._draft_mode is True
    assert dialog._save_button.isEnabled()
    reset_session_store()


def test_editor_buttons_use_shared_semantic_styles(qtbot, tmp_path, monkeypatch):
    from lvjiang import constants
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    reset_session_store()
    manager = UserConfigManager()
    dialog = AvatarEditorDialog(
        manager.get_active_user_name(), manager,
        store=UserAvatarStore(tmp_path / "avatars"),
    )
    qtbot.addWidget(dialog)

    assert "palette(highlight)" in dialog._import_button.styleSheet()
    assert "palette(highlight)" in dialog._save_button.styleSheet()
    assert "palette(button)" in dialog._cancel_button.styleSheet()
    assert "#c62828" in dialog._remove_button.styleSheet()
    assert (
        dialog._import_button.width()
        == dialog._paste_button.width()
        == dialog._screenshot_button.width()
    )
    assert not dialog._screenshot_button.isEnabled()
    assert dialog._cancel_button.width() == dialog._save_button.width()
    reset_session_store()


def test_editor_refreshes_bgr_screenshot_into_cropper(qtbot, tmp_path, monkeypatch):
    from lvjiang import constants
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    reset_session_store()
    manager = UserConfigManager()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    frame[:, :] = (10, 20, 230)
    dialog = AvatarEditorDialog(
        manager.get_active_user_name(),
        manager,
        store=UserAvatarStore(tmp_path / "avatars"),
        screenshot_callback=lambda: (frame, None),
    )
    qtbot.addWidget(dialog)

    dialog._screenshot_button.click()

    assert dialog._canvas.has_image()
    assert dialog._draft_mode is True
    assert dialog._save_button.isEnabled()
    pixel = QColor(dialog._canvas._image.pixel(0, 0))
    assert (pixel.red(), pixel.green(), pixel.blue()) == (230, 20, 10)
    reset_session_store()


def test_editor_saves_cropped_asset_and_user_reference(
    qtbot, tmp_path, monkeypatch,
):
    from lvjiang import constants
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    reset_session_store()
    manager = UserConfigManager()
    username = manager.get_active_user_name()
    store = UserAvatarStore(tmp_path / "avatars")
    dialog = AvatarEditorDialog(username, manager, store=store)
    qtbot.addWidget(dialog)
    image = QImage(640, 480, QImage.Format.Format_ARGB32)
    image.fill(QColor("#3388ee"))
    dialog._set_source_image(image)

    dialog._save()

    filename = manager.get_user(username).avatar
    assert dialog.result() == dialog.DialogCode.Accepted
    assert store.path_for(filename).exists()
    saved = QImage(str(store.path_for(filename)))
    assert saved.size().width() == saved.size().height() == 512

    reopened = AvatarEditorDialog(username, manager, store=store)
    qtbot.addWidget(reopened)
    assert reopened._selected_filename == filename
    assert reopened._canvas.has_image()
    assert not reopened._save_button.isEnabled()
    reset_session_store()


def test_editor_deletes_unused_history_avatar_from_context_action(
    qtbot, tmp_path, monkeypatch,
):
    from lvjiang import constants
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    reset_session_store()
    manager = UserConfigManager()
    store = UserAvatarStore(tmp_path / "avatars")
    canvas = AvatarCropCanvas()
    image = QImage(320, 320, QImage.Format.Format_ARGB32)
    image.fill(QColor("#8844cc"))
    canvas.set_image(image)
    filename = store.save_png(canvas.png_bytes())
    dialog = AvatarEditorDialog(
        manager.get_active_user_name(), manager, store=store)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(
        "lvjiang.ui.avatar_editor_dialog.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    item = dialog._library.item(0)
    dialog._select_library_item(item)
    dialog._delete_library_item(item)

    assert dialog._library.contextMenuPolicy() == (
        Qt.ContextMenuPolicy.CustomContextMenu)
    assert not store.path_for(filename).exists()
    assert dialog._library.count() == 0
    assert not dialog._canvas.has_image()
    reset_session_store()


def test_editor_refuses_to_delete_avatar_used_by_a_user(
    qtbot, tmp_path, monkeypatch,
):
    from lvjiang import constants
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    reset_session_store()
    manager = UserConfigManager()
    username = manager.get_active_user_name()
    store = UserAvatarStore(tmp_path / "avatars")
    canvas = AvatarCropCanvas()
    image = QImage(320, 320, QImage.Format.Format_ARGB32)
    image.fill(QColor("#33aa88"))
    canvas.set_image(image)
    filename = store.save_png(canvas.png_bytes())
    assert manager.set_user_avatar(username, filename)
    dialog = AvatarEditorDialog(username, manager, store=store)
    qtbot.addWidget(dialog)
    notices = []
    monkeypatch.setattr(
        "lvjiang.ui.avatar_editor_dialog.QMessageBox.information",
        lambda _parent, title, text: notices.append((title, text)),
    )

    dialog._delete_library_item(dialog._library.item(0))

    assert store.path_for(filename).exists()
    assert manager.get_user(username).avatar == filename
    assert username in notices[0][1]
    reset_session_store()
