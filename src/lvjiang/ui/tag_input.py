"""可删除标签形式的多值输入控件。

Profile 数据模型定义的来源/用途、玩法配置的匹配关键字都用它录入若干短词。
控件本身不带业务含义，只负责「一行内维护一组互不重复的字符串」。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr


class TagInputWidget(QFrame):
    """按 Enter 创建可删除标签的多值词条输入框。

    一行内录入若干互不重复的短词，× 逐个删除，``tags()`` 取当前值。
    Profile 的来源/用途和玩法的匹配关键字都用它——这是纯粹的输入原语，
    不带任何业务含义，所以放在公共 UI 层由各处引用。
    """

    #: 标签增删后发出；调用方据此落盘，不必自己轮询 tags()。
    tags_changed = pyqtSignal()

    def __init__(self, values: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("profileTagInput")
        self.setMinimumHeight(52)
        self.setMaximumHeight(52)
        self.setStyleSheet(
            "QFrame#profileTagInput { border: 1px solid palette(mid); "
            "border-radius: 4px; background: palette(base); }"
            "QFrame#profileTagChip { border: 1px solid palette(mid); "
            "border-radius: 9px; background: palette(alternate-base); }"
            "QFrame#profileTagChip QLabel { border: none; background: transparent; }"
            "QFrame#profileTagChip QPushButton { border: none; background: transparent; "
            "padding: 0 2px; color: palette(mid); }"
            "QFrame#profileTagChip QPushButton:hover { color: palette(text); }"
            "QFrame#profileTagInput QLineEdit { border: none; background: transparent; }"
        )

        self._values: list[str] = []
        self._chips: dict[str, QFrame] = {}

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(1, 1, 1, 1)
        self._scroll = QScrollArea()
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(False)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer_layout.addWidget(self._scroll)

        self._content = QWidget()
        self._row = QHBoxLayout(self._content)
        self._row.setContentsMargins(4, 3, 4, 3)
        self._row.setSpacing(5)
        self._row.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self._scroll.setWidget(self._content)

        self._input = QLineEdit()
        self._input.setMinimumWidth(150)
        self._input.installEventFilter(self)

        self._row.addWidget(self._input)
        for value in values:
            self.add_tag(value)

    def eventFilter(self, watched, event):  # type: ignore[override]
        """Enter 只提交标签，不触发所在对话框的默认按钮。"""
        if (
            watched is self._input
            and isinstance(event, QKeyEvent)
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            self._commit_input()
            return True
        return super().eventFilter(watched, event)

    def _commit_input(self) -> None:
        value = self._input.text().strip()
        if value:
            self.add_tag(value)
            self._input.clear()

    def add_tag(self, value: str) -> bool:
        value = value.strip()
        if not value or value in self._chips:
            return False

        chip = QFrame()
        chip.setObjectName("profileTagChip")
        chip_layout = QHBoxLayout(chip)
        chip_layout.setContentsMargins(7, 2, 3, 2)
        chip_layout.setSpacing(2)
        chip_layout.addWidget(QLabel(value))
        remove = QPushButton("×")
        remove.setFixedSize(18, 18)
        # QDialog 会把 autoDefault 按钮视为 Enter 的候选默认按钮。
        # 否则用户在输入框按 Enter 新增标签时，会同时点击第一个“×”。
        remove.setAutoDefault(False)
        remove.setToolTip(tr("删除"))
        remove.clicked.connect(lambda _checked, text=value: self.remove_tag(text))
        chip_layout.addWidget(remove)

        self._values.append(value)
        self._chips[value] = chip
        # 输入框始终位于标签之后。
        self._row.insertWidget(max(0, self._row.count() - 1), chip)
        self._content.adjustSize()
        self.updateGeometry()
        self.tags_changed.emit()
        return True

    def remove_tag(self, value: str) -> None:
        chip = self._chips.pop(value, None)
        if chip is None:
            return
        self._values.remove(value)
        self._row.removeWidget(chip)
        chip.deleteLater()
        self._content.adjustSize()
        self.updateGeometry()
        self.tags_changed.emit()

    def tags(self) -> list[str]:
        return list(self._values)

    def set_tags(self, values: list[str]) -> None:
        """整体替换标签；程序化回填不发变更信号，避免触发调用方的保存。"""
        blocked = self.blockSignals(True)
        try:
            for value in list(self._values):
                self.remove_tag(value)
            for value in values:
                self.add_tag(value)
        finally:
            self.blockSignals(blocked)
