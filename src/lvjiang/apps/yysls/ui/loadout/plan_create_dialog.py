"""新建备战方案：流派用于快速填充武学，玩法按武学无序组合筛选。"""
from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker
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
from ...config import GameConfigManager
from ...core.loadout import LoadoutPlan, resolve_school
from ..layout_helpers import fit_combo_to_contents


class PlanCreateDialog(QDialog):
    """流派是武学组合的便捷入口，不是单独持久化的方案字段。"""

    def __init__(self, game_config: GameConfigManager, parent=None,
                 *, plan: LoadoutPlan | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("编辑方案") if plan else tr("新建方案"))
        self._game_config = game_config
        self._schools = game_config.get_schools()
        self._editing_plan = plan
        self._preferred_playstyle = ""
        form = QFormLayout(self)
        self._edit_name = QLineEdit()
        form.addRow(tr("方案名称:"), self._edit_name)

        self._combo_school = QComboBox()
        self._combo_school.addItem(tr("不选择流派"), "")
        for school in self._schools:
            self._combo_school.addItem(school, school)
        fit_combo_to_contents(self._combo_school, minimum=160)
        self._combo_school.currentIndexChanged.connect(self._on_school_changed)
        form.addRow(tr("流派:"), self._combo_school)

        martial_arts = list(game_config.get_martial_arts())
        if plan is not None:
            for art in (plan.main_martial_art, plan.sub_martial_art):
                if art and art not in martial_arts:
                    martial_arts.append(art)
        self._combo_main = QComboBox()
        self._combo_main.addItems([""] + martial_arts)
        fit_combo_to_contents(self._combo_main, minimum=160)
        self._combo_main.currentIndexChanged.connect(self._on_arts_changed)
        form.addRow(tr("主武学:"), self._combo_main)
        self._combo_sub = QComboBox()
        self._combo_sub.addItems([""] + martial_arts)
        fit_combo_to_contents(self._combo_sub, minimum=160)
        self._combo_sub.currentIndexChanged.connect(self._on_arts_changed)
        form.addRow(tr("副武学:"), self._combo_sub)

        self._combo_playstyle = QComboBox()
        self._combo_playstyle.currentIndexChanged.connect(
            self._on_playstyle_changed)
        form.addRow(tr("玩法:"), self._combo_playstyle)
        self._refresh_playstyles()

        if plan is not None:
            self._edit_name.setText(plan.name)
            with QSignalBlocker(self._combo_main), QSignalBlocker(self._combo_sub):
                self._combo_main.setCurrentText(plan.main_martial_art)
                self._combo_sub.setCurrentText(plan.sub_martial_art)
            school = resolve_school(plan.main_martial_art,
                                    plan.sub_martial_art, self._schools) or ""
            with QSignalBlocker(self._combo_school):
                self._combo_school.setCurrentIndex(max(
                    self._combo_school.findData(school), 0))
            self._combo_main.setEnabled(not school)
            self._combo_sub.setEnabled(not school)
            self._preferred_playstyle = plan.playstyle
            self._refresh_playstyles()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        apply_dialog_button_box_style(buttons)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _on_school_changed(self, _index: int) -> None:
        school = self._combo_school.currentData()
        self._combo_main.setEnabled(not school)
        self._combo_sub.setEnabled(not school)
        if school:
            config = self._schools[school]
            with QSignalBlocker(self._combo_main), QSignalBlocker(self._combo_sub):
                self._combo_main.setCurrentText(
                    str((config.get("main") or {}).get("martial_art") or ""))
                self._combo_sub.setCurrentText(
                    str((config.get("sub") or {}).get("martial_art") or ""))
        self._refresh_playstyles()

    def _on_arts_changed(self, _index: int) -> None:
        school = self._combo_school.currentData()
        if school:
            config = self._schools[school]
            configured = {
                str((config.get(side) or {}).get("martial_art") or "")
                for side in ("main", "sub")
            }
            if {self.main_art, self.sub_art} != configured:
                # 手动改动流派的预置组合后，转入自由选武学模式。
                with QSignalBlocker(self._combo_school):
                    self._combo_school.setCurrentIndex(0)
        self._refresh_playstyles()

    def _refresh_playstyles(self) -> None:
        names = self._game_config.get_playstyles_for_arts(
            [self.main_art, self.sub_art])
        school = self._combo_school.currentData()
        if school:
            names = [name for name in names
                     if (self._game_config.get_playstyle(name) or {}).get(
                         "school") == school]
        legacy_style = ""
        plan = self._editing_plan
        if (plan is not None and plan.playstyle
                and {self.main_art, self.sub_art}
                == {plan.main_martial_art, plan.sub_martial_art}
                and plan.playstyle not in names):
            legacy_style = plan.playstyle
        with QSignalBlocker(self._combo_playstyle):
            self._combo_playstyle.clear()
            self._combo_playstyle.addItem(tr("不选择玩法"), "")
            for name in names:
                self._combo_playstyle.addItem(name, name)
            if legacy_style:
                self._combo_playstyle.addItem(
                    tr("{name}（当前不匹配）").format(name=legacy_style),
                    legacy_style)
            self._combo_playstyle.setCurrentIndex(max(
                self._combo_playstyle.findData(self._preferred_playstyle), 0))
        fit_combo_to_contents(self._combo_playstyle, minimum=160)
        self._combo_playstyle.setEnabled(bool(names or legacy_style))

    def _on_playstyle_changed(self, _index: int) -> None:
        self._preferred_playstyle = self.playstyle

    def _validate_and_accept(self) -> None:
        if not self.main_art or not self.sub_art:
            QMessageBox.warning(
                self, tr("新建方案"), tr("必须同时绑定主武学和副武学"))
            return
        if self.main_art == self.sub_art:
            QMessageBox.warning(
                self, tr("新建方案"), tr("主武学和副武学不能相同"))
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

    @property
    def playstyle(self) -> str:
        return str(self._combo_playstyle.currentData() or "")
