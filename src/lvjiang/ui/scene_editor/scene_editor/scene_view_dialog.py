"""视图管理对话框 - 开启/取消多视图，新增/重命名/删除视图"""

import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ....core.scene_loader import BASE_VIEW_KEY
from ....core.scene_registry import get_registry, sync_scene_cache

_RE_VIEW_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


class ViewManagerDialog(QDialog):
    """管理单个场景的视图（同一页面的多个滚动态）

    视图只影响编辑期的可见性与底图，不进入运行时寻址。
    """

    def __init__(self, scene_key: str, parent=None):
        super().__init__(parent)
        self._scene_key = scene_key
        self._registry = get_registry()
        self.setWindowTitle("管理视图")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "视图用于把同一页面的多个滚动态分屏排布，避免坐标叠在一起。\n"
            "选定视图时只展示基底视图 + 当前视图的定义。"
        ))

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        layout.addWidget(self._list)

        btn_row = QHBoxLayout()
        self._btn_add = QPushButton("新增视图")
        self._btn_add.clicked.connect(self._on_add)
        btn_row.addWidget(self._btn_add)
        self._btn_rename = QPushButton("重命名")
        self._btn_rename.clicked.connect(self._on_rename)
        btn_row.addWidget(self._btn_rename)
        self._btn_delete = QPushButton("删除视图")
        self._btn_delete.clicked.connect(self._on_delete)
        btn_row.addWidget(self._btn_delete)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        bottom_row = QHBoxLayout()
        self._btn_disable = QPushButton("取消多视图")
        self._btn_disable.setToolTip("仅剩基底视图时可用，取消后所有定义归为无视图区分")
        self._btn_disable.clicked.connect(self._on_disable)
        bottom_row.addWidget(self._btn_disable)
        bottom_row.addStretch()
        btn_close = QPushButton("关闭")
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
            item = QListWidgetItem("（未开启多视图，点「新增视图」自动开启）")
            item.setForeground(Qt.GlobalColor.gray)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(item)
        for v in views:
            item = QListWidgetItem(f"{v.name}  ({v.key})")
            item.setData(Qt.ItemDataRole.UserRole, v.key)
            self._list.addItem(item)
        self._btn_disable.setEnabled(len(views) == 1)

    def _selected_view_key(self) -> str | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    # ─── 操作 ────────────────────────────────────────────

    def _on_add(self):
        key, ok = QInputDialog.getText(
            self, "新增视图", "视图 key（小写字母开头，仅含小写字母/数字/下划线）："
        )
        if not ok:
            return
        key = key.strip()
        if not _RE_VIEW_KEY.match(key):
            QMessageBox.warning(self, "命名非法", "key 必须以小写字母开头，仅含小写字母/数字/下划线。")
            return
        if key == BASE_VIEW_KEY:
            QMessageBox.warning(self, "命名非法", f"{BASE_VIEW_KEY} 是基底视图的保留 key。")
            return
        name, ok = QInputDialog.getText(self, "新增视图", "视图名称：", text=key)
        if not ok:
            return
        try:
            self._registry.add_scene_view(self._scene_key, key, name.strip() or key)
        except ValueError as e:
            QMessageBox.warning(self, "新增失败", str(e))
            return
        sync_scene_cache(self._scene_key)
        self._changed = True
        self._refresh()

    def _on_rename(self):
        key = self._selected_view_key()
        if key is None:
            return
        name, ok = QInputDialog.getText(self, "重命名视图", "新的视图名称：")
        if not ok or not name.strip():
            return
        try:
            self._registry.rename_scene_view(self._scene_key, key, name.strip())
        except ValueError as e:
            QMessageBox.warning(self, "重命名失败", str(e))
            return
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
            QMessageBox.warning(self, "删除失败", str(e))
            return
        sync_scene_cache(self._scene_key)
        self._changed = True
        self._refresh()

    def _on_disable(self):
        try:
            self._registry.disable_scene_views(self._scene_key)
        except ValueError as e:
            QMessageBox.warning(self, "无法取消", str(e))
            return
        sync_scene_cache(self._scene_key)
        self._changed = True
        self._refresh()
