"""跨用户备战方案管理；选择编辑对象不改变主界面的活动用户或方案。"""
from __future__ import annotations

from pathlib import Path

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
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
from ...core.loadout import (
    LoadoutPlan,
    LoadoutRepository,
    resolve_school,
)
from ..domain_labels import combat_type_label
from ..layout_helpers import configure_navigation_list
from .plan_create_dialog import PlanCreateDialog
from .plan_table_delegate import (
    COL_NAME,
    PlanFieldDelegate,
    locked_columns,
    locked_reason,
    plan_field_updates,
)


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
        #: 表格各行对应的方案，与表格在 _load_user 里一起重建。
        self._row_plans: list[LoadoutPlan] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(12)
        splitter = QSplitter()
        layout.addWidget(splitter, stretch=1)

        self._users = QListWidget()
        configure_navigation_list(self._users, minimum_width=190)
        self._users.addItems(usernames)
        self._users.currentTextChanged.connect(self._load_user)
        splitter.addWidget(self._users)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        right_layout.setSpacing(10)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
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
        notice = QFrame()
        notice.setProperty("surface", "card")
        notice_layout = QVBoxLayout(notice)
        notice_layout.setContentsMargins(12, 8, 12, 8)
        notice_layout.setSpacing(4)
        notice_layout.addWidget(QLabel(
            tr("选中一行后再单击单元格即可逐项编辑；灰色单元格由流派决定")))
        hint = QLabel(tr("PVP 方案不参与智能调律：目前没有 PVP 调律方案，"
                         "一起加载会让够不到 PVE 标准的装备被判成有提升"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        notice_layout.addWidget(hint)
        right_layout.addWidget(notice)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            tr("名称"), tr("流派"), tr("主武学"), tr("副武学"), tr("玩法"),
            tr("对战类型")])
        header = self._table.horizontalHeader()
        assert header is not None
        # 名称吃掉多余宽度，其余按内容收窄；六列平分会让「对战类型」和
        # 「名称」一样宽，扫读时抓不到重点。
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        vertical_header = self._table.verticalHeader()
        assert vertical_header is not None
        vertical_header.setVisible(False)
        vertical_header.setDefaultSectionSize(
            max(28, self._table.fontMetrics().height() + 10))
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # 选中后再单击才进编辑：单击即编辑容易在挑行时误触。
        self._table.setEditTriggers(
            QTableWidget.EditTrigger.SelectedClicked
            | QTableWidget.EditTrigger.EditKeyPressed)
        self._table.setItemDelegate(PlanFieldDelegate(
            self._game_config, self._plan_at, self._commit_field, self._table))
        self._table.itemSelectionChanged.connect(self._update_actions)
        right_layout.addWidget(self._table, stretch=1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([190, 710])

        bottom = QHBoxLayout()
        bottom.addStretch()
        close_button = QPushButton(tr("关闭"))
        apply_button_style(close_button, variant="neutral")
        close_button.clicked.connect(self.accept)
        bottom.addWidget(close_button)
        layout.addLayout(bottom)

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

    def _plan_at(self, row: int) -> LoadoutPlan | None:
        """表格第 row 行当前对应的方案；委托据此构建编辑器。

        取 _load_user 本轮的快照，不重新读盘：委托每编辑一格要问三次（建
        编辑器、填初值、提交），每次 repo.load() 就是三遍读文件加解析。写回
        后 _commit_field 会走 _load_user，表格和快照总是一起重建。
        """
        return self._row_plans[row] if 0 <= row < len(self._row_plans) else None

    def _commit_field(self, plan: LoadoutPlan, column: int,
                      value: str) -> None:
        """写回一次列编辑，然后整行重读——联动结果只认仓储里的事实。"""
        repo = self._repo()
        if repo is None:
            return
        updates = plan_field_updates(
            self._game_config.get_schools(), plan, column, value)
        if not updates:
            # 名称空串等无效输入：放弃写入，让单元格回滚成原值。
            self._load_user(selected_id=plan.id)
            return
        try:
            repo.configure_plan(plan.id, **updates)
        except Exception as exc:  # noqa: BLE001 - 单次编辑失败不该关掉对话框
            logger.error(f"编辑备战方案失败: {exc}")
            QMessageBox.warning(self, tr("保存失败"), str(exc))
        self._mark_changed()
        self._load_user(selected_id=plan.id)

    def _load_user(self, _name: str = "", *, selected_id: str = "") -> None:
        repo = self._repo()
        self._loading = True
        self._table.setRowCount(0)
        self._row_plans = []
        if repo is not None:
            state = repo.load()
            schools = self._game_config.get_schools()
            for pid in state.ordered_plan_ids():
                plan = state.plans[pid]
                row = self._table.rowCount()
                self._table.insertRow(row)
                self._row_plans.append(plan)
                values = [
                    plan.name, resolve_school(plan.main_martial_art,
                                              plan.sub_martial_art, schools) or tr("自定义"),
                    plan.main_martial_art, plan.sub_martial_art,
                    plan.playstyle or "-",
                    combat_type_label(plan.combat_type),
                ]
                locked = locked_columns(schools, plan)
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    flags = (Qt.ItemFlag.ItemIsEnabled
                             | Qt.ItemFlag.ItemIsSelectable)
                    if col in locked:
                        # 不可编辑就画成灰底灰字：只靠 flags() 拦截是静默的，
                        # 用户点不动又看不出原因。
                        item.setBackground(
                            self.palette().alternateBase())
                        item.setForeground(
                            self.palette().brush(
                                QPalette.ColorGroup.Disabled,
                                QPalette.ColorRole.Text))
                        item.setToolTip(locked_reason())
                    else:
                        flags |= Qt.ItemFlag.ItemIsEditable
                    item.setFlags(flags)
                    if col == COL_NAME:
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
                playstyle=dialog.playstyle,
                combat_type=dialog.combat_type, activate=False)
        except Exception as exc:
            QMessageBox.warning(self, tr("新建失败"), str(exc))
            return
        self._mark_changed()
        self._load_user(selected_id=plan.id)

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
