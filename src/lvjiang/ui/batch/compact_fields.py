"""批量配置的双列表单：共用标签宽度，窄窗口回到单列。"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QSize
from PyQt6.QtWidgets import QGridLayout, QLabel, QPlainTextEdit, QWidget


class CompactFields(QWidget):
    """短字段按顺序并排，长字段整行；隐藏控件时重新排布但保留值。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._fields: list[tuple[QLabel | None, QWidget, bool, bool]] = []
        self._two_columns: bool | None = None

    def add_field(self, label: str | QLabel | None, widget: QWidget, *, full_row: bool = False, new_row: bool = False) -> None:
        label_widget = QLabel(label, self) if isinstance(label, str) else label
        if label_widget is not None:
            label_widget.setParent(self)
            label_widget.setVisible(True)
        widget.setParent(self)
        widget.setVisible(True)
        self._fields.append((label_widget, widget, full_row, new_row))
        self._relayout(force=True)

    def set_field_visible(self, widget: QWidget, visible: bool) -> None:
        for label, field, _full, _new in self._fields:
            if field is widget:
                if field.isHidden() == (not visible):
                    return
                field.setVisible(visible)
                if label is not None:
                    label.setVisible(visible)
                self._relayout(force=True)
                return

    def _field_width(self, label: QLabel | None, widget: QWidget, label_width: int) -> int:
        width = max(widget.minimumWidth(), widget.minimumSizeHint().width())
        return width + label_width + self._grid.horizontalSpacing() if label is not None else width

    def _relayout(self, *, force: bool = False) -> None:
        fields = [field for field in self._fields if not field[1].isHidden()]
        label_width = max((label.sizeHint().width() for label, _, _, _ in fields if label is not None), default=0)
        widths = [0, 0]
        side = 0
        for label, widget, full, new_row in fields:
            if full:
                side = 0
                continue
            if new_row:
                side = 0
            widths[side] = max(widths[side], self._field_width(label, widget, label_width))
            side = 1 - side
        two = bool(widths[1] and self.width() >= sum(widths) + self._grid.horizontalSpacing())
        if not force and self._two_columns == two:
            return
        self._two_columns = two
        while self._grid.count():
            self._grid.takeAt(0)
        self._grid.setColumnMinimumWidth(0, label_width)
        self._grid.setColumnMinimumWidth(2, label_width if two else 0)
        self._grid.setColumnStretch(1, 1)
        self._grid.setColumnStretch(3, 1 if two else 0)
        row = column = 0
        columns = 4 if two else 2
        for label, widget, full, new_row in fields:
            if (full or new_row) and column:
                row += 1
                column = 0
            span = columns if full else 2
            if isinstance(widget, QPlainTextEdit):
                if label is not None:
                    self._grid.addWidget(label, row, 0, 1, columns)
                    row += 1
                self._grid.addWidget(widget, row, 0, 1, columns)
            elif label is None:
                self._grid.addWidget(widget, row, column, 1, span)
            else:
                self._grid.addWidget(label, row, column)
                self._grid.addWidget(widget, row, column + 1, 1, span - 1)
            column += span
            if column == columns:
                row += 1
                column = 0
        self.updateGeometry()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        fields = [field for field in self._fields if not field[1].isHidden()]
        label_width = max((label.sizeHint().width() for label, _, _, _ in fields if label is not None), default=0)
        width = max((self._field_width(label, widget, label_width) for label, widget, _, _ in fields), default=0)
        return QSize(width, self._grid.minimumSize().height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._relayout()

    def event(self, event) -> bool:
        if event is not None and event.type() == QEvent.Type.LayoutRequest and hasattr(self, '_fields'):
            self._relayout()
        return super().event(event)
