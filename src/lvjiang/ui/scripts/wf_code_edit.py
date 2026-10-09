"""脚本代码编辑器：按住 Ctrl 时标记可跳转的 call，点击后请求打开 def。"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QMouseEvent,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
)
from PyQt6.QtWidgets import QPlainTextEdit, QTextEdit

from ...workflows.proc_location import ProcCallSite, index_proc_calls
from ..theme import get_theme_manager


class WfCodeEdit(QPlainTextEdit):
    """在普通代码编辑之上提供过程跳转。

    下划线只在按住 Ctrl 且光标落在已解析的过程名上时出现。运行锁定期间关闭，
    避免换文件打乱当前执行行。
    """

    definition_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source_path: Path | None = None
        self._navigation_enabled = True
        self._ctrl_down = False
        self._last_pos = QPoint()
        self._call_index: dict[int, ProcCallSite] | None = None
        self._index_key: tuple[str, str] | None = None
        self._active_link: ProcCallSite | None = None
        self._execution_line: int | None = None
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.viewport().installEventFilter(self)

    def set_source_path(self, path: Path | None) -> None:
        """当前缓冲区对应的脚本文件。换文件后丢弃上一份解析结果。"""
        self._source_path = path
        self._call_index = None
        self._index_key = None
        self._clear_link()

    def set_navigation_enabled(self, enabled: bool) -> None:
        self._navigation_enabled = enabled
        if not enabled:
            self._ctrl_down = False
            self._clear_link()

    def set_execution_line(self, line_no: int | None) -> None:
        """保留调试器的整行高亮，不覆盖过程名下划线。"""
        self._execution_line = line_no if line_no and line_no > 0 else None
        self._apply_selections()

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if watched is self.viewport() and self._navigation_enabled:
            if event.type() == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
                self._last_pos = event.position().toPoint()
                self._update_link(self._last_pos, event.modifiers())
            elif event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
                self._last_pos = event.position().toPoint()
                if (event.button() == Qt.MouseButton.LeftButton
                        and self._update_link(self._last_pos, event.modifiers())):
                    definition = self._active_link.definition if self._active_link else None
                    if definition is not None:
                        self.definition_requested.emit(definition)
                        return True
            elif event.type() == QEvent.Type.Leave:
                self._last_pos = QPoint(-1, -1)
                self._clear_link()
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Control:
            self._ctrl_down = True
            self._update_link(self._last_pos, Qt.KeyboardModifier.ControlModifier)
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Control:
            self._ctrl_down = False
            self._clear_link()
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):  # noqa: N802 - Qt override
        self._ctrl_down = False
        self._clear_link()
        super().focusOutEvent(event)

    def _ctrl_active(self, modifiers: Qt.KeyboardModifier) -> bool:
        return self._ctrl_down or bool(modifiers & Qt.KeyboardModifier.ControlModifier)

    def _update_link(self, pos: QPoint, modifiers: Qt.KeyboardModifier) -> bool:
        if not self._navigation_enabled or not self._ctrl_active(modifiers):
            self._clear_link()
            return False
        viewport = self.viewport()
        if viewport is None or not viewport.rect().contains(pos):
            self._clear_link()
            return False
        cursor = self.cursorForPosition(pos)
        block = cursor.block()
        site = self._calls().get(block.blockNumber())
        column = cursor.positionInBlock()
        if site is None or not site.start <= column < site.end:
            self._clear_link()
            return False
        if self._active_link != site:
            self._active_link = site
            viewport.setCursor(Qt.CursorShape.PointingHandCursor)
            self._apply_selections()
        return True

    def _clear_link(self) -> None:
        if self._active_link is None:
            return
        self._active_link = None
        viewport = self.viewport()
        if viewport is not None:
            viewport.unsetCursor()
        self._apply_selections()

    def _calls(self) -> dict[int, ProcCallSite]:
        text = self.toPlainText()
        origin = "" if self._source_path is None else str(self._source_path)
        key = (text, origin)
        if self._call_index is None or self._index_key != key:
            self._index_key = key
            self._call_index = index_proc_calls(text, self._source_path)
        return self._call_index

    def _apply_selections(self) -> None:
        selections: list[QTextEdit.ExtraSelection] = []
        document = self.document()
        if document is None:
            self.setExtraSelections(selections)
            return
        if self._execution_line is not None:
            block = document.findBlockByNumber(self._execution_line - 1)
            if block.isValid():
                current = QTextEdit.ExtraSelection()
                current.format.setBackground(
                    QColor(get_theme_manager().tokens.warning_surface))
                current.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                current.cursor = QTextCursor(block)
                selections.append(current)
        site = self._active_link
        if site is not None:
            block = document.findBlockByNumber(site.line)
            if block.isValid():
                text_len = len(block.text())
                start = min(site.start, text_len)
                end = min(site.end, text_len)
                if start < end:
                    link = QTextEdit.ExtraSelection()
                    fmt = QTextCharFormat()
                    fmt.setFontUnderline(True)
                    fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
                    fmt.setForeground(QColor(get_theme_manager().tokens.accent))
                    link.format = fmt
                    cursor = QTextCursor(block)
                    cursor.setPosition(block.position() + start)
                    cursor.setPosition(
                        block.position() + end, QTextCursor.MoveMode.KeepAnchor)
                    link.cursor = cursor
                    selections.append(link)
        self.setExtraSelections(selections)
