"""网格操作面板 - 设置网格参数、切割、编辑切割结果"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QLineEdit,
    QGridLayout,
)

import numpy as np


class CellEditor(QWidget):
    """单个切割 cell 的编辑组件：缩略图 + 类型 + 等级 + 分组"""

    def __init__(self, index: int, image: np.ndarray, parent=None):
        super().__init__(parent)
        self._index = index
        self._image = image  # BGR numpy

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        # 缩略图
        thumb = self._make_thumbnail(image, 48)
        self._thumb_label = QLabel()
        self._thumb_label.setPixmap(QPixmap.fromImage(thumb))
        self._thumb_label.setFixedSize(52, 52)
        layout.addWidget(self._thumb_label)

        # 信息输入区
        info_layout = QGridLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(4)

        info_layout.addWidget(QLabel("名称:"), 0, 0)
        self.type_edit = QLineEdit()
        self.type_edit.setPlaceholderText("如 定音石")
        info_layout.addWidget(self.type_edit, 0, 1)

        info_layout.addWidget(QLabel("等级:"), 1, 0)
        self.level_edit = QLineEdit()
        self.level_edit.setPlaceholderText("如 100")
        info_layout.addWidget(self.level_edit, 1, 1)

        info_layout.addWidget(QLabel("分组:"), 2, 0)
        self.group_edit = QComboBox()
        self.group_edit.setEditable(True)
        info_layout.addWidget(self.group_edit, 2, 1)

        layout.addLayout(info_layout)

    @staticmethod
    def _make_thumbnail(bgr: np.ndarray, size: int) -> QImage:
        import cv2
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        scale = min(size / w, size / h)
        new_w, new_h = int(w * scale), int(h * scale)
        resized = cv2.resize(rgb, (new_w, new_h))
        # 必须 copy()，否则 numpy 数据会被垃圾回收
        return QImage(resized.copy().data, new_w, new_h, new_w * 3, QImage.Format.Format_RGB888)

    @property
    def index(self) -> int:
        return self._index

    @property
    def image(self) -> np.ndarray:
        return self._image

    @property
    def cell_type(self) -> str:
        return self.type_edit.text().strip()

    @property
    def cell_level(self) -> int | None:
        text = self.level_edit.text().strip()
        if text.isdigit():
            return int(text)
        return None

    @property
    def cell_group(self) -> str:
        return self.group_edit.currentText().strip()

    def set_group_suggestions(self, groups: list[str]):
        """设置分组下拉建议"""
        current = self.group_edit.currentText()
        self.group_edit.clear()
        self.group_edit.addItems(groups)
        # 恢复当前输入
        self.group_edit.setEditText(current)


class GridPanel(QWidget):
    """网格操作面板 - 设置切割参数，展示切割结果并编辑"""

    cut_requested = pyqtSignal(int, int)  # rows, cols (for preview)
    gap_changed = pyqtSignal(int)  # gap (for preview)
    execute_requested = pyqtSignal()  # 执行切割
    submit_cells = pyqtSignal(list)  # list of (image, type, level, group)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()
        self._cell_editors: list[CellEditor] = []
        self._known_groups: list[str] = []

    def _init_ui(self):
        main_layout = QVBoxLayout(self)

        # ── 切割参数区 ──
        param_group = QGroupBox("切割参数")
        param_layout = QVBoxLayout(param_group)

        row_layout = QHBoxLayout()
        row_layout.addWidget(QLabel("行数:"))
        self._rows_spin = QSpinBox()
        self._rows_spin.setRange(1, 20)
        self._rows_spin.setValue(5)
        row_layout.addWidget(self._rows_spin)
        row_layout.addWidget(QLabel("列数:"))
        self._cols_spin = QSpinBox()
        self._cols_spin.setRange(1, 20)
        self._cols_spin.setValue(6)
        row_layout.addWidget(self._cols_spin)
        row_layout.addWidget(QLabel("间隔(px):"))
        self._gap_spin = QSpinBox()
        self._gap_spin.setRange(0, 50)
        self._gap_spin.setValue(0)
        self._gap_spin.setToolTip("网格线间隔像素，用于过滤黑边")
        row_layout.addWidget(self._gap_spin)
        row_layout.addStretch()
        param_layout.addLayout(row_layout)

        btn_layout = QHBoxLayout()
        self._preview_btn = QPushButton("预览网格")
        btn_layout.addWidget(self._preview_btn)

        self._execute_btn = QPushButton("执行切割")
        self._execute_btn.setEnabled(False)
        btn_layout.addWidget(self._execute_btn)
        btn_layout.addStretch()
        param_layout.addLayout(btn_layout)

        main_layout.addWidget(param_group)

        # ── 切割结果编辑区 ──
        result_group = QGroupBox("切割结果")
        result_layout = QVBoxLayout(result_group)

        self._result_info = QLabel("尚未切割")
        result_layout.addWidget(self._result_info)

        # 可滚动的 cell 编辑列表
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setMinimumHeight(300)
        self._cells_widget = QWidget()
        self._cells_layout = QVBoxLayout(self._cells_widget)
        self._cells_layout.setContentsMargins(0, 0, 0, 0)
        self._cells_layout.setSpacing(2)
        self._cells_layout.addStretch()
        self._scroll.setWidget(self._cells_widget)
        result_layout.addWidget(self._scroll)

        # 批量设置 + 提交
        batch_layout = QHBoxLayout()
        batch_layout.addWidget(QLabel("批量分组:"))
        self._batch_group = QComboBox()
        self._batch_group.setEditable(True)
        self._batch_group.setMinimumWidth(100)
        batch_layout.addWidget(self._batch_group)
        self._apply_group_btn = QPushButton("应用到全部")
        batch_layout.addWidget(self._apply_group_btn)
        batch_layout.addStretch()
        result_layout.addLayout(batch_layout)

        self._submit_btn = QPushButton("提交到库存")
        self._submit_btn.setEnabled(False)
        result_layout.addWidget(self._submit_btn)

        main_layout.addWidget(result_group)

        # ── 信号 ──
        self._preview_btn.clicked.connect(self._on_preview)
        self._execute_btn.clicked.connect(self.execute_requested.emit)
        self._submit_btn.clicked.connect(self._on_submit)
        self._apply_group_btn.clicked.connect(self._on_apply_group)
        self._gap_spin.valueChanged.connect(self.gap_changed.emit)

    # ── 属性 ──

    @property
    def rows(self) -> int:
        return self._rows_spin.value()

    @property
    def cols(self) -> int:
        return self._cols_spin.value()

    @property
    def gap(self) -> int:
        return self._gap_spin.value()

    def set_execute_enabled(self, enabled: bool):
        self._execute_btn.setEnabled(enabled)

    def set_known_groups(self, groups: list[str]):
        """更新已知分组列表，同步到所有 cell 编辑器"""
        self._known_groups = list(groups)
        self._batch_group.clear()
        self._batch_group.addItems(groups)
        for editor in self._cell_editors:
            editor.set_group_suggestions(groups)

    # ── 槽函数 ──

    def _on_preview(self):
        self.cut_requested.emit(self.rows, self.cols)

    def _on_submit(self):
        self._on_submit_cells()

    def _on_apply_group(self):
        group = self._batch_group.currentText().strip()
        if not group:
            return
        for editor in self._cell_editors:
            if not editor.cell_group:
                editor.group_edit.setEditText(group)

    # ── 展示切割结果 ──

    def show_cut_cells(self, cells: list[np.ndarray]):
        """展示切割后的 cell 列表，供用户编辑"""
        self._clear_cells()
        for i, cell_img in enumerate(cells):
            editor = CellEditor(i, cell_img, self)
            editor.set_group_suggestions(self._known_groups)
            self._cell_editors.append(editor)
            # 插入到 stretch 之前
            self._cells_layout.insertWidget(self._cells_layout.count() - 1, editor)

        self._result_info.setText(f"已切割 {len(cells)} 个单元格，请填写信息后提交")
        self._submit_btn.setEnabled(len(cells) > 0)

    def _on_submit_cells(self):
        """收集所有 cell 信息，发射 submit_cells 信号"""
        results = []
        for editor in self._cell_editors:
            results.append((
                editor.image,
                editor.cell_type,
                editor.cell_level,
                editor.cell_group,
            ))
        self.submit_cells.emit(results)
        self._clear_cells()
        self._result_info.setText(f"已提交 {len(results)} 个材料到库存")
        self._submit_btn.setEnabled(False)

    def _clear_cells(self):
        """清空 cell 编辑器列表"""
        for editor in self._cell_editors:
            self._cells_layout.removeWidget(editor)
            editor.deleteLater()
        self._cell_editors.clear()
