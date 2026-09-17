"""截图管理对话框 - 查看/删除/排序/切换活动截图"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ...core.layout_manager import (
    delete_scene_screenshot,
    get_active_screenshot_index,
    list_scene_screenshots,
    load_scene_screenshot,
    reindex_scene_screenshots,
    set_active_screenshot_index,
)
from ...core.scene_registry import get_scene_name
from ...i18n import tr
from ..button_styles import apply_button_style, fit_button_width

_PREVIEW_HEIGHT = 200


class ScreenshotManagerDialog(QDialog):
    """管理单个 (scene, view) 下的多张截图。

    功能：查看列表、设为活动、删除、上移/下移排序、缩略图预览。
    关闭后宿主通过 ``_changed`` 标记决定是否需要刷新画布。
    """

    def __init__(
        self,
        layout_name: str,
        scene_key: str,
        view: str,
        parent=None,
    ):
        super().__init__(parent)
        self._layout_name = layout_name
        self._scene_key = scene_key
        self._view = view
        self._changed = False

        scene_name = get_scene_name(scene_key)
        view_label = view if view else tr("基底")
        self.setWindowTitle(
            f"{tr('截图管理')} - {scene_name} / {view_label}")
        self.setMinimumSize(520, 480)
        self.resize(600, 540)

        layout = QVBoxLayout(self)

        # ── 截图列表 ──
        self._list = QListWidget()
        self._list.setSelectionMode(
            QListWidget.SelectionMode.SingleSelection)
        self._list.setMinimumHeight(120)
        self._list.setSpacing(4)
        self._list.setStyleSheet(
            "QListWidget { outline: none; }"
            "QListWidget::item {"
            "  min-height: 18px;"
            "  padding: 4px 10px;"
            "  border: 1px solid palette(mid);"
            "  border-radius: 4px;"
            "}"
            "QListWidget::item:selected {"
            "  background-color: palette(highlight);"
            "  color: palette(highlighted-text);"
            "  border: 1px solid palette(highlight);"
            "}"
        )
        self._list.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list, 1)

        # ── 操作按钮行 ──
        btn_row = QHBoxLayout()
        self._btn_activate = QPushButton(tr("设为活动"))
        self._btn_activate.clicked.connect(self._on_activate)
        btn_row.addWidget(self._btn_activate)

        self._btn_delete = QPushButton(tr("删除"))
        self._btn_delete.clicked.connect(self._on_delete)
        btn_row.addWidget(self._btn_delete)

        btn_row.addStretch()

        self._btn_up = QPushButton("↑")
        self._btn_up.setFixedWidth(32)
        self._btn_up.setToolTip(tr("上移"))
        self._btn_up.clicked.connect(lambda: self._on_move(-1))
        btn_row.addWidget(self._btn_up)

        self._btn_down = QPushButton("↓")
        self._btn_down.setFixedWidth(32)
        self._btn_down.setToolTip(tr("下移"))
        self._btn_down.clicked.connect(lambda: self._on_move(1))
        btn_row.addWidget(self._btn_down)

        apply_button_style(self._btn_activate)
        apply_button_style(
            self._btn_up, self._btn_down, variant="neutral")
        apply_button_style(self._btn_delete, variant="danger")
        fit_button_width(self._btn_up, self._btn_down, minimum=32)
        layout.addLayout(btn_row)

        # ── 预览区域 ──
        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumHeight(_PREVIEW_HEIGHT)
        self._preview.setStyleSheet(
            "border: 1px solid palette(mid); border-radius: 4px;"
            "background: palette(base);")
        self._preview.setText(tr("选中截图以预览"))
        layout.addWidget(self._preview)

        # ── 关闭按钮 ──
        close_row = QHBoxLayout()
        close_row.addStretch()
        btn_close = QPushButton(tr("关闭"))
        btn_close.clicked.connect(self.accept)
        apply_button_style(btn_close, variant="neutral")
        close_row.addWidget(btn_close)
        layout.addLayout(close_row)

        self._refresh()

    # ─── 数据 ────────────────────────────────────────────

    def _indices(self) -> list[int]:
        return list_scene_screenshots(
            self._layout_name, self._scene_key, self._view)

    def _active_index(self) -> int:
        return get_active_screenshot_index(
            self._layout_name, self._scene_key, self._view)

    def _refresh(self):
        indices = self._indices()
        active = self._active_index()
        selected_row = self._list.currentRow()
        self._list.clear()
        for i in indices:
            suffix = tr("  (活动)") if i == active else ""
            item = QListWidgetItem(f"截图 {i}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            self._list.addItem(item)
        # 尽量保持选中
        if 0 <= selected_row < self._list.count():
            self._list.setCurrentRow(selected_row)
        elif self._list.count():
            self._list.setCurrentRow(0)
        self._update_buttons()

    def _selected_index(self) -> int | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _update_buttons(self):
        indices = self._indices()
        sel = self._selected_index()
        has_sel = sel is not None
        self._btn_activate.setEnabled(
            has_sel and sel != self._active_index())
        self._btn_delete.setEnabled(has_sel and len(indices) > 0)
        if sel is None or len(indices) <= 1:
            self._btn_up.setEnabled(False)
            self._btn_down.setEnabled(False)
            return
        pos = indices.index(sel) if sel in indices else -1
        self._btn_up.setEnabled(pos > 0)
        self._btn_down.setEnabled(0 <= pos < len(indices) - 1)

    # ─── 预览 ────────────────────────────────────────────

    def _on_selection_changed(self, _row: int):
        self._update_buttons()
        self._update_preview()

    def _update_preview(self):
        idx = self._selected_index()
        if idx is None:
            self._preview.clear()
            self._preview.setText(tr("选中截图以预览"))
            return
        img = load_scene_screenshot(
            self._layout_name, self._scene_key, self._view, idx)
        if img is None:
            self._preview.clear()
            self._preview.setText(tr("无法加载截图"))
            return
        # BGR ndarray -> QPixmap
        h, w = img.shape[:2]
        rgb = img[:, :, ::-1].copy()
        qimg = QImage(rgb.data, w, h, w * 3,
                      QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)
        scaled = pixmap.scaled(
            self._preview.width() - 8, _PREVIEW_HEIGHT - 8,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._preview.setPixmap(scaled)

    # ─── 操作 ────────────────────────────────────────────

    def _on_activate(self):
        idx = self._selected_index()
        if idx is None:
            return
        set_active_screenshot_index(
            self._layout_name, self._scene_key, self._view, idx)
        self._changed = True
        self._refresh()

    def _on_delete(self):
        idx = self._selected_index()
        if idx is None:
            return
        indices = self._indices()
        if len(indices) <= 1:
            QMessageBox.information(
                self, tr("无法删除"),
                tr("至少需要保留一张截图"))
            return
        reply = QMessageBox.question(
            self, tr("确认删除"),
            tr("确定删除截图 {idx} 吗？\n删除后其余截图将自动重编号。"
               ).format(idx=idx),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        delete_scene_screenshot(
            self._layout_name, self._scene_key, self._view, idx)
        self._changed = True
        self._refresh()

    def _on_move(self, direction: int):
        idx = self._selected_index()
        if idx is None:
            return
        indices = self._indices()
        pos = indices.index(idx) if idx in indices else -1
        if pos < 0:
            return
        new_pos = pos + direction
        if new_pos < 0 or new_pos >= len(indices):
            return
        new_idx = indices[new_pos]
        reindex_scene_screenshots(
            self._layout_name, self._scene_key, self._view,
            idx, new_idx)
        self._changed = True
        self._refresh()
        # 选中移动后的项
        moved = self._list.findItems(
            f"截图 {new_idx}", Qt.MatchFlag.MatchExactly)
        if not moved:
            # 序号可能因 active 标注不同而找不到，按 data 查找
            for row in range(self._list.count()):
                item = self._list.item(row)
                if (item is not None
                        and item.data(Qt.ItemDataRole.UserRole) == new_idx):
                    self._list.setCurrentRow(row)
                    break
        else:
            self._list.setCurrentItem(moved[0])
