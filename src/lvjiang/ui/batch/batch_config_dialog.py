"""批量配置对话框：选择用户、排列顺序并配置生命周期工作流。"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...core.batch_config import (
    BatchConfigItem,
    BatchWorkflows,
    load_batch_config,
    save_batch_config,
)
from ...core.user_config import UserConfigManager
from ...i18n import tr
from ..button_styles import apply_button_style


class BatchConfigDialog(QDialog):
    saved = pyqtSignal()

    def __init__(self, user_manager: UserConfigManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("批量配置"))
        self.setMinimumSize(860, 560)
        self._cfg = load_batch_config()
        self._users = user_manager
        self._current_name = ""
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
        task_none.clicked.connect(lambda: self._set_all_checked(self._task_list, False))
        apply_button_style(task_all, task_none, variant="neutral")
        task_header.addWidget(task_all)
        task_header.addWidget(task_none)
        task_layout.addLayout(task_header)
        self._task_list = QListWidget()
        self._task_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._task_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._task_list.setToolTip(tr("拖动任务可调整批量执行顺序"))
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
        user_none.clicked.connect(lambda: self._set_all_checked(self._user_list, False))
        apply_button_style(user_all, user_none, variant="neutral")
        user_header.addWidget(user_all)
        user_header.addWidget(user_none)
        user_layout.addLayout(user_header)
        self._user_list = QListWidget()
        self._user_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._user_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._user_list.setToolTip(tr("拖动用户可调整批量执行顺序"))
        user_layout.addWidget(self._user_list)
        choices.addWidget(user_box)
        choices.setSizes([430, 430])
        layout.addWidget(choices, 1)

        wf_form = QFormLayout()
        self._selectors = {}
        for key, label in (
            ("batch_setup", tr("批次准备 wf：")),
            ("prepare_item", tr("条目准备 wf：")),
            ("finish_item", tr("条目收尾 wf：")),
            ("batch_teardown", tr("批次收尾 wf：")),
        ):
            row, combo = self._create_wf_selector()
            self._selectors[key] = combo
            wf_form.addRow(label, row)
        layout.addLayout(wf_form)

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

    @staticmethod
    def _set_all_checked(widget: QListWidget, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(widget.count()):
            item = widget.item(index)
            if item is not None:
                item.setCheckState(state)

    def _browse_wf(self, combo: QComboBox) -> None:
        from ...core.config import get_resolver
        root = get_resolver().system_dir / "workflows"
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择工作流文件"), str(root), tr("工作流文件 (*.wf)"))
        if path:
            from pathlib import Path
            try:
                value = str(Path(path).relative_to(root)).replace("\\", "/")
            except ValueError:
                value = path
            combo.setCurrentText(value)

    def _refresh_config_list(self) -> None:
        self._config_combo.blockSignals(True)
        self._config_combo.clear()
        self._config_combo.addItems(self._cfg.configs)
        if self._cfg.active_config in self._cfg.configs:
            self._config_combo.setCurrentText(self._cfg.active_config)
        self._config_combo.blockSignals(False)
        if self._config_combo.count():
            self._load_config(self._config_combo.currentText())
        else:
            self._clear_editor()

    def _load_config(self, name: str) -> None:
        self._current_name = name
        item = self._cfg.configs.get(name)
        if item is None:
            self._clear_editor()
            return
        from ...workflows.discovery import list_exposed_scripts

        try:
            scripts = [cfg for cfg in list_exposed_scripts() if cfg.get("batchable", True)]
        except Exception:
            scripts = []
        scripts_by_id = {str(cfg["id"]): cfg for cfg in scripts}
        selected_tasks = set(item.task_ids)
        ordered_task_ids = list(item.task_ids)
        ordered_task_ids += [
            script_id for script_id in scripts_by_id if script_id not in selected_tasks
        ]
        self._task_list.clear()
        for task_id in ordered_task_ids:
            cfg = scripts_by_id.get(task_id)
            label = str(cfg.get("name", task_id)) if cfg is not None else task_id
            row = QListWidgetItem(label)
            row.setData(Qt.ItemDataRole.UserRole, task_id)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(
                Qt.CheckState.Checked if task_id in selected_tasks
                else Qt.CheckState.Unchecked)
            self._task_list.addItem(row)

        selected_users = set(item.usernames)
        ordered = [name for name in item.usernames if name in self._users.list_users()]
        ordered += [name for name in self._users.list_users() if name not in selected_users]
        self._user_list.clear()
        for username in ordered:
            row = QListWidgetItem(username)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(
                Qt.CheckState.Checked if username in selected_users else Qt.CheckState.Unchecked)
            self._user_list.addItem(row)
        for key, combo in self._selectors.items():
            combo.setCurrentText(getattr(item.workflows, key))

    def _clear_editor(self) -> None:
        self._current_name = ""
        self._task_list.clear()
        self._user_list.clear()
        for combo in self._selectors.values():
            combo.setCurrentText("")

    def _save_current_config(self) -> None:
        item = self._cfg.configs.get(self._current_name)
        if item is None:
            return
        old_task_ids = set(item.task_ids)
        task_ids = []
        for index in range(self._task_list.count()):
            row = self._task_list.item(index)
            if row is not None and row.checkState() == Qt.CheckState.Checked:
                task_ids.append(str(row.data(Qt.ItemDataRole.UserRole)))
        item.task_ids = task_ids
        item.selected_task_ids = [
            task_id for task_id in item.selected_task_ids if task_id in task_ids
        ] + [
            task_id for task_id in task_ids if task_id not in old_task_ids
        ]

        old_usernames = set(item.usernames)
        usernames = []
        for index in range(self._user_list.count()):
            row = self._user_list.item(index)
            if row is not None and row.checkState() == Qt.CheckState.Checked:
                usernames.append(row.text())
        item.usernames = usernames
        item.selected_usernames = [
            username for username in item.selected_usernames if username in usernames
        ] + [
            username for username in usernames if username not in old_usernames
        ]
        item.workflows = BatchWorkflows(**{
            key: combo.currentText().strip() for key, combo in self._selectors.items()
        })

    def _on_config_selected(self, index: int) -> None:
        if index < 0:
            return
        name = self._config_combo.itemText(index)
        if self._current_name and self._current_name != name:
            self._save_current_config()
        self._cfg.active_config = name
        self._load_config(name)

    def _on_new_config(self) -> None:
        name, ok = QInputDialog.getText(self, tr("新建配置"), tr("配置名称："))
        name = name.strip()
        if not ok or not name:
            return
        if name in self._cfg.configs:
            QMessageBox.warning(self, tr("重复"), tr("配置名称已存在"))
            return
        self._save_current_config()
        self._cfg.configs[name] = BatchConfigItem(name=name)
        self._cfg.active_config = name
        self._refresh_config_list()

    def _on_rename_config(self) -> None:
        old_name = self._current_name
        if not old_name:
            return
        new_name, ok = QInputDialog.getText(
            self, tr("重命名配置"), tr("配置名称："), text=old_name)
        new_name = new_name.strip()
        if not ok or not new_name or new_name == old_name:
            return
        if new_name in self._cfg.configs:
            QMessageBox.warning(self, tr("重复"), tr("配置名称已存在"))
            return
        self._save_current_config()
        renamed = {}
        for name, item in self._cfg.configs.items():
            if name == old_name:
                item.name = new_name
                renamed[new_name] = item
            else:
                renamed[name] = item
        self._cfg.configs = renamed
        self._cfg.active_config = new_name
        self._current_name = new_name
        self._refresh_config_list()

    def _on_delete_config(self) -> None:
        if not self._current_name:
            return
        if QMessageBox.question(
            self, tr("确认删除"), tr("确定删除当前批量配置吗？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        del self._cfg.configs[self._current_name]
        self._cfg.active_config = next(iter(self._cfg.configs), "")
        self._refresh_config_list()

    def _on_save(self) -> None:
        self._save_current_config()
        # 对话框只负责可见范围和生命周期脚本；主页面可能同时更新实际勾选
        # 与执行顺序，因此保存前以磁盘最新值重新合并这部分状态。
        latest = load_batch_config()
        merged = {}
        for name, draft in self._cfg.configs.items():
            current = latest.configs.get(name)
            if current is None:
                merged[name] = draft
                continue
            old_tasks = set(current.task_ids)
            old_users = set(current.usernames)
            draft.selected_task_ids = [
                task_id for task_id in current.selected_task_ids
                if task_id in draft.task_ids
            ] + [
                task_id for task_id in draft.task_ids if task_id not in old_tasks
            ]
            draft.selected_usernames = [
                username for username in current.selected_usernames
                if username in draft.usernames
            ] + [
                username for username in draft.usernames if username not in old_users
            ]
            # 轮数和生命周期参数由批量主页面维护，本对话框不回写打开时快照。
            draft.rounds = current.rounds
            draft.workflow_params = current.workflow_params
            merged[name] = draft
        latest.configs = merged
        latest.active_config = self._cfg.active_config
        save_batch_config(latest)
        # 保存是应用当前全部配置，不关闭窗口，便于继续修改其他配置组。
        # 同步磁盘合并结果，避免下一次保存仍从打开窗口时的旧快照出发。
        self._cfg = latest
        self.saved.emit()
