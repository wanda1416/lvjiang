"""批量配置对话框：选择用户、排列顺序并配置生命周期工作流。"""
from __future__ import annotations

from PyQt6.QtCore import Qt
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
    QVBoxLayout,
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
    def __init__(self, user_manager: UserConfigManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("批量配置"))
        self.setMinimumSize(620, 520)
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
        self._btn_delete = QPushButton(tr("删除"))
        self._btn_new.clicked.connect(self._on_new_config)
        self._btn_delete.clicked.connect(self._on_delete_config)
        apply_button_style(self._btn_new)
        apply_button_style(self._btn_delete, variant="danger")
        config_row.addWidget(self._btn_new)
        config_row.addWidget(self._btn_delete)
        layout.addLayout(config_row)

        layout.addWidget(QLabel(tr("执行用户（勾选后可拖拽调整顺序）：")))
        self._user_list = QListWidget()
        self._user_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._user_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        layout.addWidget(self._user_list, 1)

        wf_form = QFormLayout()
        self._selectors = {}
        for key, label in (
            ("batch_setup", tr("批次初始化 wf：")),
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
        selected = set(item.usernames)
        ordered = [name for name in item.usernames if name in self._users.list_users()]
        ordered += [name for name in self._users.list_users() if name not in selected]
        self._user_list.clear()
        for username in ordered:
            row = QListWidgetItem(username)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(
                Qt.CheckState.Checked if username in selected else Qt.CheckState.Unchecked)
            self._user_list.addItem(row)
        for key, combo in self._selectors.items():
            combo.setCurrentText(getattr(item.workflows, key))

    def _clear_editor(self) -> None:
        self._current_name = ""
        self._user_list.clear()
        for combo in self._selectors.values():
            combo.setCurrentText("")

    def _save_current_config(self) -> None:
        item = self._cfg.configs.get(self._current_name)
        if item is None:
            return
        usernames = []
        for index in range(self._user_list.count()):
            row = self._user_list.item(index)
            if row is not None and row.checkState() == Qt.CheckState.Checked:
                usernames.append(row.text())
        item.usernames = usernames
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
        # 对话框打开期间，批量页仍可能通过 F9 更新脚本选择和顺序。
        # 这里只保存本对话框编辑的配置，保留最新的 script_ids。
        latest = load_batch_config()
        latest.configs = self._cfg.configs
        latest.active_config = self._cfg.active_config
        save_batch_config(latest)
        self.accept()
