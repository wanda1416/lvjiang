"""网格操作面板 - 设置网格参数、切割、编辑切割结果"""

import numpy as np
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lvjiang.core.reference_db import MetaFieldDef

from ...i18n import tr


class CellEditor(QWidget):
    """单个切割 cell 的编辑组件：缩略图 + 名称 + 分组 + 动态 meta 字段"""

    def __init__(self, index: int, image: np.ndarray,
                 meta_fields: list[MetaFieldDef] | None = None, parent=None):
        super().__init__(parent)
        self._index = index
        self._image = image  # BGR numpy
        self._all_labels: list[str] = []  # 所有名称列表
        self._labels_by_group: dict[str, list[str]] = {}  # 分组 -> 名称列表
        self._meta_edits: dict[str, QLineEdit] = {}  # key -> 输入框

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

        info_layout.addWidget(QLabel(tr("名称:")), 0, 0)
        self.label_edit = QComboBox()
        self.label_edit.setEditable(True)
        self.label_edit.setMinimumWidth(100)
        info_layout.addWidget(self.label_edit, 0, 1)

        self._group_label = QLabel(tr("分组:"))
        info_layout.addWidget(self._group_label, 1, 0)
        self.group_edit = QComboBox()
        self.group_edit.setEditable(True)
        info_layout.addWidget(self.group_edit, 1, 1)

        # 动态 meta 字段（文本），从第 2 行起依次排列
        for offset, field in enumerate(meta_fields or []):
            r = 2 + offset
            info_layout.addWidget(QLabel(f"{field.name}:"), r, 0)
            edit = QLineEdit()
            info_layout.addWidget(edit, r, 1)
            self._meta_edits[field.key] = edit

        # 分组变化时过滤名称下拉
        self.group_edit.currentTextChanged.connect(self._on_group_changed)

        layout.addLayout(info_layout)

    def set_group_edit_visible(self, visible: bool):
        """设置分组编辑区域的可见性"""
        self._group_label.setVisible(visible)
        self.group_edit.setVisible(visible)

    def _on_group_changed(self, group: str):
        """分组变化时更新名称下拉列表"""
        group = group.strip()
        current = self.label_edit.currentText()
        self.label_edit.clear()
        if group and group in self._labels_by_group:
            self.label_edit.addItems(self._labels_by_group[group])
        elif self._all_labels:
            self.label_edit.addItems(self._all_labels)
        # 恢复当前输入
        self.label_edit.setEditText(current)

    @staticmethod
    def _make_thumbnail(bgr: np.ndarray, size: int) -> QImage:
        import cv2
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        scale = min(size / w, size / h)
        new_w, new_h = int(w * scale), int(h * scale)
        resized = cv2.resize(rgb, (new_w, new_h))
        # 必须 copy()，否则 numpy 数据会被垃圾回收
        return QImage(bytes(resized.copy().data), new_w, new_h, new_w * 3, QImage.Format.Format_RGB888)

    @property
    def index(self) -> int:
        return self._index

    @property
    def image(self) -> np.ndarray:
        return self._image

    @property
    def cell_label(self) -> str:
        return self.label_edit.currentText().strip()

    @property
    def cell_meta(self) -> dict:
        """收集动态 meta 字段的非空文本值"""
        result = {}
        for key, edit in self._meta_edits.items():
            val = edit.text().strip()
            if val:
                result[key] = val
        return result

    @property
    def cell_group(self) -> str:
        return self.group_edit.currentText().strip()

    def set_group_suggestions(self, groups: list[str], labels_by_group: dict[str, list[str]] | None = None):
        """设置分组下拉建议和名称数据"""
        if labels_by_group:
            self._labels_by_group = labels_by_group
        # 收集所有名称
        all_labels = set()
        for labels in self._labels_by_group.values():
            all_labels.update(labels)
        self._all_labels = sorted(all_labels)

        current = self.group_edit.currentText()
        self.group_edit.blockSignals(True)
        self.group_edit.clear()
        self.group_edit.addItems(groups)
        self.group_edit.setEditText(current)
        self.group_edit.blockSignals(False)
        # 触发一次名称更新
        self._on_group_changed(current)


class GridPanel(QWidget):
    """网格操作面板 - 三组参数：网格参数 / 单cell尺寸 / 执行切割"""

    # 信号
    grid_params_changed = pyqtSignal(int, int, int)  # rows, cols, gap (实时响应)
    grid_defaults_changed = pyqtSignal(dict)  # 五项网格参数任一手动改动（用于落盘）
    generate_grid_requested = pyqtSignal(int, int, int, int, int)  # rows, cols, gap, height, width
    clear_grid_requested = pyqtSignal()  # 清除网格
    execute_requested = pyqtSignal()  # 执行切割
    submit_cells = pyqtSignal(list)  # list of (image, label, group, meta: dict)

    def __init__(self, rows: int = 3, cols: int = 6, gap: int = 0,
                 height: int = 100, width: int = 100, parent=None):
        super().__init__(parent)
        self._default_rows = rows
        self._default_cols = cols
        self._default_gap = gap
        self._default_height = height
        self._default_width = width
        self._init_ui()
        self._cell_editors: list[CellEditor] = []
        self._known_groups: list[str] = []
        self._labels_by_group: dict[str, list[str]] = {}
        self._meta_fields: list[MetaFieldDef] = []

    def _init_ui(self):
        main_layout = QVBoxLayout(self)

        # ── 第一组：网格参数 ──
        grid_group = QGroupBox(tr("网格参数"))
        grid_layout = QVBoxLayout(grid_group)

        row_layout = QHBoxLayout()
        row_layout.addWidget(QLabel(tr("行:")))
        self._rows_spin = QSpinBox()
        self._rows_spin.setRange(1, 20)
        self._rows_spin.setValue(self._default_rows)
        row_layout.addWidget(self._rows_spin)
        row_layout.addWidget(QLabel(tr("列:")))
        self._cols_spin = QSpinBox()
        self._cols_spin.setRange(1, 20)
        self._cols_spin.setValue(self._default_cols)
        row_layout.addWidget(self._cols_spin)
        row_layout.addWidget(QLabel(tr("间隔:")))
        self._gap_spin = QSpinBox()
        self._gap_spin.setRange(0, 50)
        self._gap_spin.setValue(self._default_gap)
        self._gap_spin.setToolTip(tr("网格线间隔像素，用于过滤黑边"))
        row_layout.addWidget(self._gap_spin)
        row_layout.addStretch()
        grid_layout.addLayout(row_layout)
        main_layout.addWidget(grid_group)

        # ── 第二组：单cell尺寸 + 生成网格 ──
        cell_group = QGroupBox(tr("单cell尺寸"))
        cell_layout = QVBoxLayout(cell_group)

        size_layout = QHBoxLayout()
        size_layout.addWidget(QLabel(tr("高:")))
        self._height_spin = QSpinBox()
        self._height_spin.setRange(10, 500)
        self._height_spin.setValue(self._default_height)
        self._height_spin.setSuffix(" px")
        size_layout.addWidget(self._height_spin)
        size_layout.addWidget(QLabel(tr("宽:")))
        self._width_spin = QSpinBox()
        self._width_spin.setRange(10, 500)
        self._width_spin.setValue(self._default_width)
        self._width_spin.setSuffix(" px")
        size_layout.addWidget(self._width_spin)
        size_layout.addStretch()
        cell_layout.addLayout(size_layout)

        self._generate_btn = QPushButton(tr("生成网格"))
        self._clear_grid_btn = QPushButton(tr("清除网格"))
        gen_layout = QHBoxLayout()
        gen_layout.addWidget(self._generate_btn)
        gen_layout.addWidget(self._clear_grid_btn)
        cell_layout.addLayout(gen_layout)
        main_layout.addWidget(cell_group)

        # ── 第三组：执行切割 ──
        execute_group = QGroupBox(tr("切割"))
        execute_layout = QVBoxLayout(execute_group)
        self._execute_btn = QPushButton(tr("执行切割"))
        self._execute_btn.setEnabled(False)
        execute_layout.addWidget(self._execute_btn)
        main_layout.addWidget(execute_group)

        # ── 切割结果编辑区 ──
        result_group = QGroupBox(tr("切割结果"))
        result_layout = QVBoxLayout(result_group)

        self._result_info = QLabel(tr("尚未切割"))
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
        batch_layout.addWidget(QLabel(tr("批量分组:")))
        self._batch_group = QComboBox()
        self._batch_group.setEditable(True)
        self._batch_group.setMinimumWidth(100)
        batch_layout.addWidget(self._batch_group)
        self._apply_group_btn = QPushButton(tr("应用到全部"))
        batch_layout.addWidget(self._apply_group_btn)
        batch_layout.addStretch()
        result_layout.addLayout(batch_layout)

        self._submit_btn = QPushButton(tr("提交到图库"))
        self._submit_btn.setEnabled(False)
        result_layout.addWidget(self._submit_btn)

        main_layout.addWidget(result_group)

        # ── 信号连接 ──
        # 第一组：实时响应
        self._rows_spin.valueChanged.connect(self._emit_grid_params)
        self._cols_spin.valueChanged.connect(self._emit_grid_params)
        self._gap_spin.valueChanged.connect(self._emit_grid_params)
        # 五项参数改动落盘（rows/cols/gap/height/width）
        for spin in (self._rows_spin, self._cols_spin, self._gap_spin,
                     self._height_spin, self._width_spin):
            spin.valueChanged.connect(self._emit_grid_defaults)
        # 第二组：生成网格
        self._generate_btn.clicked.connect(self._on_generate)
        self._clear_grid_btn.clicked.connect(self.clear_grid_requested.emit)
        # 第三组：执行切割
        self._execute_btn.clicked.connect(self.execute_requested.emit)
        self._submit_btn.clicked.connect(self._on_submit)
        self._apply_group_btn.clicked.connect(self._on_apply_group)

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

    @property
    def cell_height(self) -> int:
        return self._height_spin.value()

    @property
    def cell_width(self) -> int:
        return self._width_spin.value()

    def set_execute_enabled(self, enabled: bool):
        self._execute_btn.setEnabled(enabled)

    def set_known_groups(self, groups: list[str], labels_by_group: dict[str, list[str]] | None = None):
        """更新已知分组列表，同步到所有 cell 编辑器"""
        self._known_groups = list(groups)
        self._labels_by_group = labels_by_group or {}
        self._batch_group.clear()
        self._batch_group.addItems(groups)
        for editor in self._cell_editors:
            editor.set_group_suggestions(groups, self._labels_by_group)

    def set_meta_fields(self, fields: list[MetaFieldDef]):
        """设置当前 meta 字段定义（应用于下一次切割的 cell 编辑器）"""
        self._meta_fields = list(fields)

    # ── 槽函数 ──

    def _emit_grid_params(self):
        """发射网格参数变化信号（实时响应）"""
        self.grid_params_changed.emit(self.rows, self.cols, self.gap)

    def _emit_grid_defaults(self):
        """发射五项网格参数改动信号（由对话框落盘到 session.json）"""
        self.grid_defaults_changed.emit({
            "rows": self.rows,
            "cols": self.cols,
            "gap": self.gap,
            "height": self.cell_height,
            "width": self.cell_width,
        })

    def _on_generate(self):
        """发射生成网格请求信号"""
        self.generate_grid_requested.emit(
            self.rows, self.cols, self.gap,
            self.cell_height, self.cell_width,
        )

    def _on_submit(self):
        self._on_submit_cells()

    def _on_apply_group(self):
        group = self._batch_group.currentText().strip()
        if not group:
            # 如果清空了批量分组，恢复显示单个分组编辑
            for editor in self._cell_editors:
                editor.set_group_edit_visible(True)
            return
        # 应用批量分组，并隐藏单个分组编辑
        for editor in self._cell_editors:
            editor.group_edit.blockSignals(True)
            editor.group_edit.setEditText(group)
            editor.group_edit.blockSignals(False)
            editor.set_group_edit_visible(False)

    # ── 展示切割结果 ──

    def show_cut_cells(self, cells: list[np.ndarray]):
        """展示切割后的 cell 列表，供用户编辑"""
        self._clear_cells()
        for i, cell_img in enumerate(cells):
            editor = CellEditor(i, cell_img, self._meta_fields, self)
            editor.set_group_suggestions(self._known_groups, self._labels_by_group)
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
                editor.cell_label,
                editor.cell_group,
                editor.cell_meta,
            ))
        self.submit_cells.emit(results)
        self._clear_cells()
        self._result_info.setText(f"已提交 {len(results)} 个参考图到图库")
        self._submit_btn.setEnabled(False)

    def _clear_cells(self):
        """清空 cell 编辑器列表"""
        for editor in self._cell_editors:
            self._cells_layout.removeWidget(editor)
            editor.deleteLater()
        self._cell_editors.clear()
