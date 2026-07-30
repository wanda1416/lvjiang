"""参考图管理对话框 - 新增参考图与参考图管理两个独立 Tab"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
    QHBoxLayout,
)

import numpy as np

from lvjiang.core.reference_db import ReferenceDatabase
from .canvas import ReferenceCanvas
from .grid_panel import GridPanel
from .browser_panel import BrowserPanel
from .meta_schema_panel import MetaSchemaPanel


class ReferenceManagerDialog(QDialog):
    """参考图管理对话框

    三个顶级 Tab，完全独立的工作流：
      - 新增参考图：导入截图 → 网格切割 → 编辑 cell 信息 → 提交到图库
      - 参考图管理：按分组浏览、搜索、编辑已有参考图
      - 元数据定义：定义名称/分组之外的 meta 字段
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("参考图管理")
        self.resize(1200, 800)
        # 启用最小化/最大化按钮
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        # 加载用户配置
        from lvjiang.config import load_user_config
        self._config = load_user_config()

        self._db = ReferenceDatabase()
        self._db.load()
        self._source_name = ""
        self._data_changed = False  # 跟踪是否有数据变动
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)

        # ── 顶级 Tab ──
        self._tabs = QTabWidget()

        # Tab 1: 新增参考图（完整编辑工作流）
        self._add_tab = self._build_add_tab()
        self._tabs.addTab(self._add_tab, "新增参考图")

        # Tab 2: 参考图管理（浏览与编辑已有参考图）
        self._browser = BrowserPanel(self._db)
        self._browser.set_known_groups(self._db.get_groups())
        self._browser.refresh()
        self._browser.data_changed.connect(self._on_data_changed)
        self._tabs.addTab(self._browser, "参考图管理")

        # Tab 3: 元数据定义（定义名称/分组之外的 meta 字段）
        self._meta_panel = MetaSchemaPanel(self._db)
        self._meta_panel.schema_changed.connect(self._on_schema_changed)
        self._tabs.addTab(self._meta_panel, "元数据定义")

        main_layout.addWidget(self._tabs)

    # ── Tab 1: 新增参考图 ──

    def _build_add_tab(self) -> QWidget:
        """构建新增参考图 Tab 的完整 UI"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 顶部工具栏
        toolbar = QToolBar()
        self._import_btn = QPushButton("导入图片")
        self._import_btn.clicked.connect(self._on_import)
        toolbar.addWidget(self._import_btn)

        self._paste_btn = QPushButton("粘贴 (Ctrl+V)")
        self._paste_btn.clicked.connect(self._on_paste)
        toolbar.addWidget(self._paste_btn)

        self._clear_btn = QPushButton("清空画布")
        self._clear_btn.clicked.connect(self._on_clear)
        toolbar.addWidget(self._clear_btn)

        toolbar.addSeparator()

        self._select_all_btn = QPushButton("全选")
        self._select_all_btn.setEnabled(False)
        toolbar.addWidget(self._select_all_btn)

        self._deselect_all_btn = QPushButton("全不选")
        self._deselect_all_btn.setEnabled(False)
        toolbar.addWidget(self._deselect_all_btn)

        toolbar.addSeparator()
        self._source_label = QLabel("  未导入图片")
        toolbar.addWidget(self._source_label)

        layout.addWidget(toolbar)

        # 主体：画布 + 网格面板
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 画布容器（包含信息栏 + 画布）
        canvas_container = QWidget()
        canvas_layout = QVBoxLayout(canvas_container)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(2)

        # 信息栏：显示区域规格和 cell 规格
        self._info_label = QLabel("未框选区域")
        self._info_label.setStyleSheet("color: #888; font-size: 11px;")
        self._info_label.setMaximumHeight(20)
        canvas_layout.addWidget(self._info_label)

        self._canvas = ReferenceCanvas()
        self._canvas.region_changed.connect(self._on_region_changed)
        self._canvas.selection_changed.connect(self._on_selection_changed)
        canvas_layout.addWidget(self._canvas)

        splitter.addWidget(canvas_container)

        # 连接全选/全不选按钮（在 _canvas 创建后）
        self._select_all_btn.clicked.connect(self._canvas.select_all_cells)
        self._deselect_all_btn.clicked.connect(self._canvas.deselect_all_cells)

        self._grid_panel = GridPanel(
            rows=self._config.material_grid.rows,
            cols=self._config.material_grid.cols,
            gap=self._config.material_grid.gap,
            height=self._config.material_grid.height,
            width=self._config.material_grid.width,
        )
        # 初始化已知分组列表（供批量分组下拉使用）
        self._grid_panel.set_known_groups(self._db.get_groups(), self._db.get_all_labels_by_group())
        # 同步当前 meta 字段定义（供 cell 编辑器生成输入）
        self._grid_panel.set_meta_fields(self._db.get_meta_schema())
        # 第一组：网格参数实时响应
        self._grid_panel.grid_params_changed.connect(self._on_grid_params_changed)
        # 五项网格参数手动改动 → 落盘 session.json（下次打开作为默认值）
        self._grid_panel.grid_defaults_changed.connect(self._on_grid_defaults_changed)
        # 第二组：生成网格
        self._grid_panel.generate_grid_requested.connect(self._on_generate_grid)
        self._grid_panel.clear_grid_requested.connect(self._on_clear_grid)
        # 第三组：执行切割
        self._grid_panel.execute_requested.connect(self._on_execute_cut)
        self._grid_panel.submit_cells.connect(self._on_submit_cells)
        splitter.addWidget(self._grid_panel)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        layout.addWidget(splitter)
        return widget

    # ── 工具栏槽函数 ──

    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择参考图片", "",
            "图片文件 (*.png *.jpg *.bmp *.webp)"
        )
        if not path:
            return

        import cv2
        from PIL import Image
        try:
            # PIL 读中文路径 → numpy → cv2 BGR
            img_rgb = np.array(Image.open(path))
            img = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法读取图片:\n{path}\n{e}")
            return

        self._source_name = path.split("/")[-1].split("\\")[-1]
        self._canvas.set_image(img)
        self._update_source_label()

    def _on_paste(self):
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        image = clipboard.image()
        if image.isNull():
            QMessageBox.information(self, "提示", "剪贴板中没有图片")
            return

        # QImage → numpy BGR
        qimg = image.convertToFormat(QImage.Format.Format_RGBA8888)
        w, h = qimg.width(), qimg.height()
        ptr = qimg.bits()
        ptr.setsize(w * h * 4)
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4))
        import cv2
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)

        self._source_name = "clipboard"
        self._canvas.set_image(bgr)
        self._update_source_label()

    def _on_clear(self):
        self._canvas.clear_image()
        self._source_name = ""
        self._update_source_label()

    def _update_source_label(self):
        if self._source_name:
            self._source_label.setText(f"  来源: {self._source_name}")
        else:
            self._source_label.setText("  未导入图片")

    # ── 画布事件 ──

    def _on_region_changed(self, x1, y1, x2, y2):
        """区域变化时启用/禁用切割按钮，更新信息栏"""
        has_region = (x2 - x1) > 0.01 and (y2 - y1) > 0.01
        self._grid_panel.set_execute_enabled(has_region)
        if has_region:
            self._canvas.set_grid_size(self._grid_panel.rows, self._grid_panel.cols)
            self._canvas.set_show_grid(True)
            # 启用全选/全不选按钮
            self._select_all_btn.setEnabled(True)
        else:
            self._select_all_btn.setEnabled(False)
            self._deselect_all_btn.setEnabled(False)
        self._update_info_label()

    def _on_grid_params_changed(self, rows: int, cols: int, gap: int):
        """网格参数变化时实时更新画布网格和信息栏"""
        self._canvas.set_grid_size(rows, cols)
        self._canvas.set_grid_gap(gap)
        self._update_info_label()

    def _on_grid_defaults_changed(self, grid: dict):
        """五项网格参数手动改动后写入 session.json 的 material_grid 节点"""
        from lvjiang.config import save_material_grid
        save_material_grid(grid)

    def _on_generate_grid(self, rows: int, cols: int, gap: int, height: int, width: int):
        """根据单cell尺寸生成网格区域"""
        img_size = self._canvas.image_size
        if img_size is None:
            QMessageBox.warning(self, "提示", "请先导入图片")
            return

        img_w, img_h = img_size
        # 计算总尺寸
        total_w = cols * width + (cols - 1) * gap
        total_h = rows * height + (rows - 1) * gap

        # 检查是否超出图片范围
        if total_w > img_w or total_h > img_h:
            QMessageBox.warning(
                self, "参数超出图片大小",
                f"生成的网格尺寸 {total_w}×{total_h} 超出图片尺寸 {img_w}×{img_h}"
            )
            return

        # 从左上角 (0, 0) 开始生成
        self._canvas.set_region_from_pixels(0, 0, total_w, total_h)
        self._update_info_label()

    def _on_clear_grid(self):
        """清除网格区域"""
        self._canvas.set_grid_rect(None)
        self._canvas.set_show_grid(False)
        self._grid_panel.set_execute_enabled(False)
        self._select_all_btn.setEnabled(False)
        self._deselect_all_btn.setEnabled(False)
        self._update_info_label()

    def _on_selection_changed(self):
        """画布单元格选择变化时更新按钮状态"""
        has_selection = self._canvas.has_selection()
        self._deselect_all_btn.setEnabled(has_selection)

    def _update_info_label(self):
        """更新信息栏显示"""
        region_px = self._canvas.get_region_pixels()
        if region_px is None:
            self._info_label.setText("未框选区域")
            return

        x1, y1, x2, y2 = region_px
        region_w = x2 - x1
        region_h = y2 - y1

        rows = self._grid_panel.rows
        cols = self._grid_panel.cols
        gap = self._grid_panel.gap

        # 计算 cell 尺寸
        total_gap_w = gap * (cols - 1)
        total_gap_h = gap * (rows - 1)
        cell_w = (region_w - total_gap_w) // cols if cols > 0 else 0
        cell_h = (region_h - total_gap_h) // rows if rows > 0 else 0

        self._info_label.setText(
            f"区域: {region_w}×{region_h} px  |  "
            f"Cell: {cell_w}×{cell_h} px  ({rows}行×{cols}列，间隔{gap})"
        )

    def _on_execute_cut(self):
        """执行切割：从画布获取已选 cell 图像，传递给编辑面板"""
        # 检查是否有选中的单元格
        if not self._canvas.has_selection():
            QMessageBox.warning(self, "提示", "请至少选择一个参考图")
            return

        rows = self._grid_panel.rows
        cols = self._grid_panel.cols
        gap = self._grid_panel.gap

        # 先保存已选中的单元格（set_grid_size 会清空选择）
        selected = self._canvas.get_selected_cells()
        self._canvas.set_grid_size(rows, cols)

        cells_coords = self._canvas.get_grid_cells(gap=gap)
        if not cells_coords:
            QMessageBox.warning(self, "提示", "请先框选区域")
            return

        image = self._canvas.get_image()
        if image is None:
            QMessageBox.warning(self, "提示", "请先导入图片")
            return

        # 只裁剪已选中的 cell
        cell_images = []
        for idx, (x1, y1, x2, y2) in enumerate(cells_coords):
            row = idx // cols
            col = idx % cols
            if (row, col) in selected:
                cell = image[y1:y2, x1:x2].copy()
                cell_images.append(cell)

        # 传递给编辑面板
        self._grid_panel.show_cut_cells(cell_images)

    def _on_submit_cells(self, cells_data: list[tuple]):
        """提交切割结果到数据库

        Args:
            cells_data: list of (image, label, group, meta)
        """
        count = 0
        for image, label, group, cell_meta in cells_data:
            if image is None:
                continue
            meta = dict(cell_meta)
            if group:
                meta["group"] = group
            self._db.add_entry(
                label=label,
                meta=meta,
                source=self._source_name,
                image_data=image,
            )
            count += 1

        if count > 0:
            self._data_changed = True  # 标记数据已变动
            # 刷新分组列表（新增参考图面板 + 参考图管理面板）
            groups = self._db.get_groups()
            labels_by_group = self._db.get_all_labels_by_group()
            self._grid_panel.set_known_groups(groups, labels_by_group)
            self._browser.set_known_groups(groups)
            # 刷新图库面板
            self._browser.refresh()
            # 切换到参考图管理 tab 查看结果
            self._tabs.setCurrentIndex(1)

    def _on_data_changed(self):
        """参考图管理面板数据变动时标记"""
        self._data_changed = True

    def _on_schema_changed(self):
        """元数据定义变更时，重建动态字段并刷新"""
        self._browser.rebuild_meta_fields()
        self._browser.refresh()
        self._grid_panel.set_meta_fields(self._db.get_meta_schema())

    @property
    def data_changed(self) -> bool:
        """返回是否有数据变动（新增/修改/删除参考图）"""
        return self._data_changed

    # ── 键盘事件 ──

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_V and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # 只在新增参考图 tab 时响应粘贴
            if self._tabs.currentIndex() == 0:
                self._on_paste()
        else:
            super().keyPressEvent(event)
