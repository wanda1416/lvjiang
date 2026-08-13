"""视图管理对话框 - 开启/取消多视图，新增/重命名/删除视图，调整顺序"""

import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ...core.layout_manager import rename_view_screenshots
from ...core.scene_definition import BASE_VIEW_KEY
from ...core.scene_registry import get_registry, sync_scene_cache
from ...i18n import tr

_RE_VIEW_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


class ViewManagerDialog(QDialog):
    """管理单个场景的视图（同一页面的多个滚动态）

    视图只影响编辑期的可见性与底图，不进入运行时寻址。
    """

    def __init__(self, scene_key: str, parent=None):
        super().__init__(parent)
        self._scene_key = scene_key
        self._registry = get_registry()
        self.setWindowTitle(tr("管理视图"))
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "视图用于把同一页面的多个滚动态分屏排布，避免坐标叠在一起。\n"
            "选定视图时只展示该视图自身的定义，选「全部」才展示所有视图。"
        ))

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.currentRowChanged.connect(self._update_move_buttons)
        layout.addWidget(self._list)

        btn_row = QHBoxLayout()
        self._btn_add = QPushButton(tr("新增视图"))
        self._btn_add.clicked.connect(self._on_add)
        btn_row.addWidget(self._btn_add)
        self._btn_rename = QPushButton(tr("重命名"))
        self._btn_rename.clicked.connect(self._on_rename)
        btn_row.addWidget(self._btn_rename)
        self._btn_delete = QPushButton(tr("删除视图"))
        self._btn_delete.clicked.connect(self._on_delete)
        btn_row.addWidget(self._btn_delete)
        btn_row.addStretch()
        # 上移/下移按钮
        self._btn_up = QPushButton("↑")
        self._btn_up.setFixedWidth(32)
        self._btn_up.setToolTip(tr("上移视图"))
        self._btn_up.clicked.connect(lambda: self._on_move(-1))
        btn_row.addWidget(self._btn_up)
        self._btn_down = QPushButton("↓")
        self._btn_down.setFixedWidth(32)
        self._btn_down.setToolTip(tr("下移视图"))
        self._btn_down.clicked.connect(lambda: self._on_move(1))
        btn_row.addWidget(self._btn_down)
        layout.addLayout(btn_row)

        bottom_row = QHBoxLayout()
        self._btn_disable = QPushButton(tr("取消多视图"))
        self._btn_disable.setToolTip(tr("仅剩基底视图时可用，取消后所有定义归为无视图区分"))
        self._btn_disable.clicked.connect(self._on_disable)
        bottom_row.addWidget(self._btn_disable)
        bottom_row.addStretch()
        btn_close = QPushButton(tr("关闭"))
        btn_close.clicked.connect(self.accept)
        bottom_row.addWidget(btn_close)
        layout.addLayout(bottom_row)

        self._changed = False
        self._refresh()

    # ─── 数据 ────────────────────────────────────────────

    def _refresh(self):
        self._list.clear()
        views = self._registry.get_scene_views(self._scene_key)
        if not views:
            item = QListWidgetItem(tr("（未开启多视图，点「新增视图」自动开启）"))
            item.setForeground(Qt.GlobalColor.gray)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(item)
        for v in views:
            item = QListWidgetItem(f"{v.name}  ({v.key})")
            item.setData(Qt.ItemDataRole.UserRole, v.key)
            self._list.addItem(item)
        self._btn_disable.setEnabled(len(views) == 1)
        # 更新上移/下移按钮状态
        self._update_move_buttons()

    def _selected_view_key(self) -> str | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    # ─── 操作 ────────────────────────────────────────────

    def _on_add(self):
        """新增视图：单对话框同时输入 key 与名称，实时校验"""
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("新增视图"))
        form = QFormLayout(dialog)
        key_edit = QLineEdit()
        key_edit.setPlaceholderText(tr("小写字母开头，仅含小写字母/数字/下划线"))
        form.addRow(tr("视图 Key:"), key_edit)
        error_label = QLabel()
        error_label.setStyleSheet("color: #c62828;")
        error_label.hide()
        form.addRow("", error_label)
        name_edit = QLineEdit()
        name_edit.setPlaceholderText(tr("留空则与 key 相同"))
        form.addRow(tr("视图名称:"), name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)

        # 实时校验：key 格式 + 保留 key + 重复
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        existing = {v.key for v in
                    self._registry.get_scene_views(self._scene_key)}

        def _validate():
            k = key_edit.text().strip()
            if not k:
                ok_btn.setEnabled(False)
                error_label.hide()
                return
            if not _RE_VIEW_KEY.match(k):
                ok_btn.setEnabled(False)
                error_label.setText(tr("key 必须以小写字母开头，仅含小写字母/数字/下划线"))
                error_label.show()
                return
            if k == BASE_VIEW_KEY:
                ok_btn.setEnabled(False)
                error_label.setText(f"{BASE_VIEW_KEY} 是基底视图的保留 key")
                error_label.show()
                return
            if k in existing:
                ok_btn.setEnabled(False)
                error_label.setText(f"视图 key 已存在: {k}")
                error_label.show()
                return
            ok_btn.setEnabled(True)
            error_label.hide()

        key_edit.textChanged.connect(_validate)
        _validate()

        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key = key_edit.text().strip()
        name = name_edit.text().strip() or key
        try:
            self._registry.add_scene_view(self._scene_key, key, name)
        except ValueError as e:
            QMessageBox.warning(self, tr("新增失败"), str(e))
            return
        sync_scene_cache(self._scene_key)
        self._changed = True
        self._refresh()

    def _on_rename(self):
        """重命名视图：支持修改 key 和名称"""
        old_key = self._selected_view_key()
        if old_key is None:
            return
        views = self._registry.get_scene_views(self._scene_key)
        old_view = next((v for v in views if v.key == old_key), None)
        if old_view is None:
            return
        # 构建重命名对话框
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("重命名视图"))
        form = QFormLayout(dialog)
        key_edit = QLineEdit(old_key)
        key_edit.setPlaceholderText(tr("小写字母开头，仅含小写字母/数字/下划线"))
        form.addRow(tr("视图 Key:"), key_edit)
        error_label = QLabel()
        error_label.setStyleSheet("color: #c62828;")
        error_label.hide()
        form.addRow("", error_label)
        name_edit = QLineEdit(old_view.name)
        name_edit.setPlaceholderText(tr("留空则与 key 相同"))
        form.addRow(tr("视图名称:"), name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
        # 实时校验
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        existing = {v.key for v in views if v.key != old_key}

        def _validate():
            k = key_edit.text().strip()
            if not k:
                ok_btn.setEnabled(False)
                error_label.hide()
                return
            if not _RE_VIEW_KEY.match(k):
                ok_btn.setEnabled(False)
                error_label.setText(tr("key 必须以小写字母开头，仅含小写字母/数字/下划线"))
                error_label.show()
                return
            if k in existing:
                ok_btn.setEnabled(False)
                error_label.setText(f"视图 key 已存在: {k}")
                error_label.show()
                return
            ok_btn.setEnabled(True)
            error_label.hide()

        key_edit.textChanged.connect(_validate)
        _validate()
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_key = key_edit.text().strip()
        new_name = name_edit.text().strip() or new_key
        key_changed = new_key != old_key
        try:
            self._registry.rename_scene_view_key(self._scene_key, old_key, new_key, new_name)
        except ValueError as e:
            QMessageBox.warning(self, tr("重命名失败"), str(e))
            return
        # key 变更时同步重命名截图文件
        if key_changed:
            rename_view_screenshots(self._scene_key, old_key, new_key)
        sync_scene_cache(self._scene_key)
        self._changed = True
        self._refresh()

    def _on_delete(self):
        key = self._selected_view_key()
        if key is None:
            return
        try:
            self._registry.delete_scene_view(self._scene_key, key)
        except ValueError as e:
            QMessageBox.warning(self, tr("删除失败"), str(e))
            return
        sync_scene_cache(self._scene_key)
        self._changed = True
        self._refresh()

    def _on_disable(self):
        try:
            self._registry.disable_scene_views(self._scene_key)
        except ValueError as e:
            QMessageBox.warning(self, tr("无法取消"), str(e))
            return
        sync_scene_cache(self._scene_key)
        self._changed = True
        self._refresh()

    def _update_move_buttons(self):
        """根据当前选中视图的位置更新上移/下移按钮状态"""
        views = self._registry.get_scene_views(self._scene_key)
        key = self._selected_view_key()
        if key is None or len(views) <= 1:
            self._btn_up.setEnabled(False)
            self._btn_down.setEnabled(False)
            return
        idx = next((i for i, v in enumerate(views) if v.key == key), -1)
        self._btn_up.setEnabled(idx > 0)
        self._btn_down.setEnabled(0 <= idx < len(views) - 1)

    def _on_move(self, direction: int):
        """调整视图顺序（direction: -1=上移, +1=下移）"""
        key = self._selected_view_key()
        if key is None:
            return
        try:
            self._registry.move_scene_view(self._scene_key, key, direction)
        except ValueError as e:
            QMessageBox.warning(self, tr("移动失败"), str(e))
            return
        self._changed = True
        self._refresh()
        # 重新选中移动后的项
        views = self._registry.get_scene_views(self._scene_key)
        idx = next((i for i, v in enumerate(views) if v.key == key), -1)
        if idx >= 0:
            self._list.setCurrentRow(idx)
