"""编辑器对话框 - 区域编辑器主框架、Tab/画布管理、OCR 识别"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget,
    QPushButton, QComboBox, QStatusBar, QTextEdit,
    QApplication, QTabWidget, QSplitter,
)
from PyQt6.QtCore import Qt
from loguru import logger

from ...core.region_config import (
    FIELD_GROUPS, Layout, LayoutConfigManager,
    get_scene_name,
    load_scene_screenshot, save_scene_screenshot,
)
from .layout_ops import LayoutOpsMixin
from .scene_tab import SceneTab
from .canvas import EditMode


class RegionEditorDialog(LayoutOpsMixin, QDialog):
    """区域编辑器对话框 - 布局→场景 层级结构"""

    def __init__(
        self,
        layout_manager=None,
        refresh_callback=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("区域编辑器")
        self.setMinimumSize(900, 700)
        self.resize(1200, 800)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.setSizeGripEnabled(True)

        self._manager = layout_manager if layout_manager is not None else LayoutConfigManager()
        self._refresh_callback = refresh_callback
        self._tabs: dict[str, SceneTab] = {}
        self._current_layout: Layout | None = None
        self._dirty = False

        self._setup_ui()
        self._auto_load_active()

    # ─── UI 构建 ───────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ─── 顶部布局栏 ───
        top_bar = QHBoxLayout()

        top_bar.addWidget(QLabel("当前布局"))
        self._layout_combo = QComboBox()
        self._layout_combo.setMinimumWidth(140)
        self._layout_combo.currentIndexChanged.connect(self._on_combo_changed)
        top_bar.addWidget(self._layout_combo)

        self._btn_save = QPushButton("保存")
        self._btn_save.clicked.connect(self._on_save_layout)
        top_bar.addWidget(self._btn_save)

        self._btn_new = QPushButton("新建")
        self._btn_new.clicked.connect(self._on_new_layout)
        top_bar.addWidget(self._btn_new)

        self._btn_save_as = QPushButton("另存为")
        self._btn_save_as.clicked.connect(self._on_save_as_layout)
        top_bar.addWidget(self._btn_save_as)

        self._btn_delete = QPushButton("删除")
        self._btn_delete.clicked.connect(self._on_delete_layout)
        top_bar.addWidget(self._btn_delete)

        self._btn_refresh = QPushButton("刷新截图")
        self._btn_refresh.clicked.connect(self._on_refresh_image)
        top_bar.addWidget(self._btn_refresh)

        top_bar.addSpacing(20)

        self._btn_canvas_mode = QPushButton("编辑画布")
        self._btn_canvas_mode.setCheckable(True)
        self._btn_canvas_mode.clicked.connect(self._on_toggle_canvas_mode)
        self._btn_canvas_mode.setToolTip("切换画布编辑模式，调整画布范围以排除窗口边框")
        top_bar.addWidget(self._btn_canvas_mode)

        top_bar.addStretch()

        self._dirty_label = QLabel("● 有改动")
        self._dirty_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
        self._dirty_label.setVisible(False)
        top_bar.addWidget(self._dirty_label)

        layout.addLayout(top_bar)

        # ─── 主分割器：画布 + OCR 结果 ───
        self._splitter = QSplitter(Qt.Orientation.Vertical)

        self._tab_widget = QTabWidget()
        for scene_key, (scene_name, _) in FIELD_GROUPS.items():
            tab = SceneTab(scene_key)
            self._tabs[scene_key] = tab
            self._tab_widget.addTab(tab, scene_name)
        self._splitter.addWidget(self._tab_widget)

        # OCR 结果区
        ocr_panel = QWidget()
        ocr_layout = QVBoxLayout(ocr_panel)
        ocr_layout.setContentsMargins(0, 0, 0, 0)

        btn_row = QHBoxLayout()
        self._btn_recognize = QPushButton("识别全部字段")
        self._btn_recognize.clicked.connect(self._on_recognize)
        btn_row.addWidget(self._btn_recognize)
        btn_row.addStretch()
        ocr_layout.addLayout(btn_row)

        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setMinimumHeight(60)
        self._result_text.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13px;"
        )
        self._result_text.setPlaceholderText("点击「识别全部字段」查看 OCR 结果")
        ocr_layout.addWidget(self._result_text)

        self._splitter.addWidget(ocr_panel)
        self._splitter.setStretchFactor(0, 2)
        self._splitter.setStretchFactor(1, 1)

        layout.addWidget(self._splitter, stretch=1)

        # ─── 状态栏 ───
        self._status_bar = QStatusBar()
        self._status_bar.showMessage("请先新建或加载布局")
        layout.addWidget(self._status_bar)

    # ─── Tab 操作 ────────────────────────────────────────

    def _apply_layout_to_tabs(self):
        """将当前布局的区域数据、画布配置、截图分发到各 Tab"""
        if self._current_layout is None:
            return
        canvas = self._current_layout.get_canvas()
        layout_name = self._current_layout.name
        for scene_key, tab in self._tabs.items():
            regions = self._current_layout.get_scene_regions(scene_key)
            tab.set_regions(regions)
            tab.set_canvas_config(canvas)
            screenshot = load_scene_screenshot(layout_name, scene_key)
            if screenshot is not None:
                tab.canvas.set_image(screenshot)
            else:
                tab.canvas.clear_image()
            tab.canvas.on_region_changed = self._on_any_region_changed
            tab.canvas.on_canvas_changed = self._on_any_canvas_changed
        self._set_dirty(False)

    def _clear_all_tabs(self):
        """清空所有 Tab 的区域"""
        for tab in self._tabs.values():
            tab.set_regions([])

    # ─── 刷新截图 ────────────────────────────────────────

    def _on_refresh_image(self):
        """刷新当前场景的截图（调用外部回调获取新截图，保存到磁盘）"""
        if self._refresh_callback is None:
            self._status_bar.showMessage("无截图源，请先在主窗口定位窗口")
            return
        if self._current_layout is None:
            self._status_bar.showMessage("没有已加载的布局")
            return
        result = self._refresh_callback()
        new_image, error_msg = result if isinstance(result, tuple) else (result, None)
        if new_image is not None:
            scene_key = self._current_scene_key
            layout_name = self._current_layout.name
            save_scene_screenshot(layout_name, scene_key, new_image)
            current_tab = self._tabs.get(scene_key)
            if current_tab:
                current_tab.canvas.set_image(new_image)
            scene_name = get_scene_name(scene_key)
            self._status_bar.showMessage(f"已保存「{scene_name}」场景截图")
        else:
            self._status_bar.showMessage(error_msg or "刷新截图失败")

    # ─── 画布模式切换 ───────────────────────────────────

    def _on_toggle_canvas_mode(self, checked: bool):
        """切换画布编辑模式，同步到所有 Tab"""
        for tab in self._tabs.values():
            if checked:
                tab.set_canvas_mode()
            else:
                tab.set_region_mode()
        self._btn_canvas_mode.setText("退出画布编辑" if checked else "编辑画布")
        if checked:
            self._status_bar.showMessage("画布编辑模式：拖拽/缩放黄色画布框以排除窗口边框")
        else:
            self._status_bar.showMessage("已退出画布编辑模式")

    def _on_any_canvas_changed(self):
        """任一 Tab 的画布被修改时，同步到其他所有 Tab"""
        source_tab = None
        for tab in self._tabs.values():
            if tab.edit_mode == EditMode.CANVAS:
                source_tab = tab
                break
        if source_tab is None:
            source_tab = next(iter(self._tabs.values()))
        canvas = source_tab.get_canvas_config()
        for key, tab in self._tabs.items():
            if tab is not source_tab:
                tab.set_canvas_config(canvas)
        self._set_dirty(True)

    def _on_any_region_changed(self):
        """任一 Tab 的区域被修改时，标记 dirty + 刷新当前 Tab 的字段列表"""
        self._set_dirty(True)
        current = self._tab_widget.currentWidget()
        if hasattr(current, '_refresh_field_list'):
            current._refresh_field_list()

    def _set_dirty(self, dirty: bool):
        """设置/清除修改状态指示"""
        self._dirty = dirty
        self._dirty_label.setVisible(dirty)

    # ─── OCR 识别 ────────────────────────────────────────

    def _on_recognize(self):
        """对当前 Tab 场景的所有已定义区域逐个裁剪识别（叠加画布变换）"""
        current_tab = self._tabs.get(self._current_scene_key)
        if current_tab is None:
            return
        regions = current_tab.get_regions()
        if not regions:
            self._status_bar.showMessage("没有已定义的区域")
            return
        if current_tab.canvas.pixmap is None:
            self._status_bar.showMessage("当前场景无截图，请先刷新截图")
            return
        image = current_tab.canvas.get_image()
        if image is None:
            self._status_bar.showMessage("当前场景无截图")
            return

        self._status_bar.showMessage("正在识别...")
        QApplication.processEvents()

        from ...core.ocr import OCREngine
        engine = OCREngine()
        h, w = image.shape[:2]

        canvas = current_tab.get_canvas_config()
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h

        self._result_text.clear()
        results = {}
        for region in regions:
            x1 = int(canvas_x + region.x_ratio * canvas_w)
            y1 = int(canvas_y + region.y_ratio * canvas_h)
            x2 = int(canvas_x + (region.x_ratio + region.w_ratio) * canvas_w)
            y2 = int(canvas_y + (region.y_ratio + region.h_ratio) * canvas_h)
            crop = image[y1:y2, x1:x2]
            ocr_results = engine.recognize(crop)
            text = " | ".join(r.text for r in ocr_results) if ocr_results else "(未识别到)"
            results[region.name] = text
            self._result_text.append(f"{region.name}: {text}")

        self._status_bar.showMessage(f"识别完成，共 {len(results)} 个字段")
        logger.info(
            f"OCR 识别完成 (场景={get_scene_name(current_tab.scene_key)}): {results}"
        )

    @property
    def _current_scene_key(self) -> str:
        """当前 Tab 对应的 scene_key"""
        idx = self._tab_widget.currentIndex()
        keys = list(FIELD_GROUPS.keys())
        return keys[idx] if 0 <= idx < len(keys) else keys[0]
