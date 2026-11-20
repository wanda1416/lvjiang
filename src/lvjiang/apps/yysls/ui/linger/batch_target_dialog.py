"""批量绑定：把选中的多条来源统一绑到同一个配额 key（或解绑回固定）。

逐条配周期 key 太啰嗦——「百珍、不肝、地华」这类本来就是同一个周期账，批量一次改完
才符合实际用法。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr, tr_label
from .....ui.widgets.button_styles import apply_button_style
from .item_dialog import candidate_targets


class LingerBatchTargetDialog(QDialog):
    """选一个目标，应用到所有选中条目。"""

    def __init__(self, count: int, current: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{tr('批量绑定')} · {count}")
        self.setModal(True)
        self.setMinimumWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(12)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel(tr_label("目标")))
        self.target_combo = QComboBox(self)
        self.target_combo.addItem(tr("跟随同步模型"), "")
        for value, label in candidate_targets():
            self.target_combo.addItem(label, value)
        index = self.target_combo.findData(current)
        self.target_combo.setCurrentIndex(index if index >= 0 else 0)
        row.addWidget(self.target_combo, 1)
        outer.addLayout(row)

        buttons = QDialogButtonBox(self)
        ok = buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        if ok is not None:
            ok.setText(tr("确定"))
            apply_button_style(ok, variant="action")
        if cancel is not None:
            cancel.setText(tr("取消"))
            apply_button_style(cancel, variant="neutral")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def target(self) -> str:
        return str(self.target_combo.currentData() or "")
