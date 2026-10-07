"""数据总览新增列的字段多选窗口；选择草稿只存在于控件中。"""
from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ...core.profile.models import (
    ALL_MODELS,
    DEFAULT_KEY_GROUP,
    MODEL_LABELS,
    group_key_definitions,
)
from ...i18n import tr
from ..button_styles import apply_button_style


class ProfileKeyMultiSelectDialog(QDialog):
    """按类型/分组勾选，结果始终按树中字段顺序返回。"""

    def __init__(self, config, definitions: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("新增列"))
        self.resize(620, 520)
        layout = QVBoxLayout(self)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText(tr("搜索字段名称或 key"))
        self._search.setClearButtonEnabled(True)
        layout.addWidget(self._search)
        actions = QHBoxLayout()
        self._select_visible = QPushButton(tr("全选当前结果"))
        self._clear = QPushButton(tr("清空选择"))
        for button in (self._select_visible, self._clear):
            apply_button_style(button, variant="neutral")
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self._tree = QTreeWidget(self)
        self._tree.setHeaderHidden(True)
        self._tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        header = self._tree.header()
        assert header is not None
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._tree)
        self._leaves: list[QTreeWidgetItem] = []
        self._branches: list[QTreeWidgetItem] = []
        for model in ALL_MODELS:
            keys = [kd for kd in definitions if config.get_model_type(kd.key) == model]
            if not keys:
                continue
            root = self._branch(self._tree, MODEL_LABELS[model])
            grouped = group_key_definitions(keys)
            for group, items in grouped.items():
                parent_item = root
                if len(grouped) != 1 or group != DEFAULT_KEY_GROUP:
                    parent_item = self._branch(root, tr("默认") if group == DEFAULT_KEY_GROUP else group)
                for definition in items:
                    item = QTreeWidgetItem(parent_item, [f"{definition.label} ({definition.key})"])
                    item.setData(0, Qt.ItemDataRole.UserRole, definition.key)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.CheckState.Unchecked)
                    self._leaves.append(item)
        self._tree.expandAll()
        footer = QHBoxLayout()
        footer.addStretch()
        self._add = QPushButton()
        cancel = QPushButton(tr("取消"))
        apply_button_style(self._add)
        apply_button_style(cancel, variant="neutral")
        footer.addWidget(self._add)
        footer.addWidget(cancel)
        layout.addLayout(footer)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._search.textChanged.connect(self._filter)
        self._select_visible.clicked.connect(self._check_visible)
        self._clear.clicked.connect(self._clear_selection)
        self._add.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        self._update_selection()

    def _branch(self, parent, label: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [label])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        self._branches.append(item)
        return item

    @staticmethod
    def _descendants(item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        if item.childCount() == 0:
            return [item]
        leaves = []
        for index in range(item.childCount()):
            child = item.child(index)
            assert child is not None
            leaves.extend(ProfileKeyMultiSelectDialog._descendants(child))
        return leaves

    def selected_keys(self) -> list[str]:
        return [item.data(0, Qt.ItemDataRole.UserRole) for item in self._leaves
                if item.checkState(0) == Qt.CheckState.Checked]

    def _set_checks(self, items: list[QTreeWidgetItem], checked: bool) -> None:
        with QSignalBlocker(self._tree):
            for item in items:
                item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._update_selection()

    def _on_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.data(0, Qt.ItemDataRole.UserRole) is None:
            self._set_checks([leaf for leaf in self._descendants(item) if not leaf.isHidden()],
                             item.checkState(0) == Qt.CheckState.Checked)
        else:
            self._update_selection()

    def _update_selection(self) -> None:
        with QSignalBlocker(self._tree):
            for branch in self._branches:
                visible = [leaf for leaf in self._descendants(branch) if not leaf.isHidden()]
                checked = sum(leaf.checkState(0) == Qt.CheckState.Checked for leaf in visible)
                state = Qt.CheckState.Unchecked
                if visible and checked == len(visible):
                    state = Qt.CheckState.Checked
                elif checked:
                    state = Qt.CheckState.PartiallyChecked
                branch.setCheckState(0, state)
        count = len(self.selected_keys())
        self._add.setText(tr("添加（{count}）").format(count=count))
        self._add.setEnabled(count > 0)
        self._clear.setEnabled(count > 0)
        self._select_visible.setEnabled(any(not item.isHidden() for item in self._leaves))

    def _filter(self, text: str) -> None:
        query = text.strip().casefold()
        for item in self._leaves:
            item.setHidden(query not in item.text(0).casefold())
        for branch in reversed(self._branches):
            branch.setHidden(all(leaf.isHidden() for leaf in self._descendants(branch)))
        self._update_selection()

    def _check_visible(self) -> None:
        self._set_checks([item for item in self._leaves if not item.isHidden()], True)

    def _clear_selection(self) -> None:
        self._set_checks(self._leaves, False)
