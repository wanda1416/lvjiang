"""Sticky-note panel for the current user's information page."""

from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.user_notes import MAX_TEXT_LENGTH, UserNote, UserNotesRepository
from ...i18n import tr
from ..user_toolbar import USER_TOOLBAR_BTN_STYLE
from .user_info_styles import USER_INFO_GROUP_STYLE


class _NoteCard(QFrame):
    """Compact presentation for one sticky note."""

    def __init__(self, note: UserNote, edit_callback, delete_callback, parent=None):
        super().__init__(parent)
        self.note_id = note.id
        self.setObjectName("userNoteCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 6, 6)
        layout.setSpacing(6)

        self.text_label = QLabel(note.text)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.text_label, stretch=1)

        self.edit_button = QPushButton(tr("编辑"))
        self.edit_button.setStyleSheet(USER_TOOLBAR_BTN_STYLE)
        self.edit_button.clicked.connect(
            lambda _checked=False: edit_callback(note.id, note.text)
        )
        layout.addWidget(self.edit_button)

        self.delete_button = QPushButton(tr("删除"))
        self.delete_button.setStyleSheet(USER_TOOLBAR_BTN_STYLE)
        self.delete_button.clicked.connect(
            lambda _checked=False: delete_callback(note.id)
        )
        layout.addWidget(self.delete_button)


class UserNotesPanel(QGroupBox):
    """Add, edit and delete short notes for one user."""

    def __init__(
        self,
        username: str,
        parent: QWidget | None = None,
        *,
        repository: UserNotesRepository | None = None,
    ):
        super().__init__(tr("便利贴"), parent)
        self.setObjectName("userNotesPanel")
        self.setStyleSheet(USER_INFO_GROUP_STYLE)
        self._repository = repository or UserNotesRepository(username)
        self._cards: list[_NoteCard] = []
        self._empty_label: QLabel | None = None
        self._content_point_size = 0
        self._setup_ui()
        self.refresh()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(6)

        self._notes_layout = QVBoxLayout()
        self._notes_layout.setContentsMargins(0, 0, 0, 0)
        self._notes_layout.setSpacing(6)
        self._notes_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addLayout(self._notes_layout)

    def _apply_current_font(self, widget: QWidget) -> None:
        """Apply only the current content size to dynamic note text.

        ``UserNotesPanel`` is a QGroupBox whose title style is bold.  Copying its
        computed font wholesale makes newly rebuilt note labels bold as well, so
        dynamic labels must keep a normal body-text weight.
        """
        labels = ([widget] if isinstance(widget, QLabel)
                  else widget.findChildren(QLabel))
        for label in labels:
            font = QFont(label.font())
            if self._content_point_size > 0:
                font.setPointSize(self._content_point_size)
            font.setWeight(QFont.Weight.Normal)
            label.setFont(font)

    def set_content_font_size(self, point_size: int) -> None:
        """Apply the authoritative user-info font setting to note body text."""
        if not 8 <= int(point_size) <= 24:
            return
        self._content_point_size = int(point_size)
        for card in self._cards:
            self._apply_current_font(card)
        if self._empty_label is not None:
            self._apply_current_font(self._empty_label)

    def _clear_cards(self) -> None:
        self._cards.clear()
        self._empty_label = None
        while self._notes_layout.count():
            item = self._notes_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def refresh(self) -> None:
        """Reload the current user's notes from disk."""
        self._clear_cards()
        try:
            notes = self._repository.list_notes()
        except Exception as exc:  # noqa: BLE001 用户文件损坏时页面仍需可用
            logger.warning(f"读取用户便利贴失败: {exc}")
            label = QLabel(tr("读取便利贴失败：{error}").format(error=exc))
            label.setProperty("status", "danger")
            label.setWordWrap(True)
            self._apply_current_font(label)
            self._notes_layout.addWidget(label)
            return

        if not notes:
            self._empty_label = QLabel(tr("暂无便利贴"))
            self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._empty_label.setStyleSheet("color: palette(mid); padding: 8px;")
            self._apply_current_font(self._empty_label)
            self._notes_layout.addWidget(self._empty_label)
            return

        for note in notes:
            card = _NoteCard(note, self._edit_note, self._delete_note, self)
            self._apply_current_font(card)
            self._cards.append(card)
            self._notes_layout.addWidget(
                card, alignment=Qt.AlignmentFlag.AlignTop)

    def _prompt_text(self, title: str, current: str = "") -> str | None:
        text, accepted = QInputDialog.getMultiLineText(
            self, title, tr("便利贴内容:"), current
        )
        if not accepted:
            return None
        normalized = text.strip()
        if not normalized:
            QMessageBox.warning(
                self, tr("保存失败"), tr("便利贴内容不能为空")
            )
            return None
        if len(normalized) > MAX_TEXT_LENGTH:
            QMessageBox.warning(
                self,
                tr("保存失败"),
                tr("便利贴内容不能超过 {limit} 个字符").format(
                    limit=MAX_TEXT_LENGTH
                ),
            )
            return None
        return normalized

    def add_note(self) -> None:
        """Prompt for and persist a new sticky note."""
        text = self._prompt_text(tr("新增便利贴"))
        if text is None:
            return
        try:
            self._repository.add(text)
        except Exception as exc:  # noqa: BLE001 持久化错误需反馈给用户
            logger.error(f"保存用户便利贴失败: {exc}")
            QMessageBox.warning(
                self,
                tr("保存失败"),
                tr("保存便利贴失败：{error}").format(error=exc),
            )
            return
        self.refresh()

    def _edit_note(self, note_id: str, current: str) -> None:
        text = self._prompt_text(tr("编辑便利贴"), current)
        if text is None:
            return
        try:
            self._repository.update(note_id, text)
        except Exception as exc:  # noqa: BLE001 持久化错误需反馈给用户
            logger.error(f"更新用户便利贴失败: {exc}")
            QMessageBox.warning(
                self,
                tr("保存失败"),
                tr("保存便利贴失败：{error}").format(error=exc),
            )
            return
        self.refresh()

    def _delete_note(self, note_id: str) -> None:
        reply = QMessageBox.question(
            self,
            tr("确认删除"),
            tr("确定要删除这条便利贴吗？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._repository.delete(note_id)
        except Exception as exc:  # noqa: BLE001 持久化错误需反馈给用户
            logger.error(f"删除用户便利贴失败: {exc}")
            QMessageBox.warning(
                self,
                tr("删除失败"),
                tr("删除便利贴失败：{error}").format(error=exc),
            )
            return
        self.refresh()


__all__ = ["UserNotesPanel"]
