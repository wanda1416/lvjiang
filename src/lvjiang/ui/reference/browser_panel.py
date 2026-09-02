"""图库浏览面板 - 按分组展示参考图，支持查看、编辑与批量管理

筛选栏与编辑区中“名称、分组”之外的字段由 meta_schema 动态驱动。
"""

import numpy as np
from PyQt6.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QImage, QImageReader, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from lvjiang.core.reference_db import MetaFieldDef, ReferenceDatabase, ReferenceEntry

from ...i18n import tr
from ..button_styles import apply_button_style

THUMB_SIZE = 96  # 图库缩略图尺寸
_CHECK_SIZE = 20  # 批量模式复选框尺寸


class ThumbnailCheckboxDelegate(QStyledItemDelegate):
    """在缩略图左上角绘制复选框（仅批量模式下启用）"""

    def __init__(self, checked_items: set, batch_mode_ref: list, parent=None):
        super().__init__(parent)
        self._checked = checked_items  # 共享的已勾选 filename 集合
        self._batch_mode_ref = batch_mode_ref  # 共享的批量模式状态引用 [bool]

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):  # type: ignore[override]
        # 先绘制默认样式（含缩略图、文本、选中背景）
        super().paint(painter, option, index)
        # 非批量模式不绘制复选框
        if not self._batch_mode_ref or not self._batch_mode_ref[0]:
            return
        filename = index.data(Qt.ItemDataRole.UserRole)
        if not filename:
            return
        # 在缩略图左上角绘制复选框
        rect = option.rect
        cb_rect = QRect(
            rect.left() + 4,
            rect.top() + 4,
            _CHECK_SIZE,
            _CHECK_SIZE,
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 复选框背景（白色填充 + 深色边框）
        painter.setBrush(Qt.GlobalColor.white)
        painter.setPen(Qt.PenStyle.SolidLine)
        painter.drawRect(cb_rect)
        # 勾选标记（粗深色 ✓）
        if filename in self._checked:
            pen = painter.pen()
            pen.setColor(Qt.GlobalColor.darkBlue)
            pen.setWidth(3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            # 勾号路径：左下 → 中下 → 右上
            painter.drawLine(
                cb_rect.left() + 3, cb_rect.top() + 10,
                cb_rect.left() + 8, cb_rect.top() + 15,
            )
            painter.drawLine(
                cb_rect.left() + 8, cb_rect.top() + 15,
                cb_rect.left() + 17, cb_rect.top() + 4,
            )
        painter.restore()

    def checkbox_rect(self, item_rect: QRect) -> QRect:
        """返回指定 item 矩形的复选框区域"""
        return QRect(
            item_rect.left() + 4,
            item_rect.top() + 4,
            _CHECK_SIZE,
            _CHECK_SIZE,
        )


_UNSET = "__unset__"  # 筛选“未填写”标记


def _reference_file_text(filename: str, image_path) -> str:
    """格式化参考图文件信息；无法读取图片时保留原文件名展示。"""
    size = QImageReader(str(image_path)).size()
    if size.isValid():
        return f"文件: {filename} ({size.width()} × {size.height()})"
    return f"文件: {filename}"


class _BatchListWidget(QListWidget):
    """支持批量模式复选框点击的列表控件"""

    def __init__(self, panel: "BrowserPanel", parent=None):
        super().__init__(parent)
        self._panel = panel

    def mousePressEvent(self, event):
        if self._panel._batch_mode:
            # 检查是否点击了复选框区域
            for i in range(self.count()):
                item = self.item(i)
                if item is None:
                    continue
                item_rect = self.visualItemRect(item)
                delegate = self.itemDelegate()
                if isinstance(delegate, ThumbnailCheckboxDelegate):
                    cb_rect = delegate.checkbox_rect(item_rect)
                    if cb_rect.contains(event.position().toPoint()):
                        self._panel._toggle_checkbox(item)
                        return  # 消费事件，不触发选择
        super().mousePressEvent(event)


class BrowserPanel(QWidget):
    """图库浏览面板 - 按分组展示参考图库，支持批量管理"""

    entry_selected = pyqtSignal(str)  # filename
    refresh_requested = pyqtSignal()
    data_changed = pyqtSignal()  # 数据变动信号（保存/删除/批量修改）

    def __init__(self, db: ReferenceDatabase, parent=None):
        super().__init__(parent)
        self._db = db
        self._known_groups: list[str] = []
        self._batch_mode = False
        self._batch_mode_ref: list[bool] = [False]  # 共享给 delegate 的批量模式引用
        self._checked_items: set[str] = set()  # 已勾选的 filename 集合（批量模式）
        # 动态 meta 控件
        self._meta_filter_combos: dict[str, QComboBox] = {}
        self._meta_filter_fields: dict[str, MetaFieldDef] = {}  # key -> 字段定义（含 type/sort_by）
        self._meta_edits: dict[str, QLineEdit] = {}
        self._batch_meta_edits: dict[str, QLineEdit] = {}
        self._init_ui()
        # 安装 delegate（常驻，根据 _batch_mode_ref 状态决定是否绘制复选框）
        self._list.setItemDelegate(
            ThumbnailCheckboxDelegate(self._checked_items, self._batch_mode_ref, self._list)
        )
        self.rebuild_meta_fields()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)

        # ── 过滤栏 ──
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel(tr("分组:")))
        self._group_filter = QComboBox()
        self._group_filter.addItem(tr("全部"), "")
        self._group_filter.setMinimumWidth(120)
        filter_layout.addWidget(self._group_filter)

        filter_layout.addWidget(QLabel(tr("名称:")))
        self._label_filter = QComboBox()
        self._label_filter.addItem(tr("全部"), "")
        self._label_filter.setMinimumWidth(120)
        filter_layout.addWidget(self._label_filter)

        # 动态 meta 筛选容器（可筛选字段）
        self._meta_filter_layout = QHBoxLayout()
        filter_layout.addLayout(self._meta_filter_layout)

        self._refresh_btn = QPushButton(tr("刷新"))
        filter_layout.addWidget(self._refresh_btn)

        self._batch_btn = QPushButton(tr("批量管理"))
        self._batch_btn.setCheckable(True)
        filter_layout.addWidget(self._batch_btn)
        apply_button_style(
            self._refresh_btn, self._batch_btn, variant="neutral"
        )

        filter_layout.addStretch()
        main_layout.addLayout(filter_layout)

        # ── 主体：列表 + 编辑区 ──
        body_layout = QHBoxLayout()

        # 左侧：参考图列表
        left_layout = QVBoxLayout()
        self._list = _BatchListWidget(self)
        self._list.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self._list.setFlow(QListWidget.Flow.LeftToRight)
        self._list.setWrapping(True)
        self._list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._list.setSpacing(4)
        self._list.setGridSize(QSize(THUMB_SIZE + 12, THUMB_SIZE + 12))
        self._list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        # 批量模式选中项深色背景，突出已勾选的参考图
        self._list.setStyleSheet(
            "QListWidget::item:selected {"
            "  background-color: #3a6ea5;"
            "  border: 1px solid #2a5a8a;"
            "}"
        )
        self._list.itemClicked.connect(self._on_item_clicked)
        left_layout.addWidget(self._list)

        # 批量管理工具栏（默认隐藏）
        self._batch_toolbar = QWidget()
        batch_layout = QHBoxLayout(self._batch_toolbar)
        batch_layout.setContentsMargins(0, 0, 0, 0)

        self._select_all_btn = QPushButton(tr("全选"))
        batch_layout.addWidget(self._select_all_btn)
        self._deselect_all_btn = QPushButton(tr("全不选"))
        batch_layout.addWidget(self._deselect_all_btn)
        self._invert_sel_btn = QPushButton(tr("反选"))
        batch_layout.addWidget(self._invert_sel_btn)
        apply_button_style(
            self._select_all_btn,
            self._deselect_all_btn,
            self._invert_sel_btn,
            variant="neutral",
        )
        batch_layout.addStretch()

        self._batch_toolbar.setVisible(False)
        left_layout.addWidget(self._batch_toolbar)

        body_layout.addLayout(left_layout, 3)

        # 右侧：编辑区（靠左，不占满底部）
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # 单个编辑区
        edit_group = QGroupBox(tr("参考图信息"))
        edit_layout = QVBoxLayout(edit_group)

        self._file_label = QLabel(tr("未选择"))
        self._file_label.setStyleSheet("color: gray;")
        edit_layout.addWidget(self._file_label)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel(tr("名称:")))
        self._label_edit = QLineEdit()
        row1.addWidget(self._label_edit)
        edit_layout.addLayout(row1)

        row_group = QHBoxLayout()
        row_group.addWidget(QLabel(tr("分组:")))
        self._group_edit = QComboBox()
        self._group_edit.setEditable(True)
        row_group.addWidget(self._group_edit, 1)  # stretch=1 让它填充剩余空间
        edit_layout.addLayout(row_group)

        # 动态 meta 编辑容器（名称/分组之后，备注之前）
        self._meta_edit_layout = QVBoxLayout()
        self._meta_edit_layout.setContentsMargins(0, 0, 0, 0)
        edit_layout.addLayout(self._meta_edit_layout)

        row_notes = QHBoxLayout()
        row_notes.addWidget(QLabel(tr("备注:")))
        self._notes_edit = QLineEdit()
        row_notes.addWidget(self._notes_edit)
        edit_layout.addLayout(row_notes)

        btn_layout = QHBoxLayout()
        self._save_btn = QPushButton(tr("保存"))
        self._save_btn.setEnabled(False)
        btn_layout.addWidget(self._save_btn)
        self._delete_btn = QPushButton(tr("删除"))
        self._delete_btn.setEnabled(False)
        btn_layout.addWidget(self._delete_btn)
        apply_button_style(self._save_btn)
        apply_button_style(self._delete_btn, variant="danger")
        btn_layout.addStretch()
        edit_layout.addLayout(btn_layout)

        right_layout.addWidget(edit_group)

        # 批量编辑区（默认隐藏）
        self._batch_group = QGroupBox(tr("批量设置"))
        batch_edit_layout = QVBoxLayout(self._batch_group)

        batch_hint = QLabel(tr("已填写的字段将应用到所有选中项"))
        batch_hint.setStyleSheet("color: gray; font-size: 11px;")
        batch_edit_layout.addWidget(batch_hint)

        brow1 = QHBoxLayout()
        brow1.addWidget(QLabel(tr("名称:")))
        self._batch_label_edit = QLineEdit()
        self._batch_label_edit.setPlaceholderText(tr("留空则不修改"))
        brow1.addWidget(self._batch_label_edit)
        batch_edit_layout.addLayout(brow1)

        brow_group = QHBoxLayout()
        brow_group.addWidget(QLabel(tr("分组:")))
        self._batch_group_edit = QComboBox()
        self._batch_group_edit.setEditable(True)
        brow_group.addWidget(self._batch_group_edit, 1)
        batch_edit_layout.addLayout(brow_group)

        # 动态 meta 批量编辑容器
        self._batch_meta_layout = QVBoxLayout()
        self._batch_meta_layout.setContentsMargins(0, 0, 0, 0)
        batch_edit_layout.addLayout(self._batch_meta_layout)

        self._batch_apply_btn = QPushButton(tr("应用设置"))
        self._batch_apply_btn.setEnabled(False)
        batch_edit_layout.addWidget(self._batch_apply_btn)
        apply_button_style(self._batch_apply_btn)

        self._batch_delete_btn = QPushButton(tr("全部删除"))
        self._batch_delete_btn.setEnabled(False)
        apply_button_style(self._batch_delete_btn, variant="danger")
        batch_edit_layout.addWidget(self._batch_delete_btn)

        self._batch_group.setVisible(False)
        right_layout.addWidget(self._batch_group)

        right_layout.addStretch()
        body_layout.addWidget(right_widget, 1)

        main_layout.addLayout(body_layout, 1)

        # ── 信号 ──
        self._refresh_btn.clicked.connect(self.refresh)
        self._batch_btn.toggled.connect(self._on_batch_mode_toggled)
        self._save_btn.clicked.connect(self._on_save)
        self._delete_btn.clicked.connect(self._on_delete)
        self._group_filter.currentIndexChanged.connect(self._on_filter_changed)
        self._label_filter.currentIndexChanged.connect(self._on_filter_changed)
        self._select_all_btn.clicked.connect(self._on_select_all)
        self._deselect_all_btn.clicked.connect(self._on_deselect_all)
        self._invert_sel_btn.clicked.connect(self._invert_selection)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._batch_apply_btn.clicked.connect(self._on_batch_apply)
        self._batch_delete_btn.clicked.connect(self._on_batch_delete)

    # ── 动态 meta 字段 ──

    @staticmethod
    def _clear_layout(layout):
        """递归清空布局中的所有控件与子布局"""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            else:
                sub = item.layout()
                if sub is not None:
                    BrowserPanel._clear_layout(sub)

    @staticmethod
    def _meta_text(entry: ReferenceEntry, key: str) -> str:
        value = entry.meta.get(key)
        return "" if value is None else str(value)

    def rebuild_meta_fields(self):
        """按 meta_schema 重建筛选/编辑/批量的动态 meta 控件

        仅展示 input 字段（排除 output 字段）：output 字段是从图中识别读取的，
        不是用户可编辑的元信息。
        """
        self._clear_layout(self._meta_filter_layout)
        self._clear_layout(self._meta_edit_layout)
        self._clear_layout(self._batch_meta_layout)
        self._meta_filter_combos.clear()
        self._meta_edits.clear()
        self._batch_meta_edits.clear()

        for field in self._db.get_custom_input_fields():
            # 编辑区输入
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{field.name}:"))
            edit = QLineEdit()
            row.addWidget(edit)
            self._meta_edit_layout.addLayout(row)
            self._meta_edits[field.key] = edit

            # 批量输入
            brow = QHBoxLayout()
            brow.addWidget(QLabel(f"{field.name}:"))
            bedit = QLineEdit()
            bedit.setPlaceholderText(tr("留空则不修改"))
            brow.addWidget(bedit)
            self._batch_meta_layout.addLayout(brow)
            self._batch_meta_edits[field.key] = bedit

            # 可筛选字段生成筛选下拉
            if field.filterable:
                self._meta_filter_layout.addWidget(QLabel(f"{field.name}:"))
                combo = QComboBox()
                combo.setMinimumWidth(80)
                combo.currentIndexChanged.connect(self._on_filter_changed)
                self._meta_filter_layout.addWidget(combo)
                self._meta_filter_combos[field.key] = combo
                self._meta_filter_fields[field.key] = field

        self._refresh_meta_filter_values()

    def _refresh_meta_filter_values(self):
        """刷新各 meta 筛选下拉的候选值（保留当前选择）"""
        for key, combo in self._meta_filter_combos.items():
            field = self._meta_filter_fields.get(key)
            if field is None:
                continue
            cur = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(tr("全部"), None)
            combo.addItem(tr("未填写"), _UNSET)
            for v in self._db.get_meta_options(field):
                combo.addItem(v, v)
            idx = combo.findData(cur)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    # ── 公共方法 ──

    def set_known_groups(self, groups: list[str]):
        """更新已知分组列表（供外部调用）"""
        self._known_groups = list(groups)
        # 更新过滤下拉
        cur = self._group_filter.currentData()
        self._group_filter.blockSignals(True)
        self._group_filter.clear()
        self._group_filter.addItem(tr("全部"), "")
        for g in groups:
            self._group_filter.addItem(g, g)
        idx = self._group_filter.findData(cur)
        if idx >= 0:
            self._group_filter.setCurrentIndex(idx)
        self._group_filter.blockSignals(False)
        # 更新分组编辑下拉
        self._update_group_edit()
        # 更新批量分组下拉
        self._batch_group_edit.blockSignals(True)
        self._batch_group_edit.clear()
        self._batch_group_edit.addItem("")  # 空选项作为默认，防止误操作
        self._batch_group_edit.addItems(groups)
        self._batch_group_edit.blockSignals(False)

    def _update_group_edit(self):
        """更新分组编辑下拉框的选项（保留当前输入）"""
        current = self._group_edit.currentText()
        self._group_edit.blockSignals(True)
        self._group_edit.clear()
        self._group_edit.addItems(self._known_groups)
        self._group_edit.setEditText(current)
        self._group_edit.blockSignals(False)

    def refresh(self):
        """刷新列表和过滤器"""
        self._list.clear()
        self._update_filters()
        self._refresh_meta_filter_values()

        group = self._group_filter.currentData()
        label = self._label_filter.currentData()

        # 先获取所有的参考图
        entries = self._db.list_entries()

        # 处理"未分组"特殊情况
        if group == "ungrouped":
            entries = [e for e in entries if not e.group]
        elif group:
            entries = [e for e in entries if e.group == group]

        # 处理"未命名"特殊情况
        if label == "unnamed":
            entries = [e for e in entries if not e.label]
        elif label:
            entries = [e for e in entries if e.label == label]

        # 处理动态 meta 筛选
        for key, combo in self._meta_filter_combos.items():
            data = combo.currentData()
            if data is None:
                continue  # 全部
            if data == _UNSET:
                entries = [e for e in entries if not self._meta_text(e, key)]
            else:
                entries = [e for e in entries if self._meta_text(e, key) == data]

        for entry in entries:
            self._add_item(entry)

        # 刷新后只保留仍存在的勾选项（切换筛选条件时不丢失勾选）
        current_files = {e.file for e in entries}
        stale = self._checked_items - current_files
        if stale:
            self._checked_items -= stale
        self._update_batch_buttons()

        self.refresh_requested.emit()

    def _on_select_all(self):
        """全选：同步选中状态与复选框勾选状态"""
        self._list.selectAll()
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item is None:
                continue
            filename = item.data(Qt.ItemDataRole.UserRole)
            if filename:
                self._checked_items.add(filename)
        self._list.viewport().update()
        self._update_batch_buttons()

    def _on_deselect_all(self):
        """全不选：清空选中与复选框勾选"""
        self._list.clearSelection()
        self._checked_items.clear()
        self._list.viewport().update()
        self._update_batch_buttons()

    def _toggle_checkbox(self, item: QListWidgetItem):
        """切换单个项的复选框状态（批量模式）"""
        filename = item.data(Qt.ItemDataRole.UserRole)
        if not filename:
            return
        if filename in self._checked_items:
            self._checked_items.discard(filename)
        else:
            self._checked_items.add(filename)
        # 触发 delegate 重绘（QListWidget.update 不支持 QRect，改用 viewport）
        self._list.viewport().update()
        self._update_batch_buttons()

    def _update_batch_buttons(self):
        """根据已勾选项数量更新批量按钮的启用状态"""
        has_checked = bool(self._checked_items) and self._batch_mode
        self._batch_apply_btn.setEnabled(has_checked)
        self._batch_delete_btn.setEnabled(has_checked)

    def _get_checked_items(self) -> list:
        """返回所有已勾选的项（批量模式下）"""
        result = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item is None:
                continue
            filename = item.data(Qt.ItemDataRole.UserRole)
            if filename in self._checked_items:
                result.append(item)
        return result

    def _update_filters(self):
        """更新分组/名称过滤下拉框的选项"""
        cur_group = self._group_filter.currentData()
        cur_label = self._label_filter.currentData()

        self._group_filter.blockSignals(True)
        self._group_filter.clear()
        self._group_filter.addItem(tr("全部"), "")
        self._group_filter.addItem(tr("- 未分组 -"), "ungrouped")
        for g in self._db.get_groups():
            self._group_filter.addItem(g, g)
        idx = self._group_filter.findData(cur_group)
        if idx >= 0:
            self._group_filter.setCurrentIndex(idx)
        self._group_filter.blockSignals(False)

        self._label_filter.blockSignals(True)
        self._label_filter.clear()
        self._label_filter.addItem(tr("全部"), "")
        self._label_filter.addItem(tr("- 未命名 -"), "unnamed")
        for t in self._db.get_labels():
            self._label_filter.addItem(t, t)
        idx = self._label_filter.findData(cur_label)
        if idx >= 0:
            self._label_filter.setCurrentIndex(idx)
        self._label_filter.blockSignals(False)

    def _add_item(self, entry: ReferenceEntry):
        """添加一个参考图项到列表"""
        thumb = self._load_thumbnail(entry.file)
        item = QListWidgetItem()
        if thumb:
            item.setIcon(QIcon(QPixmap.fromImage(thumb)))
        # tooltip 显示参考图信息（名称/分组 + 各 meta 字段 + 备注）
        tooltip_parts = [entry.file]
        if entry.label:
            tooltip_parts.append(f"名称: {entry.label}")
        if entry.group:
            tooltip_parts.append(f"分组: {entry.group}")
        for field in self._db.get_meta_schema():
            val = self._meta_text(entry, field.key)
            if val:
                tooltip_parts.append(f"{field.name}: {val}")
        if entry.notes:
            tooltip_parts.append(f"备注: {entry.notes}")
        item.setToolTip("\n".join(tooltip_parts))
        item.setData(Qt.ItemDataRole.UserRole, entry.file)
        self._list.addItem(item)

    def _load_thumbnail(self, filename: str) -> QImage | None:
        """加载参考图并生成缩略图"""
        import cv2
        from PIL import Image
        path = self._db.image_path(filename)
        try:
            img_rgb = np.array(Image.open(path))
            bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
            h, w = bgr.shape[:2]
            scale = min(THUMB_SIZE / w, THUMB_SIZE / h)
            new_w, new_h = int(w * scale), int(h * scale)
            resized = cv2.resize(bgr, (new_w, new_h))
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            return QImage(bytes(rgb.copy().data), new_w, new_h, new_w * 3, QImage.Format.Format_RGB888)
        except Exception:
            return None

    def _invert_selection(self):
        """反选所有项：同步选中状态与复选框勾选状态"""
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item is None:
                continue
            item.setSelected(not item.isSelected())
            filename = item.data(Qt.ItemDataRole.UserRole)
            if not filename:
                continue
            if item.isSelected():
                self._checked_items.add(filename)
            else:
                self._checked_items.discard(filename)
        self._list.viewport().update()
        self._update_batch_buttons()

    # ── 槽函数 ──

    def _on_batch_mode_toggled(self, checked: bool):
        """切换批量管理模式"""
        self._batch_mode = checked
        self._batch_toolbar.setVisible(checked)
        self._batch_group.setVisible(checked)
        # 非批量模式下隐藏单个编辑区的按钮
        self._save_btn.setVisible(not checked)
        self._delete_btn.setVisible(not checked)
        # 更新批量模式引用（delegate 会根据此状态决定是否绘制复选框）
        self._batch_mode_ref[0] = checked
        if checked:
            self._checked_items.clear()
            self._batch_btn.setText(tr("退出批量"))
            self._batch_btn.setStyleSheet(
                "QPushButton { background-color: #1976d2; color: white; }"
                "QPushButton:hover { background-color: #1565c0; }"
            )
            self._list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
            self._update_batch_buttons()
        else:
            self._checked_items.clear()
            self._batch_btn.setText(tr("批量管理"))
            self._batch_btn.setStyleSheet("")  # 恢复默认样式
            self._list.clearSelection()
            self._update_batch_buttons()
        # 触发重绘（delegate 根据新状态绘制/不绘制复选框）
        self._list.viewport().update()

    def _on_selection_changed(self):
        """选择变化时更新批量按钮（非批量模式下显示编辑区）"""
        if not self._batch_mode:
            selected = self._list.selectedItems()
            if len(selected) == 1:
                self._on_item_clicked(selected[0])

    def _on_item_clicked(self, item: QListWidgetItem):
        """点击单项时加载到编辑区"""
        if self._batch_mode:
            return  # 批量模式下不响应单项点击
        filename = item.data(Qt.ItemDataRole.UserRole)
        if not filename:
            return
        entry = self._db.get_entry(filename)
        if not entry:
            return

        self._file_label.setText(
            _reference_file_text(entry.file, self._db.image_path(entry.file))
        )
        self._label_edit.setText(entry.label)
        self._update_group_edit()
        self._group_edit.setEditText(entry.group)
        for key, edit in self._meta_edits.items():
            edit.setText(self._meta_text(entry, key))
        self._notes_edit.setText(entry.notes)
        self._save_btn.setEnabled(True)
        self._delete_btn.setEnabled(True)
        self.entry_selected.emit(filename)

    def _on_save(self):
        item = self._list.currentItem()
        if not item:
            return
        filename = item.data(Qt.ItemDataRole.UserRole)

        # 构建 meta 更新（分组 + 各动态字段的非空文本）
        meta = {}
        group = self._group_edit.currentText().strip()
        if group:
            meta["group"] = group
        for key, edit in self._meta_edits.items():
            val = edit.text().strip()
            if val:
                meta[key] = val

        self._db.update_entry(
            filename,
            label=self._label_edit.text().strip(),
            meta=meta,
            notes=self._notes_edit.text().strip(),
        )
        self.data_changed.emit()  # 通知数据变动
        self.refresh()

    def _on_delete(self):
        item = self._list.currentItem()
        if not item:
            return
        filename = item.data(Qt.ItemDataRole.UserRole)
        self._db.remove_entry(filename)
        self._save_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        self._file_label.setText(tr("未选择"))
        self.data_changed.emit()  # 通知数据变动
        self.refresh()

    def _on_filter_changed(self):
        self.refresh()

    def _on_batch_apply(self):
        """批量应用设置（作用于所有已勾选项）"""
        checked = self._get_checked_items()
        if not checked:
            return

        # 收集要应用的字段（只应用已填写的）
        updates = {}
        new_label = self._batch_label_edit.text().strip()
        if new_label:
            updates['label'] = new_label

        meta = {}
        new_group = self._batch_group_edit.currentText().strip()
        if new_group:
            meta['group'] = new_group
        for key, edit in self._batch_meta_edits.items():
            val = edit.text().strip()
            if val:
                meta[key] = val

        if meta:
            updates['meta'] = meta

        if not updates:
            return

        # 应用到所有已勾选项
        for item in checked:
            filename = item.data(Qt.ItemDataRole.UserRole)
            if filename:
                self._db.update_entry(filename, **updates)

        self.data_changed.emit()  # 通知数据变动

        # 保存当前分组过滤器（应用后保持不变）
        current_group_filter = self._group_filter.currentData()

        # 清空批量输入
        self._batch_label_edit.clear()
        self._batch_group_edit.blockSignals(True)
        self._batch_group_edit.setEditText("")
        self._batch_group_edit.blockSignals(False)
        for edit in self._batch_meta_edits.values():
            edit.clear()

        # 刷新列表和分组（保持分组过滤器）
        self.set_known_groups(self._db.get_groups())
        # 确保分组过滤器保持不变
        idx = self._group_filter.findData(current_group_filter)
        if idx >= 0:
            self._group_filter.setCurrentIndex(idx)
        self.refresh()

    def _on_batch_delete(self):
        """批量删除已勾选的参考图"""
        checked = self._get_checked_items()
        if not checked:
            return

        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            tr("确认删除"),
            f"确定要删除已勾选的 {len(checked)} 个参考图吗？\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for item in checked:
            filename = item.data(Qt.ItemDataRole.UserRole)
            if filename:
                self._db.remove_entry(filename)

        self.data_changed.emit()
        # 保存当前分组过滤器（删除后保持不变）
        current_group_filter = self._group_filter.currentData()
        self.set_known_groups(self._db.get_groups())
        # 确保分组过滤器保持不变
        idx = self._group_filter.findData(current_group_filter)
        if idx >= 0:
            self._group_filter.setCurrentIndex(idx)
        self.refresh()
