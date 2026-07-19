"""库存浏览面板 - 按分组展示材料，支持查看、编辑与批量管理"""

from PyQt6.QtCore import pyqtSignal, Qt, QSize
from PyQt6.QtGui import QImage, QPixmap, QIcon
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QCheckBox,
)

import numpy as np

from ...core.material_db import MaterialDatabase, MaterialEntry


THUMB_SIZE = 96  # 库存缩略图尺寸


class BrowserPanel(QWidget):
    """库存浏览面板 - 按分组展示材料库，支持批量管理"""

    entry_selected = pyqtSignal(str)  # filename
    refresh_requested = pyqtSignal()
    data_changed = pyqtSignal()  # 数据变动信号（保存/删除/批量修改）

    def __init__(self, db: MaterialDatabase, parent=None):
        super().__init__(parent)
        self._db = db
        self._known_groups: list[str] = []
        self._batch_mode = False
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)

        # ── 过滤栏 ──
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("分组:"))
        self._group_filter = QComboBox()
        self._group_filter.addItem("全部", "")
        self._group_filter.setMinimumWidth(120)
        filter_layout.addWidget(self._group_filter)

        filter_layout.addWidget(QLabel("名称:"))
        self._type_filter = QComboBox()
        self._type_filter.addItem("全部", "")
        self._type_filter.setMinimumWidth(120)
        filter_layout.addWidget(self._type_filter)

        filter_layout.addWidget(QLabel("等级:"))
        self._level_filter = QComboBox()
        self._level_filter.setMinimumWidth(80)
        filter_layout.addWidget(self._level_filter)

        self._refresh_btn = QPushButton("刷新")
        filter_layout.addWidget(self._refresh_btn)

        self._batch_btn = QPushButton("批量管理")
        self._batch_btn.setCheckable(True)
        filter_layout.addWidget(self._batch_btn)

        filter_layout.addStretch()
        main_layout.addLayout(filter_layout)

        # ── 主体：列表 + 编辑区 ──
        body_layout = QHBoxLayout()

        # 左侧：材料列表
        left_layout = QVBoxLayout()
        self._list = QListWidget()
        self._list.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self._list.setFlow(QListWidget.Flow.LeftToRight)
        self._list.setWrapping(True)
        self._list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._list.setSpacing(4)
        self._list.setGridSize(QSize(THUMB_SIZE + 12, THUMB_SIZE + 12))
        self._list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._list.itemClicked.connect(self._on_item_clicked)
        left_layout.addWidget(self._list)

        # 批量管理工具栏（默认隐藏）
        self._batch_toolbar = QWidget()
        batch_layout = QHBoxLayout(self._batch_toolbar)
        batch_layout.setContentsMargins(0, 0, 0, 0)

        self._select_all_btn = QPushButton("全选")
        batch_layout.addWidget(self._select_all_btn)
        self._deselect_all_btn = QPushButton("全不选")
        batch_layout.addWidget(self._deselect_all_btn)
        self._invert_sel_btn = QPushButton("反选")
        batch_layout.addWidget(self._invert_sel_btn)
        batch_layout.addStretch()

        self._batch_toolbar.setVisible(False)
        left_layout.addWidget(self._batch_toolbar)

        body_layout.addLayout(left_layout, 3)

        # 右侧：编辑区（靠左，不占满底部）
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # 单个编辑区
        edit_group = QGroupBox("材料信息")
        edit_layout = QVBoxLayout(edit_group)

        self._file_label = QLabel("未选择")
        self._file_label.setStyleSheet("color: gray;")
        edit_layout.addWidget(self._file_label)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("名称:"))
        self._type_edit = QLineEdit()
        row1.addWidget(self._type_edit)
        edit_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("等级:"))
        self._level_edit = QLineEdit()
        row2.addWidget(self._level_edit)
        edit_layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("分组:"))
        self._group_edit = QComboBox()
        self._group_edit.setEditable(True)
        row3.addWidget(self._group_edit, 1)  # stretch=1 让它填充剩余空间
        edit_layout.addLayout(row3)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("备注:"))
        self._notes_edit = QLineEdit()
        row4.addWidget(self._notes_edit)
        edit_layout.addLayout(row4)

        btn_layout = QHBoxLayout()
        self._save_btn = QPushButton("保存")
        self._save_btn.setEnabled(False)
        btn_layout.addWidget(self._save_btn)
        self._delete_btn = QPushButton("删除")
        self._delete_btn.setEnabled(False)
        btn_layout.addWidget(self._delete_btn)
        btn_layout.addStretch()
        edit_layout.addLayout(btn_layout)

        right_layout.addWidget(edit_group)

        # 批量编辑区（默认隐藏）
        self._batch_group = QGroupBox("批量设置")
        batch_edit_layout = QVBoxLayout(self._batch_group)

        batch_hint = QLabel("已填写的字段将应用到所有选中项")
        batch_hint.setStyleSheet("color: gray; font-size: 11px;")
        batch_edit_layout.addWidget(batch_hint)

        brow1 = QHBoxLayout()
        brow1.addWidget(QLabel("名称:"))
        self._batch_type_edit = QLineEdit()
        self._batch_type_edit.setPlaceholderText("留空则不修改")
        brow1.addWidget(self._batch_type_edit)
        batch_edit_layout.addLayout(brow1)

        brow2 = QHBoxLayout()
        brow2.addWidget(QLabel("等级:"))
        self._batch_level_edit = QLineEdit()
        self._batch_level_edit.setPlaceholderText("留空则不修改")
        brow2.addWidget(self._batch_level_edit)
        batch_edit_layout.addLayout(brow2)

        brow3 = QHBoxLayout()
        brow3.addWidget(QLabel("分组:"))
        self._batch_group_edit = QComboBox()
        self._batch_group_edit.setEditable(True)
        brow3.addWidget(self._batch_group_edit, 1)
        batch_edit_layout.addLayout(brow3)

        self._batch_apply_btn = QPushButton("应用设置")
        self._batch_apply_btn.setEnabled(False)
        batch_edit_layout.addWidget(self._batch_apply_btn)

        self._batch_delete_btn = QPushButton("全部删除")
        self._batch_delete_btn.setEnabled(False)
        self._batch_delete_btn.setStyleSheet("color: #d32f2f;")
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
        self._type_filter.currentIndexChanged.connect(self._on_filter_changed)
        self._level_filter.currentIndexChanged.connect(self._on_filter_changed)
        self._select_all_btn.clicked.connect(self._list.selectAll)
        self._deselect_all_btn.clicked.connect(self._list.clearSelection)
        self._invert_sel_btn.clicked.connect(self._invert_selection)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._batch_apply_btn.clicked.connect(self._on_batch_apply)
        self._batch_delete_btn.clicked.connect(self._on_batch_delete)

    # ── 公共方法 ──

    def set_known_groups(self, groups: list[str]):
        """更新已知分组列表（供外部调用）"""
        self._known_groups = list(groups)
        # 更新过滤下拉
        cur = self._group_filter.currentData()
        self._group_filter.blockSignals(True)
        self._group_filter.clear()
        self._group_filter.addItem("全部", "")
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

        group = self._group_filter.currentData()
        mat_type = self._type_filter.currentData()
        level = self._level_filter.currentData()

        # 先获取所有材料
        entries = self._db.list_materials()

        # 处理“未分组”特殊情况
        if group == "ungrouped":
            entries = [e for e in entries if not e.group]
        elif group:
            entries = [e for e in entries if e.group == group]

        # 处理“未命名”特殊情况
        if mat_type == "unnamed":
            entries = [e for e in entries if not e.type]
        elif mat_type:
            entries = [e for e in entries if e.type == mat_type]

        # 处理“未分级”特殊情况
        if level == "ungraded":
            entries = [e for e in entries if e.level is None]
        elif level is not None:
            entries = [e for e in entries if e.level == level]
        # 如果没有选择等级（currentIndex < 0），则不过滤

        for entry in entries:
            self._add_item(entry)

        self.refresh_requested.emit()

    def _update_filters(self):
        """更新过滤下拉框的选项"""
        cur_group = self._group_filter.currentData()
        cur_type = self._type_filter.currentData()
        cur_level = self._level_filter.currentData()

        self._group_filter.blockSignals(True)
        self._group_filter.clear()
        self._group_filter.addItem("全部", "")
        self._group_filter.addItem("- 未分组 -", "ungrouped")
        for g in self._db.get_groups():
            self._group_filter.addItem(g, g)
        idx = self._group_filter.findData(cur_group)
        if idx >= 0:
            self._group_filter.setCurrentIndex(idx)
        self._group_filter.blockSignals(False)

        self._type_filter.blockSignals(True)
        self._type_filter.clear()
        self._type_filter.addItem("全部", "")
        self._type_filter.addItem("- 未命名 -", "unnamed")
        for t in self._db.get_types():
            self._type_filter.addItem(t, t)
        idx = self._type_filter.findData(cur_type)
        if idx >= 0:
            self._type_filter.setCurrentIndex(idx)
        self._type_filter.blockSignals(False)

        self._level_filter.blockSignals(True)
        self._level_filter.clear()
        # 全部（不过滤）
        self._level_filter.addItem("全部", None)
        # 未分级（level 为 null）
        self._level_filter.addItem("未分级", "ungraded")
        # 等级按倒序排列（高等级在前）
        for lv in reversed(self._db.get_levels()):
            self._level_filter.addItem(f"{lv}级", lv)
        idx = self._level_filter.findData(cur_level)
        if idx >= 0:
            self._level_filter.setCurrentIndex(idx)
        self._level_filter.blockSignals(False)

    def _add_item(self, entry: MaterialEntry):
        """添加一个材料项到列表"""
        thumb = self._load_thumbnail(entry.file)
        item = QListWidgetItem()
        if thumb:
            item.setIcon(QIcon(QPixmap.fromImage(thumb)))
        # tooltip 显示材料信息
        tooltip_parts = [entry.file]
        if entry.type:
            tooltip_parts.append(f"名称: {entry.type}")
        if entry.level:
            tooltip_parts.append(f"等级: {entry.level}")
        if entry.group:
            tooltip_parts.append(f"分组: {entry.group}")
        item.setToolTip("\n".join(tooltip_parts))
        item.setData(Qt.ItemDataRole.UserRole, entry.file)
        self._list.addItem(item)

    def _load_thumbnail(self, filename: str) -> QImage | None:
        """加载材料图片并生成缩略图"""
        import cv2
        from PIL import Image
        path = self._db.dir / filename
        try:
            img_rgb = np.array(Image.open(path))
            bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
            h, w = bgr.shape[:2]
            scale = min(THUMB_SIZE / w, THUMB_SIZE / h)
            new_w, new_h = int(w * scale), int(h * scale)
            resized = cv2.resize(bgr, (new_w, new_h))
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            return QImage(rgb.copy().data, new_w, new_h, new_w * 3, QImage.Format.Format_RGB888)
        except Exception:
            return None

    def _invert_selection(self):
        """反选所有项"""
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setSelected(not item.isSelected())

    # ── 槽函数 ──

    def _on_batch_mode_toggled(self, checked: bool):
        """切换批量管理模式"""
        self._batch_mode = checked
        self._batch_toolbar.setVisible(checked)
        self._batch_group.setVisible(checked)
        # 非批量模式下隐藏单个编辑区的按钮
        self._save_btn.setVisible(not checked)
        self._delete_btn.setVisible(not checked)
        # 更新按钮文本和样式
        if checked:
            self._batch_btn.setText("退出批量")
            self._batch_btn.setStyleSheet(
                "QPushButton { background-color: #1976d2; color: white; }"
                "QPushButton:hover { background-color: #1565c0; }"
            )
            self._list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
            self._batch_apply_btn.setEnabled(True)
        else:
            self._batch_btn.setText("批量管理")
            self._batch_btn.setStyleSheet("")  # 恢复默认样式
            self._list.clearSelection()

    def _on_selection_changed(self):
        """选择变化时更新批量按钮状态"""
        selected = self._list.selectedItems()
        has_selection = len(selected) > 0 and self._batch_mode
        self._batch_apply_btn.setEnabled(has_selection)
        self._batch_delete_btn.setEnabled(has_selection)
        # 非批量模式下，单选时显示编辑区
        if not self._batch_mode and len(selected) == 1:
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

        self._file_label.setText(f"文件: {entry.file}")
        self._type_edit.setText(entry.type)
        self._level_edit.setText(str(entry.level) if entry.level else "")
        self._update_group_edit()
        self._group_edit.setEditText(entry.group)
        self._notes_edit.setText(entry.notes)
        self._save_btn.setEnabled(True)
        self._delete_btn.setEnabled(True)
        self.entry_selected.emit(filename)

    def _on_save(self):
        item = self._list.currentItem()
        if not item:
            return
        filename = item.data(Qt.ItemDataRole.UserRole)
        level_text = self._level_edit.text().strip()
        level = int(level_text) if level_text.isdigit() else None
        self._db.update_entry(
            filename,
            type=self._type_edit.text().strip(),
            level=level,
            group=self._group_edit.currentText().strip(),
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
        self._file_label.setText("未选择")
        self.data_changed.emit()  # 通知数据变动
        self.refresh()

    def _on_filter_changed(self):
        self.refresh()

    def _on_batch_apply(self):
        """批量应用设置"""
        selected = self._list.selectedItems()
        if not selected:
            return

        # 收集要应用的字段（只应用已填写的）
        updates = {}
        new_type = self._batch_type_edit.text().strip()
        if new_type:
            updates['type'] = new_type

        new_level_text = self._batch_level_edit.text().strip()
        if new_level_text.isdigit():
            updates['level'] = int(new_level_text)

        new_group = self._batch_group_edit.currentText().strip()
        if new_group:
            updates['group'] = new_group

        if not updates:
            return

        # 应用到所有选中项
        for item in selected:
            filename = item.data(Qt.ItemDataRole.UserRole)
            if filename:
                self._db.update_entry(filename, **updates)

        self.data_changed.emit()  # 通知数据变动

        # 保存当前分组过滤器（应用后保持不变）
        current_group_filter = self._group_filter.currentData()

        # 清空批量输入
        self._batch_type_edit.clear()
        self._batch_level_edit.clear()
        self._batch_group_edit.blockSignals(True)
        self._batch_group_edit.setEditText("")
        self._batch_group_edit.blockSignals(False)

        # 刷新列表和分组（保持分组过滤器）
        self.set_known_groups(self._db.get_groups())
        # 确保分组过滤器保持不变
        idx = self._group_filter.findData(current_group_filter)
        if idx >= 0:
            self._group_filter.setCurrentIndex(idx)
        self.refresh()

    def _on_batch_delete(self):
        """批量删除选中的材料"""
        selected = self._list.selectedItems()
        if not selected:
            return

        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除选中的 {len(selected)} 个材料吗？\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for item in selected:
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
