"""新建备战方案对话框。

必须同时绑定主武学与副武学，且两者组成的武学集合需匹配流派。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
)

from .....i18n import tr
from .....ui.button_styles import apply_dialog_button_box_style
from ...core.loadout import resolve_school


class PlanCreateDialog(QDialog):
    """新建方案对话框：必须同时绑定主武学与副武学，且组合需匹配流派。"""

    def __init__(self, schools: dict, martial_arts, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("新建方案"))
        self._schools = schools
        form = QFormLayout(self)
        self._edit_name = QLineEdit()
        form.addRow(tr("方案名称:"), self._edit_name)
        # 候选来自独立武学配置；流派仅负责校验两门武学的无序组合。
        martial_arts = list(dict.fromkeys(
            str(name).strip() for name in martial_arts if str(name).strip()
        ))
        self._combo_main = QComboBox()
        self._combo_main.addItems(martial_arts)
        form.addRow(tr("主武学:"), self._combo_main)
        self._combo_sub = QComboBox()
        self._combo_sub.addItems(martial_arts)
        form.addRow(tr("副武学:"), self._combo_sub)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        apply_dialog_button_box_style(buttons)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _validate_and_accept(self):
        main_art = self._combo_main.currentText()
        sub_art = self._combo_sub.currentText()
        if not main_art or not sub_art:
            QMessageBox.warning(
                self, tr("新建方案"), tr("必须同时绑定主武学和副武学"))
            return
        school = resolve_school(main_art, sub_art, self._schools)
        if school is None:
            QMessageBox.warning(
                self, tr("新建方案"),
                tr("两门武学组合无法匹配任何流派，请重新选择"))
            return
        self.accept()

    @property
    def plan_name(self) -> str:
        return self._edit_name.text().strip()

    @property
    def main_art(self) -> str:
        return self._combo_main.currentText()

    @property
    def sub_art(self) -> str:
        return self._combo_sub.currentText()
