"""编辑器对话框 - 区域编辑器主框架、Tab/画布管理、OCR 识别"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget,
    QPushButton, QComboBox, QStatusBar, QTextEdit,
    QApplication, QTabWidget, QSplitter, QMenu, QInputDialog, QMessageBox,
)
from PyQt6.QtCore import Qt
from loguru import logger

from ...core.region_config import (
    SCENE_REGIONS, Layout, LayoutConfigManager,
    Point, Arrow,
    get_scene_name, get_registry, reload_scene_registry,
    load_scene_screenshot, save_scene_screenshot,
)
from ...constants import APP_CONFIG_PATH
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
        self.setWindowTitle("页面管理")
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

        top_bar.addSpacing(20)

        self._btn_new_scene = QPushButton("创建场景")
        self._btn_new_scene.setToolTip("新建场景")
        self._btn_new_scene.clicked.connect(self._on_new_scene)
        top_bar.addWidget(self._btn_new_scene)

        top_bar.addStretch()

        self._dirty_label = QLabel("● 有改动")
        self._dirty_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
        self._dirty_label.setVisible(False)
        top_bar.addWidget(self._dirty_label)

        layout.addLayout(top_bar)

        # ─── 主分割器：画布 + OCR 结果 ───
        self._splitter = QSplitter(Qt.Orientation.Vertical)

        self._tab_widget = QTabWidget()
        self._tab_widget.setMovable(True)
        self._tab_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tab_widget.customContextMenuRequested.connect(self._on_tab_context_menu)
        self._tab_widget.tabBar().tabMoved.connect(self._on_tab_moved)
        self._splitter.addWidget(self._tab_widget)
        self._rebuild_tabs()

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
        """将当前布局的区域/坐标/方向数据、画布配置、截图分发到各 Tab"""
        if self._current_layout is None:
            return
        canvas = self._current_layout.get_canvas()
        layout_name = self._current_layout.name
        for scene_key, tab in self._tabs.items():
            regions = self._current_layout.get_scene_regions(scene_key)
            points = self._current_layout.get_scene_points(scene_key)
            arrows = self._current_layout.get_scene_arrows(scene_key)
            tab.set_regions(regions)
            tab.set_points(points)
            tab.set_arrows(arrows)
            tab.set_canvas_config(canvas)
            screenshot = load_scene_screenshot(layout_name, scene_key)
            if screenshot is not None:
                tab.canvas.set_image(screenshot)
            else:
                tab.canvas.clear_image()
            tab.canvas.on_region_changed = self._on_any_region_changed
            tab.canvas.on_canvas_changed = self._on_any_canvas_changed
            tab.canvas.on_poi_changed = self._on_any_poi_changed
        self._set_dirty(False)
        self._status_bar.showMessage(f"当前布局: {layout_name}")

    def _clear_all_tabs(self):
        """清空所有 Tab 的区域/坐标/方向"""
        for tab in self._tabs.values():
            tab.set_regions([])
            tab.set_points([])
            tab.set_arrows([])

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
        if hasattr(current, '_refresh_region_list'):
            current._refresh_region_list()

    def _on_any_poi_changed(self):
        """任一 Tab 的 point/arrow 被修改时，标记 dirty + 刷新当前 Tab 的坐标/方向列表"""
        self._set_dirty(True)
        current = self._tab_widget.currentWidget()
        if hasattr(current, '_on_poi_changed'):
            current._on_poi_changed()

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
        canvas = current_tab.get_canvas_config()

        results = engine.ocr_scene_regions(image, canvas, regions, current_tab.scene_key)

        # 展示结果
        self._result_text.clear()
        for key, text in results.items():
            # 查找字段中文名
            name = key
            for r in regions:
                if r.key == key:
                    name = r.name
                    break
            self._result_text.append(f"{name}: {text or '(未识别到)'}")

        self._status_bar.showMessage(f"识别完成，共 {len(results)} 个字段")
        logger.info(
            f"OCR 识别完成 (场景={get_scene_name(current_tab.scene_key)}): {results}"
        )

    @property
    def _current_scene_key(self) -> str:
        """当前 Tab 对应的 scene_key"""
        current = self._tab_widget.currentWidget()
        if isinstance(current, SceneTab):
            return current.scene_key
        keys = list(self._tabs.keys())
        return keys[0] if keys else ""

    # ─── Tab 重建 ────────────────────────────────────────

    def _rebuild_tabs(self):
        """从 SCENE_REGIONS 重建所有 Tab"""
        self._tab_widget.blockSignals(True)
        self._tab_widget.clear()
        self._tabs.clear()
        for scene_key, (scene_name, _) in SCENE_REGIONS.items():
            tab = SceneTab(scene_key)
            self._tabs[scene_key] = tab
            self._tab_widget.addTab(tab, scene_name)
        self._tab_widget.blockSignals(False)

    # ─── 场景 CRUD ────────────────────────────────────────

    def _on_new_scene(self):
        """新建场景：弹窗输入 key 和 name"""
        from PyQt6.QtWidgets import QDialog, QFormLayout, QLineEdit, QDialogButtonBox
        dialog = QDialog(self)
        dialog.setWindowTitle("新建场景")
        form = QFormLayout(dialog)
        key_edit = QLineEdit()
        key_edit.setPlaceholderText("英文，如 my_scene")
        form.addRow("场景 Key:", key_edit)
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("中文名称")
        form.addRow("场景名称:", name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)

        # 实时校验：非空 + key 格式
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        def _validate():
            k = key_edit.text().strip()
            n = name_edit.text().strip()
            ok_btn.setEnabled(bool(k and n and k.replace("_", "").isalnum()))
        key_edit.textChanged.connect(_validate)
        name_edit.textChanged.connect(_validate)
        _validate()

        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key = key_edit.text().strip()
        name = name_edit.text().strip()
        registry = get_registry()
        try:
            registry.create_scene(key, name)
        except ValueError as e:
            QMessageBox.warning(self, "创建失败", str(e))
            return
        reload_scene_registry()
        self._rebuild_tabs()
        self._apply_layout_to_tabs()
        self._status_bar.showMessage(f"已创建场景: {name}")

    def _on_tab_context_menu(self, pos):
        """Tab 右键菜单：重命名 / 删除"""
        tab_index = self._tab_widget.tabBar().tabAt(pos)
        if tab_index < 0:
            return
        scene_key = list(self._tabs.keys())[tab_index] if tab_index < len(self._tabs) else None
        if not scene_key:
            return
        menu = QMenu(self)
        rename_action = menu.addAction("重命名")
        delete_action = menu.addAction("删除")
        action = menu.exec(self._tab_widget.mapToGlobal(pos))
        if action == rename_action:
            self._do_rename_scene(scene_key)
        elif action == delete_action:
            self._do_delete_scene(scene_key)

    def _do_rename_scene(self, scene_key: str):
        """重命名场景（只允许修改名称，key 不可变）"""
        from PyQt6.QtWidgets import QDialog, QFormLayout, QLineEdit, QDialogButtonBox
        old_name = get_scene_name(scene_key)
        dialog = QDialog(self)
        dialog.setWindowTitle("重命名场景")
        form = QFormLayout(dialog)
        # 显示 key（只读）
        key_label = QLineEdit(scene_key)
        key_label.setReadOnly(True)
        form.addRow("场景 Key:", key_label)
        # 名称（可编辑）
        name_edit = QLineEdit(old_name)
        form.addRow("场景名称:", name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
        # 实时校验
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        def _validate():
            ok_btn.setEnabled(bool(name_edit.text().strip()))
        name_edit.textChanged.connect(_validate)
        _validate()
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_name = name_edit.text().strip()
        registry = get_registry()
        try:
            registry.rename_scene(scene_key, scene_key, new_name)
        except ValueError as e:
            QMessageBox.warning(self, "重命名失败", str(e))
            return
        registry.save_scene_order(registry.all_scene_keys(), APP_CONFIG_PATH)
        reload_scene_registry()
        self._rebuild_tabs()
        self._apply_layout_to_tabs()
        self._status_bar.showMessage(f"已重命名场景: {new_name}")

    def _do_delete_scene(self, scene_key: str):
        """删除场景（二次确认）"""
        scene_name = get_scene_name(scene_key)
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除场景「{scene_name}」({scene_key}) 吗？\n"
            f"这将删除场景定义文件，但不会影响布局数据。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        registry = get_registry()
        try:
            registry.delete_scene(scene_key)
        except ValueError as e:
            QMessageBox.warning(self, "删除失败", str(e))
            return
        registry.save_scene_order(registry.all_scene_keys(), APP_CONFIG_PATH)
        reload_scene_registry()
        self._rebuild_tabs()
        self._apply_layout_to_tabs()
        self._status_bar.showMessage(f"已删除场景: {scene_name}")

    def _on_tab_moved(self, from_index: int, to_index: int):
        """Tab 拖拽排序后保存新顺序到 app.yaml"""
        # 收集当前 Tab 顺序对应的 scene_key
        new_order = []
        for i in range(self._tab_widget.count()):
            widget = self._tab_widget.widget(i)
            if isinstance(widget, SceneTab):
                new_order.append(widget.scene_key)
        registry = get_registry()
        registry.save_scene_order(new_order, APP_CONFIG_PATH)
        reload_scene_registry()
        # 重建 _tabs 字典的顺序（虽然 dict 保持插入顺序，但这里不需要重建）
        logger.info(f"场景顺序已更新: {new_order}")
