"""跨用户备战方案管理；选择编辑对象不改变主界面的活动用户或方案。"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr
from .....ui.button_styles import apply_button_style
from ...config import GameConfigManager, get_game_config
from ...core.loadout import LoadoutRepository, resolve_school
from .plan_create_dialog import PlanCreateDialog


class PlanManagerDialog(QDialog):
    """管理各用户的方案定义、显示顺序；方案内容双击后整体编辑。"""

    def __init__(self, usernames: list[str], current_user: str,
                 users_dir: Path | None = None, parent=None,
                 game_config: GameConfigManager | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("备战方案管理"))
        self.setMinimumSize(900, 520)
        self._users_dir = users_dir
        self._game_config = game_config or get_game_config()
        self.changed_users: set[str] = set()
        self._loading = False

        layout = QVBoxLayout(self)
        splitter = QSplitter()
        layout.addWidget(splitter)

        self._users = QListWidget()
        self._users.addItems(usernames)
        self._users.currentTextChanged.connect(self._load_user)
        splitter.addWidget(self._users)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        toolbar = QHBoxLayout()
        for label, name, callback, variant in (
            (tr("新建"), "_add_button", self._create_plan, "action"),
            (tr("删除"), "_delete_button", self._delete_plan, "danger"),
            (tr("上移"), "_up_button", lambda: self._move_plan(-1), "neutral"),
            (tr("下移"), "_down_button", lambda: self._move_plan(1), "neutral"),
        ):
            button = QPushButton(label)
            apply_button_style(button, variant=variant)
            button.clicked.connect(callback)
            setattr(self, name, button)
            toolbar.addWidget(button)
        toolbar.addStretch()
        right_layout.addLayout(toolbar)
        right_layout.addWidget(QLabel(tr("双击方案可编辑名称、流派、武学与玩法")))

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels([
            tr("名称"), tr("流派"), tr("主武学"), tr("副武学"), tr("玩法")])
        header = self._table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        vertical_header = self._table.verticalHeader()
        assert vertical_header is not None
        vertical_header.setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._update_actions)
        self._table.cellDoubleClicked.connect(self._edit_plan)
        right_layout.addWidget(self._table)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([180, 720])

        if usernames:
            index = usernames.index(current_user) if current_user in usernames else 0
            self._users.setCurrentRow(index)
        self._update_actions()

    def _repo(self) -> LoadoutRepository | None:
        username = self._users.currentItem()
        return (LoadoutRepository(username.text(), self._users_dir)
                if username is not None else None)

    def _selected_plan_id(self) -> str:
        row = self._table.currentRow()
        item = self._table.item(row, 0) if row >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _load_user(self, _name: str = "", *, selected_id: str = "") -> None:
        repo = self._repo()
        self._loading = True
        self._table.setRowCount(0)
        if repo is not None:
            state = repo.load()
            schools = self._game_config.get_schools()
            for pid in state.ordered_plan_ids():
                plan = state.plans[pid]
                row = self._table.rowCount()
                self._table.insertRow(row)
                values = [
                    plan.name, resolve_school(plan.main_martial_art,
                                              plan.sub_martial_art, schools) or tr("自定义"),
                    plan.main_martial_art, plan.sub_martial_art,
                    plan.playstyle or "-",
                ]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled
                                  | Qt.ItemFlag.ItemIsSelectable)
                    if col == 0:
                        item.setData(Qt.ItemDataRole.UserRole, pid)
                        if pid == state.active_plan_id:
                            item.setText(f"★ {plan.name}")
                            item.setToolTip(tr("该用户当前使用的方案"))
                    self._table.setItem(row, col, item)
                if pid == selected_id:
                    self._table.selectRow(row)
        self._loading = False
        self._update_actions()

    def _update_actions(self) -> None:
        if self._loading:
            return
        repo = self._repo()
        row = self._table.currentRow()
        count = self._table.rowCount()
        self._add_button.setEnabled(repo is not None)
        self._delete_button.setEnabled(row >= 0 and count > 1)
        self._up_button.setEnabled(row > 0)
        self._down_button.setEnabled(0 <= row < count - 1)

    def _mark_changed(self) -> None:
        user = self._users.currentItem()
        if user is not None:
            self.changed_users.add(user.text())

    def _create_plan(self) -> None:
        repo = self._repo()
        if repo is None:
            return
        dialog = PlanCreateDialog(self._game_config, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            plan = repo.create_plan(
                dialog.plan_name, dialog.main_art, dialog.sub_art,
                playstyle=dialog.playstyle, activate=False)
        except Exception as exc:
            QMessageBox.warning(self, tr("新建失败"), str(exc))
            return
        self._mark_changed()
        self._load_user(selected_id=plan.id)

    def _edit_plan(self, row: int, _column: int) -> None:
        repo = self._repo()
        if repo is None:
            return
        item = self._table.item(row, 0)
        pid = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
        plan = repo.load().plans.get(pid)
        if plan is None:
            self._load_user()
            return
        dialog = PlanCreateDialog(self._game_config, self, plan=plan)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            repo.configure_plan(
                pid, name=dialog.plan_name,
                main_martial_art=dialog.main_art,
                sub_martial_art=dialog.sub_art,
                playstyle=dialog.playstyle)
        except Exception as exc:
            QMessageBox.warning(self, tr("保存失败"), str(exc))
            return
        self._mark_changed()
        self._load_user(selected_id=pid)

    def _delete_plan(self) -> None:
        repo = self._repo()
        pid = self._selected_plan_id()
        if repo is None or not pid:
            return
        state = repo.load()
        if len(state.plans) <= 1 or pid not in state.plans:
            return
        plan = state.plans[pid]
        references = sum(bool(fp) for fp in plan.equipment.values())
        answer = QMessageBox.question(
            self, tr("删除备战方案"),
            tr("确定删除「{name}」吗？该方案的 {count} 个装备引用会解除，"
               "公共装备池中的装备不会删除。").format(
                   name=plan.name, count=references),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            repo.delete_plan(pid)
        except Exception as exc:
            QMessageBox.warning(self, tr("删除失败"), str(exc))
            return
        self._mark_changed()
        self._load_user()

    def _move_plan(self, offset: int) -> None:
        repo = self._repo()
        pid = self._selected_plan_id()
        if repo is None or not pid:
            return
        try:
            repo.move_plan(pid, offset)
        except Exception as exc:
            QMessageBox.warning(self, tr("排序失败"), str(exc))
            return
        self._mark_changed()
        self._load_user(selected_id=pid)
