"""分类管理：改名 / 清除。

分类只是条目的展示前缀，平时不需要管理——新建或编辑条目时在下拉里输入新名字
就是添加。这个对话框只处理两件事：某个分类打错了想统一改名、某个分类不想要了
想把条目退回无类型。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr, tr_label
from .....ui.widgets.button_styles import apply_button_style
from .....ui.widgets.combo_box import AutoWidthComboBox, ComboWidthMode
from ...core.linger import known_categories, remove_category, rename_category


class LingerCategoryDialog(QDialog):
    """对在用的分类做改名或清除。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("管理分类"))
        self.setModal(True)
        self.setMinimumWidth(460)
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(12)

        hint = QLabel(tr(
            "分类在编辑条目时直接输入新名字即可添加；这里改名作用于全部条目，"
            "清除则把这些条目退回无类型。"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        outer.addWidget(hint)

        rename_row = QHBoxLayout()
        rename_row.addWidget(QLabel(tr_label("类型")))
        self.rename_from = AutoWidthComboBox(self, width_mode=ComboWidthMode.POPUP)
        # 首项留空：打开时不默认选中任何分类，避免一键作用到意外目标
        self.rename_from.addItem("")
        self.rename_from.addItems(known_categories())
        self.rename_from.setCurrentIndex(0)
        self.rename_from.currentTextChanged.connect(self._update_actions)
        rename_row.addWidget(self.rename_from, 1)
        self.rename_to = AutoWidthComboBox(self, width_mode=ComboWidthMode.POPUP)
        self.rename_to.setEditable(True)
        self.rename_to.currentTextChanged.connect(lambda _text: self._update_actions())
        rename_row.addWidget(self.rename_to, 1)
        outer.addLayout(rename_row)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.rename_button = QPushButton(tr("改名"))
        apply_button_style(self.rename_button, variant="neutral")
        self.rename_button.setEnabled(False)
        self.rename_button.clicked.connect(self._rename)
        button_row.addWidget(self.rename_button)
        self.remove_button = QPushButton(tr("清除分类"))
        apply_button_style(self.remove_button, variant="danger")
        self.remove_button.setEnabled(False)
        self.remove_button.clicked.connect(self._remove)
        button_row.addWidget(self.remove_button)
        close_button = QPushButton(tr("关闭"))
        apply_button_style(close_button, variant="neutral")
        close_button.clicked.connect(self.close)
        button_row.addWidget(close_button)
        outer.addLayout(button_row)

    def _update_actions(self) -> None:
        chosen = bool(self.rename_from.currentText().strip())
        self.rename_button.setEnabled(chosen and bool(self.rename_to.currentText().strip()))
        self.remove_button.setEnabled(chosen)

    def _rename(self) -> None:
        old = self.rename_from.currentText().strip()
        new = self.rename_to.currentText().strip()
        if not old or not new:
            return
        rename_category(old, new)
        self._refresh()

    def _remove(self) -> None:
        name = self.rename_from.currentText().strip()
        if not name:
            return
        answer = QMessageBox.question(
            self, tr("清除分类"),
            tr("把「{name}」从全部条目上移除？条目本身保留，退回无类型。").format(name=name))
        if answer != QMessageBox.StandardButton.Yes:
            return
        remove_category(name)
        self._refresh()

    def _refresh(self) -> None:
        self.rename_from.blockSignals(True)
        self.rename_from.clear()
        self.rename_from.addItem("")
        self.rename_from.addItems(known_categories())
        self.rename_from.setCurrentIndex(0)
        self.rename_from.blockSignals(False)
        self._update_actions()
