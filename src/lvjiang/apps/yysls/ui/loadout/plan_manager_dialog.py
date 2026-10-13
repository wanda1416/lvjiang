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

from lvjiang.ui.layout_helpers import configure_navigation_list, fit_combo_to_contents

from .....i18n import tr
from .....ui.button_styles import apply_button_style
from ...config import GameConfigManager, get_game_config
from ...core.loadout import (
    LoadoutPlan,
    LoadoutRepository,
    resolve_school,
)
from ..domain_labels import combat_type_label
from ..game_settings.level_combo import LevelCombo
from .plan_create_dialog import PlanCreateDialog
from .plan_draft import PlanDraft
from .plan_table_delegate import (
    COL_MAIN_ART,
    COL_NAME,
    COL_SCHOOL,
    COL_SUB_ART,
    PlanFieldDelegate,
    locked_columns,
    locked_reason,
    plan_field_updates,
)


class PlanManagerDialog(QDialog):
    """管理各用户的方案定义、显示顺序；方案内容双击后逐项编辑。"""

    def __init__(self, usernames: list[str], current_user: str,
                 users_dir: Path | None = None, parent=None,
                 game_config: GameConfigManager | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("备战方案管理"))
        self.setMinimumSize(900, 520)
        self._users_dir = users_dir
        self._game_config = game_config or get_game_config()
        #: 已经保存过的用户；主界面据此决定要不要刷新。
        self.changed_users: set[str] = set()
        self._loading = False
        #: 每个用户一份未保存草稿：编辑只改这里，点「保存」才合并写回磁盘。
        self._drafts: dict[str, PlanDraft] = {}
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
        user_nav_width = min(
            320,
            max(
                190,
                max(
                    (self._users.fontMetrics().horizontalAdvance(username) + 32
                     for username in usernames),
                    default=0,
                ),
            ),
        )
        self._users.setMinimumWidth(user_nav_width)
        for index, username in enumerate(usernames):
            item = self._users.item(index)
            if item is not None:
                item.setToolTip(username)
        self._users.currentTextChanged.connect(self._load_user)
        splitter.addWidget(self._users)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        right_layout.setSpacing(10)
        world_level_row = QHBoxLayout()
        world_level_row.addWidget(QLabel(tr("个人世界等级：")))
        season_level = self._game_config.current_equip_level()
        follow_label = (
            tr("随赛季更新（{level}）").format(level=season_level)
            if season_level else tr("随赛季更新")
        )
        self._world_level_combo = LevelCombo(
            allow_empty=True, empty_label=follow_label)
        # 「随赛季更新（115）」比等级数字长得多，按文本实测宽度撑开，
        # 并把下拉箭头和边距算进去，否则这一项会被截断成「随赛季更…」。
        fit_combo_to_contents(self._world_level_combo, minimum=180)
        self._world_level_combo.setToolTip(tr(
            "个人等级决定毕业率计算使用的抗性，并限定可加载的最高方案等级。"
            "留空时自动跟随当前赛季等级。"))
        self._world_level_combo.currentIndexChanged.connect(
            self._on_world_level_changed)
        world_level_row.addWidget(self._world_level_combo)
        world_level_row.addStretch()
        right_layout.addLayout(world_level_row)
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
            tr("双击单元格即可逐项编辑；灰色单元格由流派决定")))
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
        # 名称、流派和两门武学都是四到六字的扫读重点，长度相近，一起均分富余
        # 宽度；玩法和对战类型文本固定很短，按内容收窄，不跟着窗口变宽。只把
        # 名称设成 Stretch 会让它独占几百像素的空白，另外五列挤在右侧。
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for col in (COL_NAME, COL_SCHOOL, COL_MAIN_ART, COL_SUB_ART):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        vertical_header = self._table.verticalHeader()
        assert vertical_header is not None
        vertical_header.setVisible(False)
        vertical_header.setDefaultSectionSize(
            max(28, self._table.fontMetrics().height() + 10))
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # 双击才进编辑：单击只负责选中，挑行时不会误触进入编辑态。Qt 的双击
        # 事件序列里第二次 press 被 DoubleClick 取代，只写 SelectedClicked
        # 会让双击完全进不去编辑，必须显式声明 DoubleClicked。
        self._table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed)
        self._table.setItemDelegate(PlanFieldDelegate(
            self._game_config, self._plan_at, self._commit_field, self._table))
        self._table.itemSelectionChanged.connect(self._update_actions)
        right_layout.addWidget(self._table, stretch=1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([user_nav_width, 900 - user_nav_width])

        bottom = QHBoxLayout()
        bottom.addStretch()
        save_button = QPushButton(tr("保存"))
        apply_button_style(save_button, variant="action")
        save_button.clicked.connect(self._save)
        save_button.setEnabled(False)
        self._save_button = save_button
        bottom.addWidget(save_button)
        close_button = QPushButton(tr("关闭"))
        apply_button_style(close_button, variant="neutral")
        close_button.clicked.connect(self.reject)
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

    def _draft(self) -> PlanDraft | None:
        """当前用户的草稿；第一次访问时从磁盘载入一份工作副本。

        草稿按用户名常驻，所以来回切换用户不会丢掉各自的未保存修改。
        """
        repo = self._repo()
        if repo is None:
            return None
        username = self._username()
        draft = self._drafts.get(username)
        if draft is None:
            draft = PlanDraft.load(repo)
            self._drafts[username] = draft
        return draft

    def _username(self) -> str:
        item = self._users.currentItem()
        return item.text() if item is not None else ""

    def _selected_plan_id(self) -> str:
        row = self._table.currentRow()
        item = self._table.item(row, 0) if row >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _plan_at(self, row: int) -> LoadoutPlan | None:
        """表格第 row 行当前对应的方案；委托据此构建编辑器。

        取 _load_user 本轮的快照，不重新读盘：委托每编辑一格要问三次（建
        编辑器、填初值、提交），每次 repo.load() 就是三遍读文件加解析。写回
        草稿后 _load_user 会整表重绘，表格和快照总是一起重建。
        """
        return self._row_plans[row] if 0 <= row < len(self._row_plans) else None

    def _commit_field(self, plan: LoadoutPlan, column: int,
                      value: str) -> None:
        """把一次列编辑写进草稿，然后整行重绘——联动结果只认草稿里的事实。"""
        draft = self._draft()
        if draft is None:
            return
        updates = plan_field_updates(
            self._game_config.get_schools(), plan, column, value)
        if updates:
            draft.update_fields(plan.id, updates)
        # 名称空串等无效输入不写入，重绘会让单元格回滚成原值。
        self._load_user(selected_id=plan.id)

    def _load_user(self, _name: str = "", *, selected_id: str = "") -> None:
        """用户切换或需要整表重绘时调用；数据一律取自草稿。"""
        draft = self._draft()
        self._loading = True
        self._table.setRowCount(0)
        self._row_plans = []
        if draft is not None:
            state = draft.state
            self._world_level_combo.blockSignals(True)
            self._world_level_combo.set_level(state.world_level)
            self._world_level_combo.blockSignals(False)
            self._world_level_combo.setEnabled(True)
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
                        # 名称列宽有限，被省略的长名字靠悬停提示看全。
                        item.setToolTip(plan.name)
                        if pid == state.active_plan_id:
                            item.setText(f"★ {plan.name}")
                            item.setToolTip(
                                f"{tr('该用户当前使用的方案')}\n{plan.name}")
                    self._table.setItem(row, col, item)
                if pid == selected_id:
                    self._table.selectRow(row)
        else:
            self._world_level_combo.blockSignals(True)
            self._world_level_combo.set_level(None)
            self._world_level_combo.blockSignals(False)
            self._world_level_combo.setEnabled(False)
        self._loading = False
        self._update_actions()

    def _on_world_level_changed(self, _index: int) -> None:
        if self._loading:
            return
        draft = self._draft()
        if draft is None:
            return
        draft.set_world_level(self._world_level_combo.get_level())
        self._update_actions()

    def _update_actions(self) -> None:
        if self._loading:
            return
        draft = self._draft()
        row = self._table.currentRow()
        count = self._table.rowCount()
        self._add_button.setEnabled(draft is not None)
        self._delete_button.setEnabled(row >= 0 and count > 1)
        self._up_button.setEnabled(row > 0)
        self._down_button.setEnabled(0 <= row < count - 1)
        self._save_button.setEnabled(bool(self._dirty_users()))

    def _dirty_users(self) -> list[str]:
        """有未保存修改的用户名。"""
        return [name for name, draft in self._drafts.items() if draft.dirty]

    def _save(self, _checked: bool = False) -> bool:
        """把所有草稿按字段合并写回；返回是否全部成功。"""
        failures: list[str] = []
        for username, draft in self._drafts.items():
            if not draft.dirty:
                continue
            try:
                draft.flush(LoadoutRepository(username, self._users_dir))
            except Exception as exc:  # noqa: BLE001 - 单用户失败不影响其它用户
                logger.error(f"保存备战方案失败（{username}）: {exc}")
                failures.append(f"{username}: {exc}")
                continue
            draft.accept()
            self.changed_users.add(username)
        if failures:
            QMessageBox.warning(self, tr("保存失败"), "\n".join(failures))
        self._update_actions()
        return not failures

    def _ask_unsaved(self) -> str:
        """询问未保存的修改怎么处置：save / discard / cancel。

        单独成方法是为了测试能直接给出答案，不必去驱动模态框。
        """
        count = len(self._dirty_users())
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(tr("未保存的修改"))
        box.setText(tr("{count} 个用户的备战方案还有未保存的修改，"
                       "要保存后再关闭吗？").format(count=count))
        save_button = box.addButton(
            tr("保存并关闭"), QMessageBox.ButtonRole.AcceptRole)
        discard_button = box.addButton(
            tr("放弃修改"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(tr("取消"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save_button:
            return "save"
        if clicked is discard_button:
            return "discard"
        return "cancel"

    def reject(self) -> None:
        """关闭前拦一道：有草稿就交给用户决定保存、放弃还是留下。"""
        if self._dirty_users():
            answer = self._ask_unsaved()
            if answer == "cancel":
                return
            if answer == "save" and not self._save():
                return
        super().reject()

    def _create_plan(self) -> None:
        draft = self._draft()
        if draft is None:
            return
        dialog = PlanCreateDialog(self._game_config, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            plan = draft.add_plan(
                name=dialog.plan_name, main_art=dialog.main_art,
                sub_art=dialog.sub_art, playstyle=dialog.playstyle,
                combat_type=dialog.combat_type)
        except Exception as exc:
            QMessageBox.warning(self, tr("新建失败"), str(exc))
            return
        self._load_user(selected_id=plan.id)

    def _delete_plan(self) -> None:
        draft = self._draft()
        pid = self._selected_plan_id()
        if draft is None or not pid:
            return
        plan = draft.state.plans.get(pid)
        if plan is None or len(draft.state.plans) <= 1:
            return
        references = sum(bool(fp) for fp in plan.equipment.values())
        answer = QMessageBox.question(
            self, tr("删除备战方案"),
            tr("确定删除「{name}」吗？该方案的 {count} 个装备引用会解除，"
               "公共装备池中的装备不会删除，保存后才会生效。").format(
                   name=plan.name, count=references),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            draft.remove_plan(pid)
        except Exception as exc:
            QMessageBox.warning(self, tr("删除失败"), str(exc))
            return
        self._load_user()

    def _move_plan(self, offset: int) -> None:
        draft = self._draft()
        pid = self._selected_plan_id()
        if draft is None or not pid:
            return
        try:
            draft.move_plan(pid, offset)
        except Exception as exc:
            QMessageBox.warning(self, tr("排序失败"), str(exc))
            return
        self._load_user(selected_id=pid)
