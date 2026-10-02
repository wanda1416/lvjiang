"""批量配置对话框：选择用户、排列顺序并配置生命周期工作流。"""
from __future__ import annotations

from typing import Any, cast

from loguru import logger
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.batch_config import (
    BatchConfigItem,
    BatchWorkflows,
    lifecycle_parameter_definitions,
    load_batch_config,
    save_batch_config,
)
from ...core.user_config import UserConfigManager
from ...i18n import tr
from ...workflows.builtins._coerce import to_bool
from ..button_styles import apply_button_style
from ..layout_helpers import fit_combo_popup_to_contents


class BatchConfigDialog(QDialog):
    saved = pyqtSignal()

    def __init__(self, user_manager: UserConfigManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("批量配置"))
        self.setMinimumSize(860, 560)
        self._cfg = load_batch_config()
        self._users = user_manager
        #: 当前编辑的配置组 ID。用 ID 而不是名称：重命名不该让关联断掉。
        self._current_id = ""
        self._updating_choices = False
        self._setup_ui()
        self._refresh_config_list()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        config_row = QHBoxLayout()
        config_row.addWidget(QLabel(tr("配置：")))
        self._config_combo = QComboBox()
        self._config_combo.currentIndexChanged.connect(self._on_config_selected)
        config_row.addWidget(self._config_combo, 1)
        self._btn_new = QPushButton(tr("新建"))
        self._btn_rename = QPushButton(tr("重命名"))
        self._btn_delete = QPushButton(tr("删除"))
        self._btn_new.clicked.connect(self._on_new_config)
        self._btn_rename.clicked.connect(self._on_rename_config)
        self._btn_delete.clicked.connect(self._on_delete_config)
        apply_button_style(self._btn_new)
        apply_button_style(self._btn_rename, variant="neutral")
        apply_button_style(self._btn_delete, variant="danger")
        config_row.addWidget(self._btn_new)
        config_row.addWidget(self._btn_rename)
        config_row.addWidget(self._btn_delete)
        layout.addLayout(config_row)

        choices = QSplitter(Qt.Orientation.Horizontal)
        task_box = QWidget()
        task_layout = QVBoxLayout(task_box)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_header = QHBoxLayout()
        task_header.addWidget(QLabel(tr("可见任务：")))
        task_header.addStretch()
        task_all = QPushButton(tr("全选"))
        task_none = QPushButton(tr("全不选"))
        task_all.clicked.connect(lambda: self._set_all_checked(self._task_list, True))
        task_none.clicked.connect(
            lambda: self._set_all_checked(self._task_list, False))
        apply_button_style(task_all, task_none, variant="neutral")
        task_header.addWidget(task_all)
        task_header.addWidget(task_none)
        task_layout.addLayout(task_header)
        self._task_list = self._create_choice_tree(tr("可见任务"))
        self._task_list.setToolTip(tr(
            "第一列决定是否出现在主页面，第二列决定主页面首次加载或点「恢复默认」"
            "时是否勾选；拖动可调整初始顺序"))
        task_layout.addWidget(self._task_list)
        choices.addWidget(task_box)

        user_box = QWidget()
        user_layout = QVBoxLayout(user_box)
        user_layout.setContentsMargins(0, 0, 0, 0)
        user_header = QHBoxLayout()
        user_header.addWidget(QLabel(tr("可见用户：")))
        user_header.addStretch()
        user_all = QPushButton(tr("全选"))
        user_none = QPushButton(tr("全不选"))
        user_all.clicked.connect(lambda: self._set_all_checked(self._user_list, True))
        user_none.clicked.connect(
            lambda: self._set_all_checked(self._user_list, False))
        apply_button_style(user_all, user_none, variant="neutral")
        user_header.addWidget(user_all)
        user_header.addWidget(user_none)
        user_layout.addLayout(user_header)
        self._user_list = self._create_choice_tree(tr("可见用户"))
        self._user_list.setToolTip(tr(
            "第一列决定是否出现在主页面，第二列决定主页面首次加载或点「恢复默认」"
            "时是否勾选；拖动可调整初始顺序"))
        user_layout.addWidget(self._user_list)
        choices.addWidget(user_box)
        choices.setSizes([430, 430])
        layout.addWidget(choices, 1)

        scope_form = QFormLayout()
        # 标签左对齐，并且所有行共用同一个表单：分成两个 QFormLayout 时各自
        # 按自己的最长标签算列宽，输入框的左边缘就对不齐了。
        scope_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._unit_combo = QComboBox()
        self._unit_combo.currentIndexChanged.connect(self._on_unit_changed)
        self._unit_combo.setToolTip(tr(
            "按用户名调度，还是按用户资料的某个属性调度。它决定主页面候选列表的"
            "内容、生命周期契约和条目准备工作流的合法性，所以属于配置组定义"))
        scope_form.addRow(tr("调度单元："), self._unit_combo)
        sort_row = QHBoxLayout()
        # 去掉内边距：外层包了一个 QWidget 才能放两个控件，默认边距会让这一行
        # 比上下行的输入框整体右缩一截，左边缘就对不齐了。
        sort_row.setContentsMargins(0, 0, 0, 0)
        # key 用和「用户总览 → 新增列」同一套三级选择（类型 → 分组 → 定义）：
        # Profile key 多起来之后，平铺的下拉框根本看不完也选不动。
        self._sort_key_slot = QHBoxLayout()
        self._sort_key_slot.setContentsMargins(0, 0, 0, 0)
        self._profile_sort_key_value = ""
        self._profile_sort_key: QPushButton | None = None
        self._profile_sort_direction = QComboBox()
        self._profile_sort_direction.addItem(tr("升序"), "asc")
        self._profile_sort_direction.addItem(tr("降序"), "desc")
        fit_combo_popup_to_contents(self._profile_sort_direction)
        sort_row.addLayout(self._sort_key_slot, 1)
        sort_row.addWidget(self._profile_sort_direction)
        self._profile_sort_label = QLabel(tr("指定排序："))
        self._profile_sort_widget = QWidget()
        self._profile_sort_widget.setLayout(sort_row)
        self._profile_sort_widget.setToolTip(tr(
            "定义主页面右键「按 Profile 排序」用哪个键、什么方向。排完的顺序只属于"
            "本次运行，不写回这里"))
        scope_form.addRow(self._profile_sort_label, self._profile_sort_widget)
        self._scope_form = scope_form

        wf_form = scope_form
        self._selectors = {}
        for key, label in (
            ("batch_setup", tr("批次准备工作流：")),
            ("prepare_item", tr("条目准备工作流：")),
            ("finish_item", tr("条目收尾工作流：")),
            ("batch_teardown", tr("批次收尾工作流：")),
        ):
            row, combo = self._create_wf_selector()
            self._selectors[key] = combo
            wf_form.addRow(label, row)
        self._skip_single_lifecycle = QCheckBox(
            tr("单个执行单元时跳过上述生命周期工作流")
        )
        self._skip_single_lifecycle.setToolTip(
            tr("实际只选择一个用户单元时，直接执行任务；属性单元始终运行准备工作流")
        )
        wf_form.addRow("", self._skip_single_lifecycle)
        recover_row, recover_combo = self._create_wf_selector()
        self._selectors["recover_unattended"] = recover_combo
        recover_combo.setToolTip(tr(
            "无人值守撞上暂停或确认框后，用它把游戏收回登录主页。"
            "主页面的「无人值守」要等这里配好才能勾选"))
        wf_form.addRow(tr("异常恢复工作流："), recover_row)
        layout.addLayout(wf_form)
        # 生命周期参数紧跟它所属的 wf：wf 在这里选，参数却要去另一个窗口填，
        # 是上一版把同一件事拆到两处的典型。
        self._workflow_params_panel = QWidget()
        self._workflow_params_layout = QVBoxLayout(self._workflow_params_panel)
        self._workflow_params_layout.setContentsMargins(0, 0, 0, 0)
        self._workflow_param_groups: list[QGroupBox] = []
        self._workflow_param_widgets: dict[tuple[str, str], QWidget] = {}
        self._workflow_param_types: dict[tuple[str, str], str] = {}
        layout.addWidget(self._workflow_params_panel)
        for combo in self._selectors.values():
            combo.currentTextChanged.connect(self._on_workflow_changed)

        buttons = QHBoxLayout()
        buttons.addStretch()
        save = QPushButton(tr("保存"))
        cancel = QPushButton(tr("取消"))
        save.setDefault(True)
        save.clicked.connect(self._on_save)
        cancel.clicked.connect(self.reject)
        apply_button_style(save)
        apply_button_style(cancel, variant="neutral")
        buttons.addWidget(save)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def _create_wf_selector(self):
        row = QHBoxLayout()
        combo = QComboBox()
        combo.setEditable(True)
        row.addWidget(combo, 1)
        browse = QPushButton(tr("浏览..."))
        browse.clicked.connect(lambda: self._browse_wf(combo))
        apply_button_style(browse, variant="neutral")
        row.addWidget(browse)
        return row, combo

    # ─── 两列选择树：可见 / 默认执行 ───────────────────────

    def _create_choice_tree(self, title: str) -> QTreeWidget:
        """可见性与默认勾选是两件事，所以一行两个勾选框。

        只有一个勾选框时，它得同时表达「出现在主页面」和「默认要跑」——上一版
        正是如此，于是主页面的一次临时取消勾选会写回配置组。
        """
        tree = QTreeWidget()
        tree.setColumnCount(2)
        tree.setHeaderLabels([title, tr("默认执行")])
        tree.setRootIsDecorated(False)
        tree.setUniformRowHeights(True)
        tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        header = tree.header()
        if header is not None:
            header.setStretchLastSection(False)
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(
                1, QHeaderView.ResizeMode.ResizeToContents)
        tree.itemChanged.connect(self._on_choice_item_changed)
        return tree

    def _add_choice_row(
        self, tree: QTreeWidget, key: str, label: str,
        *, visible: bool, default: bool,
    ) -> None:
        row = QTreeWidgetItem([label, ""])
        row.setData(0, Qt.ItemDataRole.UserRole, key)
        row.setFlags(
            (row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            & ~Qt.ItemFlag.ItemIsDropEnabled)
        row.setCheckState(
            0, Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)
        row.setCheckState(
            1, Qt.CheckState.Checked
            if (visible and default) else Qt.CheckState.Unchecked)
        row.setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
        tree.addTopLevelItem(row)

    def _on_choice_item_changed(
        self, row: QTreeWidgetItem, column: int,
    ) -> None:
        """取消可见后，"默认执行"必须跟着清掉——不可见的条目谈不上默认跑。"""
        if self._updating_choices or column != 0:
            return
        if (row.checkState(0) != Qt.CheckState.Checked
                and row.checkState(1) == Qt.CheckState.Checked):
            self._updating_choices = True
            try:
                row.setCheckState(1, Qt.CheckState.Unchecked)
            finally:
                self._updating_choices = False

    @staticmethod
    def _choice_key(row: QTreeWidgetItem | None) -> str:
        if row is None:
            return ""
        value = row.data(0, Qt.ItemDataRole.UserRole)
        return value if isinstance(value, str) else ""

    def _choice_rows(self, tree: QTreeWidget) -> list[QTreeWidgetItem]:
        rows = []
        for index in range(tree.topLevelItemCount()):
            row = tree.topLevelItem(index)
            if row is not None:
                rows.append(row)
        return rows

    def _collect_choices(
        self, tree: QTreeWidget,
    ) -> tuple[list[str], list[str]]:
        """返回（可见项按当前顺序，其中的默认勾选项）。"""
        visible: list[str] = []
        defaults: list[str] = []
        for row in self._choice_rows(tree):
            key = self._choice_key(row)
            if not key or row.checkState(0) != Qt.CheckState.Checked:
                continue
            visible.append(key)
            if row.checkState(1) == Qt.CheckState.Checked:
                defaults.append(key)
        return visible, defaults

    def _set_all_checked(self, tree: QTreeWidget, checked: bool) -> None:
        """全选 / 全不选作用于「可见」列，默认执行列随之同步。"""
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._updating_choices = True
        try:
            for row in self._choice_rows(tree):
                row.setCheckState(0, state)
                row.setCheckState(1, state)
        finally:
            self._updating_choices = False

    def _browse_wf(self, combo: QComboBox) -> None:
        from pathlib import Path

        from ...core.config import get_resolver
        resolver = get_resolver()
        root = resolver.system_dir / "workflows"
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择工作流文件"), str(root), tr("工作流文件 (*.wf)"))
        if not path:
            return
        # 槽位存的是「相对 workflows 根」的路径，执行侧拿它拼
        # workflows/{value} 再 resolve_read。三层根目录都可能被选中，逐个
        # 试；都不匹配就拒绝——存下绝对路径的话拼进去必然解析失败。
        chosen = Path(path).resolve()
        for layer_root in (resolver.local_dir, resolver.remote_dir,
                           resolver.system_dir):
            base = (layer_root / "workflows").resolve()
            try:
                value = chosen.relative_to(base).as_posix()
            except ValueError:
                continue
            combo.setCurrentText(value)
            return
        QMessageBox.warning(
            self, tr("选择工作流文件"),
            tr("请选择 workflows 目录下的 .wf 文件"))

    def _refresh_config_list(self) -> None:
        self._config_combo.blockSignals(True)
        self._config_combo.clear()
        for group_id, item in self._cfg.configs.items():
            self._config_combo.addItem(item.name, group_id)
        index = self._config_combo.findData(self._current_id)
        if index < 0:
            index = 0 if self._config_combo.count() else -1
        if index >= 0:
            self._config_combo.setCurrentIndex(index)
        self._config_combo.blockSignals(False)
        if index >= 0:
            self._load_config(str(self._config_combo.itemData(index) or ""))
        else:
            self._clear_editor()

    def _load_config(self, group_id: str) -> None:
        self._current_id = group_id
        item = self._cfg.configs.get(group_id)
        if item is None:
            self._clear_editor()
            return
        from ...workflows.discovery import list_exposed_scripts, script_display_name

        try:
            scripts = [
                cfg for cfg in list_exposed_scripts()
                if cfg.get("batchable", False)
            ]
        except Exception:
            scripts = []
        scripts_by_id = {str(cfg["id"]): cfg for cfg in scripts}
        visible_tasks = set(item.task_ids)
        default_tasks = set(item.default_task_ids)
        ordered_task_ids = list(item.task_ids)
        ordered_task_ids += [
            script_id for script_id in scripts_by_id
            if script_id not in visible_tasks
        ]
        self._updating_choices = True
        try:
            self._task_list.clear()
            for task_id in ordered_task_ids:
                cfg = scripts_by_id.get(task_id)
                label = (script_display_name(cfg)
                         if cfg is not None else task_id)
                self._add_choice_row(
                    self._task_list, task_id, label,
                    visible=task_id in visible_tasks,
                    default=task_id in default_tasks)
            self._populate_user_list(item)
        finally:
            self._updating_choices = False
        for key, combo in self._selectors.items():
            combo.blockSignals(True)
            combo.setCurrentText(getattr(item.workflows, key))
            combo.blockSignals(False)
        # 勾选状态统一由 _apply_unit_dependent_state 决定（它要按调度单元
        # 判断是否适用），这里不再单独设一次，免得两处争同一个控件。
        self._refresh_scope_controls(item)
        self._rebuild_workflow_params(item)

    def _rebuild_workflow_params(self, item: BatchConfigItem | None) -> None:
        while self._workflow_params_layout.count():
            layout_item = self._workflow_params_layout.takeAt(0)
            old_widget = layout_item.widget() if layout_item is not None else None
            if old_widget is not None:
                old_widget.deleteLater()
        self._workflow_param_groups.clear()
        self._workflow_param_widgets.clear()
        self._workflow_param_types.clear()
        # require 依赖按行控显隐：记下每个参数占用的行号与本阶段的参数定义
        self._workflow_param_forms: dict[str, QFormLayout] = {}
        self._workflow_param_rows: dict[tuple[str, str], list[int]] = {}
        self._workflow_param_defs: dict[str, list[dict]] = {}
        if item is None:
            self._workflow_params_panel.setVisible(False)
            return

        phase_labels = {
            "batch_setup": tr("批次准备"),
            "prepare_item": tr("条目准备"),
            "finish_item": tr("条目收尾"),
            "batch_teardown": tr("批次收尾"),
            # 异常恢复不在上面那四行只读摘要里，但它同样是生命周期 wf，
            # 声明了参数就要能在这里编辑——少一个 key 就是 KeyError。
            "recover_unattended": tr("异常恢复"),
        }
        definitions = lifecycle_parameter_definitions(item.workflows)
        for phase, params in definitions.items():
            if not params:
                continue
            group = QGroupBox(phase_labels[phase])
            form = QFormLayout(group)
            self._workflow_param_groups.append(group)
            self._workflow_param_forms[phase] = form
            self._workflow_param_defs[phase] = list(params)
            saved = item.workflow_params.get(phase, {})
            for definition in params:
                name = str(definition["name"])
                rows_before = form.rowCount()
                label = str(definition.get("label") or name)
                value = saved.get(name, definition.get("default"))
                param_type = definition.get("type", "select")
                widget: QWidget
                if param_type == "bool":
                    checkbox = QCheckBox()
                    checkbox.setChecked(to_bool(value))
                    checkbox.toggled.connect(self._on_workflow_param_changed)
                    widget = checkbox
                elif param_type == "number":
                    spin = QSpinBox()
                    spin.setRange(
                        int(definition.get("min", 0)),
                        int(definition.get("max", 999999)),
                    )
                    spin.setValue(int(value) if value is not None else 0)
                    spin.valueChanged.connect(self._on_workflow_param_changed)
                    widget = spin
                elif param_type == "select":
                    combo = QComboBox()
                    for option in definition.get("options", []):
                        if isinstance(option, dict):
                            combo.addItem(str(option.get("label", option["value"])),
                                          option["value"])
                        else:
                            combo.addItem(str(option), str(option))
                    selected = combo.findData(value)
                    if selected >= 0:
                        combo.setCurrentIndex(selected)
                    combo.currentIndexChanged.connect(self._on_workflow_param_changed)
                    widget = combo
                elif param_type == "checkgroup":
                    container = QWidget()
                    options_layout = QHBoxLayout(container)
                    options_layout.setContentsMargins(0, 0, 0, 0)
                    selected_values = value if isinstance(value, dict) else {}
                    for option in definition.get("options", []):
                        if isinstance(option, dict):
                            option_name = str(option["value"])
                            option_label = str(option.get("label", option_name))
                        else:
                            option_name = option_label = str(option)
                        checkbox = QCheckBox(option_label)
                        checkbox.setObjectName(option_name)
                        checkbox.setChecked(bool(selected_values.get(option_name, True)))
                        checkbox.toggled.connect(self._on_workflow_param_changed)
                        options_layout.addWidget(checkbox)
                    options_layout.addStretch()
                    widget = container
                else:
                    edit = QPlainTextEdit() if definition.get("multiline") else QLineEdit()
                    if isinstance(edit, QPlainTextEdit):
                        edit.setMaximumHeight(100)
                        edit.setPlainText(str(value or ""))
                        edit.textChanged.connect(self._on_workflow_param_changed)
                    else:
                        edit.setText(str(value or ""))
                        edit.textChanged.connect(self._on_workflow_param_changed)
                    widget = edit
                widget.setObjectName(name)
                self._workflow_param_widgets[(phase, name)] = widget
                self._workflow_param_types[(phase, name)] = str(param_type)
                if isinstance(widget, QPlainTextEdit):
                    form.addRow(QLabel(f"{label}："))
                    form.addRow(widget)
                else:
                    form.addRow(f"{label}：", widget)
                self._workflow_param_rows[(phase, name)] = list(
                    range(rows_before, form.rowCount()))
            self._workflow_params_layout.addWidget(group)
        self._refresh_workflow_param_visibility()
        self._workflow_params_panel.setVisible(bool(self._workflow_param_groups))

    def _refresh_workflow_param_visibility(self) -> None:
        """按 require 依赖隐藏当前取值下不适用的生命周期参数行。

        只改显隐：控件和值都留着，运行时参数快照照旧包含它们。
        """
        defs_by_phase = getattr(self, "_workflow_param_defs", {})
        if not any(item.get("require")
                   for params in defs_by_phase.values() for item in params):
            return
        from ...core.param_require import RequireError, visible_parameter_names
        collected = self._collect_workflow_params()
        for phase, params in defs_by_phase.items():
            form = self._workflow_param_forms.get(phase)
            if form is None:
                continue
            try:
                visible = visible_parameter_names(params, collected.get(phase, {}))
            except RequireError as exc:
                logger.warning(f"生命周期参数依赖求值失败，本次全部展示: {exc}")
                continue
            for (row_phase, name), indices in self._workflow_param_rows.items():
                if row_phase != phase:
                    continue
                for index in indices:
                    form.setRowVisible(index, name in visible)

    def _collect_workflow_params(self) -> dict[str, dict]:
        """从控件读出各阶段参数值；写回配置与算 require 可见性共用这一份。"""
        values: dict[str, dict] = {}
        for key, widget in self._workflow_param_widgets.items():
            phase, name = key
            param_type = self._workflow_param_types[key]
            value: Any
            if isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                value = widget.value()
            elif isinstance(widget, QComboBox):
                value = widget.currentData()
            elif isinstance(widget, QPlainTextEdit):
                value = widget.toPlainText()
            elif param_type == "checkgroup":
                value = {
                    checkbox.objectName(): checkbox.isChecked()
                    for checkbox in widget.findChildren(QCheckBox)
                }
            else:
                value = cast(QLineEdit, widget).text()
            values.setdefault(phase, {})[name] = value
        return values

    def _on_workflow_param_changed(self, *_args) -> None:
        """参数值改动只刷新 require 显隐；落盘统一在「保存」时进行。

        这与对话框里其他控件一致：编辑期只动草稿，点保存才写配置组。
        """
        self._refresh_workflow_param_visibility()


    def _refresh_scope_controls(self, item: BatchConfigItem | None) -> None:
        """填充调度单元与 Profile 排序：两者都是配置组定义。"""

        self._unit_combo.blockSignals(True)
        self._unit_combo.clear()
        self._unit_combo.addItem(tr("用户名"), "user")
        keys: dict[str, None] = {}
        if item is not None:
            for username in item.usernames:
                user = self._users.get_user(username)
                if user is None:
                    continue
                keys.update({
                    key: None for key, value in user.attributes.items()
                    if key and str(value).strip()
                })
            keys.setdefault(item.execution_unit_key, None)
        for key in keys:
            if key != "user":
                self._unit_combo.addItem(key, key)
        current_key = item.execution_unit_key if item is not None else "user"
        self._unit_combo.setCurrentIndex(
            max(0, self._unit_combo.findData(current_key)))
        self._unit_combo.blockSignals(False)

        self._profile_sort_key_value = (
            item.profile_sort_key if item is not None else "")
        self._rebuild_sort_key_picker()
        self._profile_sort_direction.blockSignals(True)
        direction = item.profile_sort_direction if item is not None else "asc"
        self._profile_sort_direction.setCurrentIndex(
            max(0, self._profile_sort_direction.findData(direction)))
        self._profile_sort_direction.blockSignals(False)
        self._apply_unit_dependent_state()

    def _rebuild_sort_key_picker(self) -> None:
        """重建 key 选择按钮。

        定义可能在 Profile 编辑器里被增删，而菜单结构是建按钮时一次性生成的，
        所以每次载入配置组都重建一次，而不是缓存一个过期的菜单。
        """
        from ...core.profile.schema import get_profile_config
        from ..profile.key_picker import create_profile_key_picker

        while self._sort_key_slot.count():
            taken = self._sort_key_slot.takeAt(0)
            widget = taken.widget() if taken is not None else None
            if widget is not None:
                widget.deleteLater()
        try:
            config = get_profile_config()
            all_keys = list(config.get_all_keys())
        except (OSError, ValueError) as exc:
            logger.warning(f"读取 Profile 定义失败，指定排序不可用: {exc}")
            button = QPushButton(tr("Profile 定义不可用"))
            button.setEnabled(False)
            self._profile_sort_key = button
            self._sort_key_slot.addWidget(button, 1)
            return

        def _remember(key: str) -> None:
            self._profile_sort_key_value = key

        button = create_profile_key_picker(
            config, all_keys, self._profile_sort_key_value, _remember,
            empty_label=tr("不指定"))
        self._profile_sort_key = button
        self._sort_key_slot.addWidget(button, 1)

    def _on_unit_changed(self, _index: int) -> None:
        self._apply_unit_dependent_state()

    def _apply_unit_dependent_state(self) -> None:
        """调度单元决定另外两项是否适用，就地禁用并说明原因。

        禁用的同时**取消勾选**：属性单元下生命周期始终执行，一个灰着却勾着的
        框字面意思正好相反。但这只是展示——存储值不能被它带走，否则切回用户名
        时用户原来存的选择就被悄悄抹掉了（写回由 _save_current_config 把门）。
        """
        user_unit = str(self._unit_combo.currentData() or "user") == "user"
        item = self._cfg.configs.get(self._current_id)
        stored = (item.skip_lifecycle_for_single_item
                  if item is not None else True)
        self._skip_single_lifecycle.blockSignals(True)
        self._skip_single_lifecycle.setChecked(stored if user_unit else False)
        self._skip_single_lifecycle.blockSignals(False)
        self._skip_single_lifecycle.setEnabled(user_unit)
        self._skip_single_lifecycle.setToolTip(tr(
            "实际只选择一个用户单元时，直接执行任务，不调用生命周期工作流"
        ) if user_unit else tr(
            "按属性调度时不适用：一个属性值可能对应多名用户，必须由条目准备"
            "工作流选定并回传用户名，所以生命周期始终执行。切回「用户名」会恢复"
            "你原来的选择"))
        self._scope_form.setRowVisible(self._profile_sort_label, user_unit)

    def _on_workflow_changed(self, *_args) -> None:
        """wf 换了就重读它声明的参数，参数面板跟着变。"""
        item = self._cfg.configs.get(self._current_id)
        if item is None:
            return
        item.workflows = BatchWorkflows(**{
            key: combo.currentText().strip()
            for key, combo in self._selectors.items()
        })
        self._rebuild_workflow_params(item)

    def _clear_editor(self) -> None:
        self._current_id = ""
        self._task_list.clear()
        self._user_list.clear()
        for combo in self._selectors.values():
            combo.setCurrentText("")
        self._refresh_scope_controls(None)
        self._rebuild_workflow_params(None)
        # 没有配置组时没什么可编辑的；要放在 _refresh_scope_controls 之后，
        # 否则会被它按"用户名单元"重新启用。
        self._skip_single_lifecycle.setEnabled(False)

    def _save_current_config(self) -> None:
        item = self._cfg.configs.get(self._current_id)
        if item is None:
            return
        item.task_ids, item.default_task_ids = self._collect_choices(
            self._task_list)
        self._save_user_list(item)
        item.workflows = BatchWorkflows(**{
            key: combo.currentText().strip()
            for key, combo in self._selectors.items()
        })
        item.execution_unit_key = str(
            self._unit_combo.currentData() or "user")
        # 非用户名单元时这个框被强制显示为未勾选，那是展示而不是用户意图——
        # 原值留着，切回用户名还要用。
        if item.execution_unit_key == "user":
            item.skip_lifecycle_for_single_item = (
                self._skip_single_lifecycle.isChecked())
        item.profile_sort_key = self._profile_sort_key_value
        item.profile_sort_direction = str(
            self._profile_sort_direction.currentData() or "asc")
        item.workflow_params = self._collect_workflow_params()

    def _populate_user_list(self, item: BatchConfigItem) -> None:
        candidates = self._users.list_users()
        visible = set(item.usernames)
        defaults = set(item.default_usernames)
        ordered = [value for value in item.usernames if value in candidates]
        ordered.extend(value for value in candidates if value not in visible)
        self._user_list.clear()
        for value in ordered:
            self._add_choice_row(
                self._user_list, value, value,
                visible=value in visible, default=value in defaults)

    def _save_user_list(self, item: BatchConfigItem) -> None:
        item.usernames, item.default_usernames = self._collect_choices(
            self._user_list)

    def _on_config_selected(self, index: int) -> None:
        if index < 0:
            return
        group_id = str(self._config_combo.itemData(index) or "")
        if self._current_id and self._current_id != group_id:
            self._save_current_config()
        self._load_config(group_id)

    def _on_new_config(self) -> None:
        name, ok = QInputDialog.getText(self, tr("新建配置"), tr("配置名称："))
        name = name.strip()
        if not ok or not name:
            return
        if self._cfg.by_name(name) is not None:
            QMessageBox.warning(self, tr("重复"), tr("配置名称已存在"))
            return
        self._save_current_config()
        item = self._cfg.add(BatchConfigItem(name=name))
        self._current_id = item.id
        self._refresh_config_list()

    def _on_rename_config(self) -> None:
        """重命名只改名字。配置组按 ID 索引，所以位置、草稿、历史全都不动。"""
        item = self._cfg.configs.get(self._current_id)
        if item is None:
            return
        new_name, ok = QInputDialog.getText(
            self, tr("重命名配置"), tr("配置名称："), text=item.name)
        new_name = new_name.strip()
        if not ok or not new_name or new_name == item.name:
            return
        existing = self._cfg.by_name(new_name)
        if existing is not None and existing.id != item.id:
            QMessageBox.warning(self, tr("重复"), tr("配置名称已存在"))
            return
        self._save_current_config()
        item.name = new_name
        self._refresh_config_list()

    def _on_delete_config(self) -> None:
        if self._current_id not in self._cfg.configs:
            return
        if QMessageBox.question(
            self, tr("确认删除"), tr("确定删除当前批量配置吗？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        del self._cfg.configs[self._current_id]
        self._current_id = self._cfg.first_id()
        self._refresh_config_list()

    def _on_save(self) -> None:
        """把全部配置组写回 batch.json。

        不再做"读盘再把主页面拥有的字段搬回来"那套反向合并：运行态已经全部
        搬去 session 的运行草稿，`batch.json` 里再没有主页面会写的字段，
        这个窗口就是定义层的唯一入口。
        """
        self._save_current_config()
        save_batch_config(self._cfg)
        # 保存是应用当前全部配置，不关闭窗口，便于继续修改其他配置组。
        self.saved.emit()
