"""工具 → 任务参数：按用户或按任务浏览、编辑日常任务的参数。

两个视图是同一份数据的两个切面：

- **任务视图**：顶部选任务，左侧是「全局参数」+ 全部用户；
- **用户视图**：顶部选用户，左侧是该用户的全部日常任务。

右侧永远是"当前这一对"的开关与参数，顺序固定：跳过该任务 → 使用独立参数 →
参数表单。数据源与日常页完全一致，没有任何副本：

- 全局参数：``session.json`` 的 ``wf_configs[task_id]``；
- 用户独立参数：用户资料的 ``workflow_params[task_id]``；
- 跳过声明：用户资料的 ``skipped_task_ids``。

编辑即落盘（文本输入按 250ms 合并），所以底部只有「关闭」——它就是日常页
参数面板的另一个投影，不是"打开快照、点保存整体覆盖"的配置对话框。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...core.config.wf_configs import get_wf_config, update_wf_config
from ...core.task_params import (
    merge_task_params,
    parameters_for_env,
    resolve_task_params,
)
from ...core.users.document import (
    delete_user_workflow_params,
    get_user_skipped_tasks,
    get_user_workflow_params,
    set_user_skipped_task,
    set_user_workflow_params,
)
from ...i18n import tr
from ...workflows.discovery import script_display_name, script_supports_env
from ..widgets.button_styles import (
    apply_compact_button_style,
    apply_dialog_button_box_style,
)
from ..widgets.combo_box import AutoWidthComboBox
from ..widgets.task_param_form import TaskParamForm
from ..widgets.theme import get_theme_manager

#: 「全局参数」伪条目的展示名；它不是一个用户，作用域是任务。
_GLOBAL_LABEL = tr("全局参数")

#: 跳过声明造成的跳过状态在列表里的标记。
_SKIP_MARK = "⊘"

#: 左区默认宽度按多少个全角汉字计算。
_LIST_WIDTH_CHARS = 14


class TaskParamsDialog(QDialog):
    """「全部用户 × 全部日常任务」的参数与跳过声明编辑器。"""

    def __init__(self, owner, parent: QWidget | None = None):
        super().__init__(parent)
        self._owner = owner
        self._view = "task"
        #: 当前编辑对象：``("global", task_id)`` 或 ``(username, task_id)``。
        self._current: tuple[str, str] | None = None
        self._current_task_id = ""
        self._baseline: dict = {}
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(250)
        self._save_timer.timeout.connect(self._save_current)

        self.setWindowTitle(tr("任务参数"))
        self.resize(900, 620)
        self._build_ui()
        # 灰色字取自主题 token，换主题后列表要按新配色重画。
        get_theme_manager().theme_changed.connect(self._on_theme_changed)
        self._reload_sources()
        self._reload_list()

    def _on_theme_changed(self, *_args) -> None:
        self._reload_list(keep=True)

    # ─── 数据源 ───────────────────────────────────────────

    def _users_dir(self) -> Path | None:
        manager = getattr(self._owner, "_user_manager", None)
        return getattr(manager, "_users_dir", None)

    def _run_env(self) -> str:
        getter = getattr(self._owner, "_selected_run_env", None)
        return str(getter() if callable(getter) else "")

    def users(self) -> list[str]:
        manager = getattr(self._owner, "_user_manager", None)
        return list(manager.list_users()) if manager is not None else []

    def configs(self) -> list[dict]:
        """当前环境下可用的日常脚本（专用脚本不在本工具范围内）。"""
        env = self._run_env()
        sources = getattr(self._owner, "_workflow_configs", []) or []
        return [
            cfg for cfg in sources
            if cfg.get("scope", "daily") == "daily"
            and script_supports_env(cfg, env)
        ]

    def _config(self, task_id: str) -> dict | None:
        return next(
            (cfg for cfg in self.configs() if str(cfg.get("id")) == task_id),
            None,
        )

    # ─── UI ───────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        # 任务视图是默认视图，排在左侧第一位；用户视图在它右边。
        self._task_view_radio = QRadioButton(tr("任务视图"))
        self._user_view_radio = QRadioButton(tr("用户视图"))
        self._task_view_radio.setChecked(True)
        for radio in (self._task_view_radio, self._user_view_radio):
            radio.setAutoExclusive(True)
            radio.toggled.connect(self._on_view_changed)
            top.addWidget(radio)
        top.addSpacing(12)
        self._user_combo = AutoWidthComboBox()
        self._user_combo.setObjectName("task_params_user")
        self._user_combo.setMinimumWidth(180)
        self._task_combo = AutoWidthComboBox()
        self._task_combo.setObjectName("task_params_task")
        self._task_combo.setMinimumWidth(220)
        top.addWidget(self._user_combo, 1)
        top.addWidget(self._task_combo, 1)
        # 上一个/下一个：切的是当前视图顶部那个下拉（任务视图切任务，
        # 用户视图切用户）。← → 也是同一动作，文本输入控件里保留光标移动。
        self._previous_button = QPushButton(tr("上一个"))
        self._previous_button.setObjectName("task_params_previous")
        self._next_button = QPushButton(tr("下一个"))
        self._next_button.setObjectName("task_params_next")
        for button in (self._previous_button, self._next_button):
            button.setAutoDefault(False)
            button.setFixedHeight(24)
            top.addWidget(button)
        apply_compact_button_style(
            self._previous_button, self._next_button, variant="neutral")
        self._previous_button.clicked.connect(lambda: self._step(-1))
        self._next_button.clicked.connect(lambda: self._step(1))
        layout.addLayout(top)
        self._user_combo.currentIndexChanged.connect(self._on_combo_changed)
        self._task_combo.currentIndexChanged.connect(self._on_combo_changed)
        self._apply_view_visibility()

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        self._list = QListWidget()
        self._list.setObjectName("task_params_list")
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        # 条目之间留出行距，并对列表整体 +2 号字：默认字号下用户名与任务名
        # 挤在一起，扫读很容易看错行。
        self._list.setSpacing(4)
        # 只保留下限：默认宽度按 14 个汉字留位（见 _apply_list_width），
        # 用户拖动分隔条后不再回写。
        self._list.setMinimumWidth(120)
        list_font = self._list.font()
        list_font.setPointSize(list_font.pointSize() + 2)
        self._list.setFont(list_font)
        self._list.currentRowChanged.connect(self._on_row_changed)
        left_layout.addWidget(self._list, 1)

        self._splitter = splitter
        splitter.addWidget(left)
        splitter.addWidget(self._build_detail())
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 700])
        layout.addWidget(splitter, 1)

        # 提示分两处：方向键说明贴在顶部导航下方，"改动立即生效"放到最底下
        # 与「关闭」同一行的最左侧——它是一句状态说明，不占一行版面。
        self._nav_hint = QLabel(tr("方向键 ← / → 切换上一个 / 下一个"))
        self._nav_hint.setWordWrap(True)
        layout.addWidget(self._nav_hint)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(8)
        self._hint = QLabel(tr("改动立即生效"))
        self._hint.setWordWrap(True)
        bottom_row.addWidget(self._hint)
        bottom_row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        apply_dialog_button_box_style(buttons)
        bottom_row.addWidget(buttons)
        layout.addLayout(bottom_row)

    def _build_detail(self) -> QWidget:
        box = QGroupBox(tr("任务参数"))
        self._detail_box = box
        outer = QVBoxLayout(box)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        self._source_label = QLabel()
        self._source_label.setObjectName("parameter_source")
        outer.addWidget(self._source_label)

        self._skip_check = QCheckBox(tr("当前用户跳过该任务"))
        self._skip_check.setObjectName("task_params_skip_task")
        self._skip_check.setToolTip(tr(
            "勾选后该用户在批量执行中跳过此任务，任务列仍然保留、状态记为跳过"))
        self._skip_check.toggled.connect(self._on_skip_toggled)
        outer.addWidget(self._skip_check)

        self._override_check = QCheckBox(tr("当前用户使用独立参数"))
        self._override_check.setObjectName("task_params_override")
        self._override_check.toggled.connect(self._on_override_toggled)
        outer.addWidget(self._override_check)

        body = QWidget()
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._form = TaskParamForm(body)
        self._form.value_changed.connect(self._on_form_changed)
        self._form.text_edited.connect(lambda _values: self._save_timer.start())
        self._body_layout.addWidget(self._form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        return box

    # ─── 源切换 ───────────────────────────────────────────

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        # 非模态窗口活得比一次构造久：用户、脚本、运行环境都可能在打开期间变化。
        self._reload_sources()
        self._reload_list(keep=True)
        super().showEvent(event)

    def _reload_sources(self) -> None:
        """重新同步用户与脚本候选，尽量保留当前选择。"""
        self._user_combo.blockSignals(True)
        try:
            current_user = self._user_combo.currentData()
            self._user_combo.clear()
            for username in self.users():
                self._user_combo.addItem(str(username), str(username))
            index = self._user_combo.findData(current_user)
            self._user_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self._user_combo.blockSignals(False)
        self._task_combo.blockSignals(True)
        try:
            current_task = self._task_combo.currentData()
            self._task_combo.clear()
            for cfg in self.configs():
                self._task_combo.addItem(
                    script_display_name(cfg), str(cfg.get("id") or ""))
            index = self._task_combo.findData(current_task)
            self._task_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self._task_combo.blockSignals(False)
        self._refresh_step_buttons()

    def _active_combo(self):
        """当前视图顶部那个下拉：用户视图是用户，任务视图是任务。"""
        return self._user_combo if self._view == "user" else self._task_combo

    def _step(self, offset: int) -> None:
        """切到上一个/下一个顶部下拉项；两端不动。"""
        combo = self._active_combo()
        target = combo.currentIndex() + offset
        if 0 <= target < combo.count():
            combo.setCurrentIndex(target)

    def _refresh_step_buttons(self) -> None:
        combo = self._active_combo()
        index = combo.currentIndex()
        self._previous_button.setEnabled(index > 0)
        self._next_button.setEnabled(0 <= index < combo.count() - 1)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        """←/→ 等于上一个/下一个。

        文本输入控件（输入框、多行文本、数字框）里方向键仍是光标移动，
        否则改参数时连光标都动不了。
        """
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            focus = self.focusWidget()
            if not isinstance(focus, (QLineEdit, QPlainTextEdit, QAbstractSpinBox)):
                self._step(-1 if event.key() == Qt.Key.Key_Left else 1)
                event.accept()
                return
        super().keyPressEvent(event)

    def _apply_view_visibility(self) -> None:
        """只显示当前视图那一维的下拉。

        另一个维度的下拉跟这个视图无关，留着只会让人以为它也在生效——例如
        任务视图里那排用户名没有任何作用。
        """
        self._user_combo.setVisible(self._view == "user")
        self._task_combo.setVisible(self._view == "task")

    def _on_view_changed(self, *_args) -> None:
        self._view = "user" if self._user_view_radio.isChecked() else "task"
        self._save_timer.stop()
        self._save_current()
        self._apply_view_visibility()
        self._refresh_step_buttons()
        self._reload_list(keep=False)

    def _on_combo_changed(self, *_args) -> None:
        self._save_timer.stop()
        self._save_current()
        self._refresh_step_buttons()
        # 保留左列表里选中的那个对象：上一个/下一个只换另一维，不该把选中行
        # 打回第一行（否则每按一次方向键都要重新找人）。
        self._reload_list(keep=True)

    # ─── 左侧列表 ─────────────────────────────────────────

    def _user_sets(self, username: str) -> tuple[set[str], set[str]]:
        """一次读盘取出该用户「跳过的任务」与「使用独立参数的任务」。"""
        if not username:
            return set(), set()
        from ...core.users.document import load_user_metadata
        user = load_user_metadata(username, self._users_dir())
        if user is None:
            return set(), set()
        return set(user.skipped_task_ids), set(user.workflow_params)

    def _user_marks(self, username: str, task_id: str) -> tuple[bool, bool]:
        """(是否声明跳过, 是否使用独立参数)。"""
        skipped_tasks, overridden_tasks = self._user_sets(username)
        return task_id in skipped_tasks, task_id in overridden_tasks

    def _style_item(
        self, item: QListWidgetItem, *, bold: bool, skipped: bool,
    ) -> None:
        """列表项样式：使用独立参数加粗，声明跳过用灰色字。

        两个事实可以同时成立（跳过后独立参数值仍然保留），所以加粗与灰色
        互不替代；跳过的条目另加前缀标记与 tooltip，不单靠颜色表达状态。
        """
        font = item.font()
        font.setBold(bold)
        item.setFont(font)
        if skipped:
            tokens = get_theme_manager().tokens
            item.setForeground(QBrush(QColor(tokens.text_disabled)))

    @staticmethod
    def _user_tooltip(skipped: bool, overridden: bool) -> str:
        lines = []
        if skipped:
            lines.append(tr("该用户声明跳过此任务，批量执行时状态记为跳过"))
        if overridden:
            lines.append(tr("该用户使用独立参数"))
        return "\n".join(lines)

    def _reload_list(self, *, keep: bool = True) -> None:
        """重建左侧列表；``keep`` 时尽量停在原来那一行。

        视图或顶部下拉换了，原来的对象就不在同一批候选里，此时不能沿用
        旧选择，否则右侧会显示一个左列表里并不存在的对象。
        """
        previous = self._current if keep else None
        self._list.blockSignals(True)
        try:
            self._list.clear()
            if self._view == "task":
                task_id = str(self._task_combo.currentData() or "")
                global_item = QListWidgetItem(_GLOBAL_LABEL)
                global_item.setData(Qt.ItemDataRole.UserRole, "")
                self._style_item(global_item, bold=True, skipped=False)
                global_item.setToolTip(tr(
                    "任务的全局参数，作用于所有未使用独立参数的用户"))
                self._list.addItem(global_item)
                for username in self.users():
                    skipped, overridden = self._user_marks(username, task_id)
                    item = QListWidgetItem(
                        f"{_SKIP_MARK} {username}" if skipped else username)
                    item.setData(Qt.ItemDataRole.UserRole, username)
                    self._style_item(
                        item, bold=overridden, skipped=skipped)
                    item.setToolTip(self._user_tooltip(skipped, overridden))
                    self._list.addItem(item)
                wanted = "" if previous and previous[0] == "global" else (
                    previous[0] if previous else None)
            else:
                username = str(self._user_combo.currentData() or "")
                skipped_tasks, overridden_tasks = self._user_sets(username)
                for cfg in self.configs():
                    task_id = str(cfg.get("id") or "")
                    name = script_display_name(cfg)
                    skipped = task_id in skipped_tasks
                    overridden = task_id in overridden_tasks
                    label = f"{_SKIP_MARK} {name}" if skipped else name
                    item = QListWidgetItem(label)
                    item.setData(Qt.ItemDataRole.UserRole, task_id)
                    self._style_item(
                        item, bold=overridden, skipped=skipped)
                    item.setToolTip(self._user_tooltip(skipped, overridden))
                    self._list.addItem(item)
                wanted = previous[1] if previous else None
            target = 0
            if wanted is not None:
                for row in range(self._list.count()):
                    candidate = self._list.item(row)
                    if candidate is None:
                        continue
                    data = str(candidate.data(Qt.ItemDataRole.UserRole) or "")
                    if data == wanted:
                        target = row
                        break
            if self._list.count():
                self._list.setCurrentRow(target)
        finally:
            self._list.blockSignals(False)
        self._apply_list_width()
        self._on_row_changed(self._list.currentRow())

    def _apply_list_width(self) -> None:
        """左区默认宽度按 14 个汉字定：够放常见用户名与任务名，不多留空档。

        只算一次：用户手动拖过分隔条之后，重建列表不应把他的宽度改回去。
        """
        if getattr(self, "_list_width_applied", False):
            return
        if not self._list.count():
            return
        metrics = self._list.fontMetrics()
        # 「测」是全角汉字，宽度足以代表 CJK 用户名与任务名。
        text_width = metrics.horizontalAdvance("测" * _LIST_WIDTH_CHARS)
        scrollbar = self._list.verticalScrollBar()
        scrollbar_width = scrollbar.sizeHint().width() if scrollbar else 0
        # 左右内边距 + 边框 + 滚动条 + 一点点余量。
        padding = 2 * self._list.frameWidth() + 24 + scrollbar_width
        width = text_width + padding
        total = sum(self._splitter.sizes()) or 900
        self._splitter.setSizes([width, max(320, total - width)])
        self._list_width_applied = True

    def _on_row_changed(self, row: int) -> None:
        item = self._list.item(row) if row >= 0 else None
        if item is None:
            self._current = None
            self._render_detail()
            return
        self._save_timer.stop()
        self._save_current()
        data = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if self._view == "task":
            task_id = str(self._task_combo.currentData() or "")
            self._current = ("global", task_id) if not data else (data, task_id)
        else:
            self._current = (str(self._user_combo.currentData() or ""), data)
        self._render_detail()

    # ─── 右侧详情 ─────────────────────────────────────────

    def _render_detail(self) -> None:
        current = self._current
        enabled = current is not None
        self._detail_box.setEnabled(enabled)
        if current is None:
            self._current_task_id = ""
            self._form.set_parameters([], {})
            self._skip_check.setVisible(False)
            self._override_check.setVisible(False)
            self._source_label.setText(tr("没有可编辑的任务"))
            return
        who, task_id = current
        self._current_task_id = task_id
        cfg = self._config(task_id)
        if cfg is None:
            self._form.set_parameters([], {})
            self._source_label.setText(tr("任务已不可用"))
            self._skip_check.setVisible(False)
            self._override_check.setVisible(False)
            return
        definitions = parameters_for_env(
            list(cfg.get("parameters") or []), self._run_env())
        is_global = who == "global"
        # 全局伪条目没有"该用户"这层语义，两个开关都不适用。
        self._skip_check.setVisible(not is_global)
        self._override_check.setVisible(not is_global)
        if is_global:
            values, _source = merge_task_params(
                definitions, get_wf_config(task_id), None)
            self._source_label.setText(
                tr("全局参数：作用于所有未使用独立参数的用户"))
        else:
            values, source = resolve_task_params(
                task_id, who, definitions, self._users_dir())
            independent = source == "user"
            self._override_check.blockSignals(True)
            self._override_check.setChecked(independent)
            self._override_check.blockSignals(False)
            self._source_label.setText(
                tr("参数来源：独立参数") if independent
                else tr("参数来源：全局参数"))
            skipped = task_id in get_user_skipped_tasks(who, self._users_dir())
            self._skip_check.blockSignals(True)
            self._skip_check.setChecked(skipped)
            self._skip_check.blockSignals(False)
        self._form.set_parameters(definitions, values, editable=True)
        self._baseline = self._form.collect()
        self._apply_skip_state()

    def _apply_skip_state(self) -> None:
        """跳过时参数区置灰（保留值），并说明原因。

        用 ``isHidden`` 而不是 ``isVisible``：窗口未显示（例如非模态窗口刚被
        构造、或测试里没有映射到屏幕）时 ``isVisible`` 恒为假，会把跳过状态
        误判成"可编辑"。
        """
        skipped = (not self._skip_check.isHidden()
                   and self._skip_check.isChecked())
        self._form.set_editable(not skipped)
        self._detail_box.setToolTip(
            tr("该用户已声明跳过此任务；取消勾选后可继续编辑参数")
            if skipped else "")

    # ─── 写入 ─────────────────────────────────────────────

    def _on_skip_toggled(self, checked: bool) -> None:
        current = self._current
        if current is None or current[0] == "global":
            return
        username, task_id = current
        try:
            set_user_skipped_task(
                username, task_id, bool(checked), self._users_dir())
        except Exception as exc:  # noqa: BLE001 - 单条失败不关闭窗口
            logger.warning(f"保存跳过声明失败 {username}/{task_id}: {exc}")
            return
        self._apply_skip_state()
        self._refresh_row_marks()

    def _on_override_toggled(self, checked: bool) -> None:
        current = self._current
        if current is None or current[0] == "global":
            return
        username, task_id = current
        self._save_timer.stop()
        self._save_current()
        try:
            if checked:
                set_user_workflow_params(
                    username, task_id, self._form.collect(), self._users_dir())
            else:
                delete_user_workflow_params(
                    username, task_id, self._users_dir())
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"切换独立参数失败 {username}/{task_id}: {exc}")
            return
        self._render_detail()
        self._refresh_row_marks()

    def _on_form_changed(self, *_args) -> None:
        self._save_current()

    def _save_current(self) -> None:
        """把当前详情里的改动写回它自己的归属，不碰别的对象。"""
        current = self._current
        task_id = getattr(self, "_current_task_id", "")
        if current is None or not task_id:
            return
        cfg = self._config(task_id)
        if cfg is None or not self._form.has_fields():
            return
        if not self._form.is_editable():
            # 跳过的对象参数区被禁用：不把展示值当成编辑结果写回。
            self._baseline = self._form.collect()
            return
        values = self._form.collect()
        patch = {
            key: value for key, value in values.items()
            if key not in self._baseline or self._baseline[key] != value
        }
        if not patch:
            self._baseline = values
            return
        who, _task = current
        try:
            if who == "global":
                update_wf_config(task_id, patch)
            elif get_user_workflow_params(
                    who, task_id, self._users_dir()) is not None:
                existing = get_user_workflow_params(
                    who, task_id, self._users_dir()) or {}
                existing.update(patch)
                set_user_workflow_params(
                    who, task_id, existing, self._users_dir())
            else:
                # 跟随全局的用户在这里改参数，写的是全局值——与日常页共享模式一致。
                update_wf_config(task_id, patch)
        except Exception as exc:  # noqa: BLE001 - 单个对象失败不拖垮整窗
            logger.warning(f"任务参数保存失败 {who}/{task_id}: {exc}")
            return
        self._baseline = values

    # ─── 列表刷新 ─────────────────────────────────────────

    def _refresh_row_marks(self) -> None:
        """只刷新左侧列表的标记与字体，保留选择与滚动位置。"""
        self._reload_list(keep=True)


__all__ = ["TaskParamsDialog"]
