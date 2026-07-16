"""编辑器对话框 - 区域编辑器主框架、Tab/画布管理、OCR 识别"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget,
    QPushButton, QComboBox, QStatusBar, QTextEdit,
    QApplication, QTabWidget, QSplitter, QMenu, QInputDialog, QMessageBox,
    QFileDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject
from loguru import logger

from ...core.region_config import (
    SCENE_REGIONS, Layout, LayoutConfigManager,
    get_scene_name, get_registry, reload_scene_registry,
    load_scene_screenshot, save_scene_screenshot,
    get_group_name, get_scene_group,
)
from ...constants import APP_CONFIG_PATH, SYSTEM_WORKFLOWS_DIR
from .layout_ops import LayoutOpsMixin
from .scene_tab import SceneTab
from .canvas import EditMode


class _ScriptOutputBridge(QObject):
    """信号桥：将脚本测试输出安全转发到主线程 UI"""
    append_output = pyqtSignal(str)


class _SceneKeyButton(QPushButton):
    """场景 key 按钮：点击后在脚本编辑器光标处插入当前场景 key"""

    def __init__(self, get_scene_key, parent=None):
        super().__init__("输入当前场景", parent)
        self._get_scene_key = get_scene_key
        self._target: QTextEdit | None = None
        self.setToolTip("点击在脚本编辑器光标处插入当前场景 key")

    def set_target(self, target: QTextEdit):
        self._target = target

    def _on_clicked(self):
        key = self._get_scene_key()
        if key and self._target is not None:
            cursor = self._target.textCursor()
            cursor.insertText(key)
            self._target.setTextCursor(cursor)
            self._target.setFocus()


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
        self._tabs: dict[str, SceneTab] = {}          # scene_key -> SceneTab
        self._group_tabs: dict[str, QTabWidget] = {}  # group_key -> QTabWidget
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

        self._btn_new_group = QPushButton("创建分组")
        self._btn_new_group.setToolTip("新建场景分组")
        self._btn_new_group.clicked.connect(self._on_new_group)
        top_bar.addWidget(self._btn_new_group)

        top_bar.addSpacing(20)

        self._btn_new_scene = QPushButton("创建场景")
        self._btn_new_scene.setToolTip("在当前分组下新建场景")
        self._btn_new_scene.clicked.connect(self._on_new_scene)
        top_bar.addWidget(self._btn_new_scene)

        top_bar.addStretch()

        self._dirty_label = QLabel("● 有改动")
        self._dirty_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
        self._dirty_label.setVisible(False)
        top_bar.addWidget(self._dirty_label)

        layout.addLayout(top_bar)

        # ─── 主分割器：分组 Tab + OCR 结果区 ───
        self._splitter = QSplitter(Qt.Orientation.Vertical)

        # 一级 Tab：分组 Tab
        self._group_tab_widget = QTabWidget()
        self._group_tab_widget.setMovable(True)
        self._group_tab_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._group_tab_widget.customContextMenuRequested.connect(self._on_group_tab_context_menu)
        self._group_tab_widget.tabBar().tabMoved.connect(self._on_group_tab_moved)
        self._group_tab_widget.currentChanged.connect(self._on_group_tab_changed)
        self._splitter.addWidget(self._group_tab_widget)
        self._rebuild_group_tabs()

        # 底部面板：左侧 OCR 结果区 + 右侧脚本测试器
        bottom_splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 左侧：OCR 结果区 ──
        ocr_panel = QWidget()
        ocr_layout = QVBoxLayout(ocr_panel)
        ocr_layout.setContentsMargins(0, 0, 0, 0)

        btn_row = QHBoxLayout()
        self._btn_recognize = QPushButton("识别全部字段")
        self._btn_recognize.clicked.connect(self._on_recognize)
        btn_row.addWidget(self._btn_recognize)
        self._btn_recognize_mat = QPushButton("识别全部材料")
        self._btn_recognize_mat.clicked.connect(self._on_recognize_materials)
        btn_row.addWidget(self._btn_recognize_mat)
        btn_row.addStretch()
        ocr_layout.addLayout(btn_row)

        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setMinimumHeight(60)
        self._result_text.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13px;"
        )
        self._result_text.setPlaceholderText("点击「识别全部字段」查看 OCR 结果，点击「识别全部材料」查看材料识别结果")
        ocr_layout.addWidget(self._result_text)

        bottom_splitter.addWidget(ocr_panel)

        # ── 右侧：脚本测试器 ──
        script_panel = QWidget()
        script_layout = QVBoxLayout(script_panel)
        script_layout.setContentsMargins(0, 0, 0, 0)

        script_btn_row = QHBoxLayout()
        self._btn_run_script = QPushButton("运行脚本")
        self._btn_run_script.clicked.connect(self._on_script_test)
        script_btn_row.addWidget(self._btn_run_script)
        self._btn_load_script = QPushButton("加载文件")
        self._btn_load_script.clicked.connect(self._on_load_script_file)
        script_btn_row.addWidget(self._btn_load_script)
        # 当前场景 key 按钮
        self._scene_key_btn = _SceneKeyButton(self._get_current_scene_key)
        self._scene_key_btn.clicked.connect(self._scene_key_btn._on_clicked)
        script_btn_row.addWidget(self._scene_key_btn)
        script_btn_row.addStretch()
        script_layout.addLayout(script_btn_row)

        self._script_text = QTextEdit()
        self._script_text.setPlaceholderText("输入 DSL 脚本内容...")
        self._script_text.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13px;"
        )
        script_layout.addWidget(self._script_text)

        # 设置按钮目标为脚本编辑器
        self._scene_key_btn.set_target(self._script_text)

        self._script_output = QTextEdit()
        self._script_output.setReadOnly(True)
        self._script_output.setMinimumHeight(60)
        self._script_output.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 12px; background-color: #1e1e1e; color: #d4d4d4;"
        )
        self._script_output.setPlaceholderText("脚本运行输出...")
        script_layout.addWidget(self._script_output)

        bottom_splitter.addWidget(script_panel)
        bottom_splitter.setSizes([500, 500])

        self._splitter.addWidget(bottom_splitter)
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
        current = self._current_scene_tab()
        if current and hasattr(current, '_refresh_region_list'):
            current._refresh_region_list()

    def _on_any_poi_changed(self):
        """任一 Tab 的 point/arrow 被修改时，标记 dirty + 刷新当前 Tab 的坐标/方向列表"""
        self._set_dirty(True)
        current = self._current_scene_tab()
        if current and hasattr(current, '_on_poi_changed'):
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

    def _on_recognize_materials(self):
        """对当前 Tab 场景中所有 type==slot 的区域做材料识别"""
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

        # 筛选 slot 类型区域
        from ...core.region_config import get_region_defs
        slot_keys = {r.key for r in get_region_defs(current_tab.scene_key) if r.type == "slot"}
        slot_regions = [r for r in regions if r.key in slot_keys]
        if not slot_regions:
            self._status_bar.showMessage("当前场景没有 slot 类型的区域")
            return

        self._status_bar.showMessage("正在识别材料...")
        QApplication.processEvents()

        from ...core.material_recognizer import MaterialRecognizer
        from ...core.ocr import OCREngine
        ocr_engine = OCREngine()
        recognizer = MaterialRecognizer(ocr_engine)
        canvas = current_tab.get_canvas_config()
        h, w = image.shape[:2]
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h

        # 展示结果
        self._result_text.clear()
        for region in slot_regions:
            x1 = int(canvas_x + region.x_ratio * canvas_w)
            y1 = int(canvas_y + region.y_ratio * canvas_h)
            x2 = int(canvas_x + (region.x_ratio + region.w_ratio) * canvas_w)
            y2 = int(canvas_y + (region.y_ratio + region.h_ratio) * canvas_h)
            crop = image[y1:y2, x1:x2]
            info = recognizer.recognize(crop)

            if not info.type:
                line = f"{region.name}: (空槽)"
            else:
                parts = [info.type]
                if info.level is not None:
                    parts.append(f"{info.level}级")
                if info.count is not None:
                    count_str = f"×{info.count}"
                    if info.owned is not None:
                        count_str += f"/{info.owned}"
                    parts.append(count_str)
                parts.append(f"[{info.confidence:.0%}]")
                line = f"{region.name}: {' '.join(parts)}"
            self._result_text.append(line)

        self._status_bar.showMessage(f"材料识别完成，共 {len(slot_regions)} 个槽位")
        logger.info(
            f"材料识别完成 (场景={get_scene_name(current_tab.scene_key)}, "
            f"槽位数={len(slot_regions)})"
        )

    # ─── 脚本测试 ─────────────────────────────────────────

    def _on_load_script_file(self):
        """加载 .wf 文件到脚本编辑器"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择脚本文件",
            str(SYSTEM_WORKFLOWS_DIR),
            "工作流文件 (*.wf);;所有文件 (*)",
        )
        if not path:
            return
        from pathlib import Path
        content = Path(path).read_text(encoding="utf-8")
        self._script_text.setPlainText(content)
        logger.info(f"已加载脚本: {path}")

    def _on_script_test(self):
        """执行脚本测试器中的 DSL 脚本（同步执行，结果输出到 script_output）"""
        script = self._script_text.toPlainText().strip()
        if not script:
            self._script_output_append("[错误] 脚本内容为空")
            return

        # 检查是否有父窗口（主窗口）提供运行环境
        main_win = self.parent()
        if main_win is None or not hasattr(main_win, '_target_window') or main_win._target_window is None:
            self._script_output_append("[错误] 请先在主窗口定位游戏窗口")
            return

        self._script_output_clear()
        self._script_output_append("[运行] 执行脚本测试...")
        self._status_bar.showMessage("脚本测试运行中...")
        QApplication.processEvents()

        try:
            # 构建 BaseWorkflow
            from ...workflows.base import BaseWorkflow
            layout_name = self._current_layout.name if self._current_layout else ""
            if not layout_name:
                self._script_output_append("[错误] 没有已加载的布局")
                return

            layout = main_win._layout_manager.load_layout(layout_name)
            if not layout:
                self._script_output_append(f"[错误] 无法加载布局: {layout_name}")
                return

            wf = BaseWorkflow(
                capture=main_win._capture,
                ocr=main_win._ocr,
                input_ctrl=main_win._input,
                layout=layout,
                delay_config=main_win._user_config.delay,
                window_left=main_win._target_window["left"],
                window_top=main_win._target_window["top"],
                stop_check=lambda: False,  # 暂不支持主动停止
            )

            # 写入临时 .wf 文件
            temp_wf = SYSTEM_WORKFLOWS_DIR / "_temp_test.wf"
            temp_wf.write_text(script, encoding="utf-8")

            # 同步执行
            result = wf.run_file(temp_wf)

            # 输出结果
            import json
            from ..run_control import _to_serializable
            serializable = _to_serializable(result)
            self._script_output_append(
                f"[完成] 输出:\n{json.dumps(serializable, ensure_ascii=False, indent=2)}"
            )
            self._status_bar.showMessage("脚本测试完成")

        except Exception as e:
            import traceback
            self._script_output_append(f"[错误] {e}")
            self._script_output_append(traceback.format_exc())
            self._status_bar.showMessage("脚本测试失败")
            logger.error(f"脚本测试异常: {e}")

    def _script_output_append(self, text: str):
        """线程安全地追加脚本输出"""
        if not hasattr(self, '_script_bridge'):
            self._script_bridge = _ScriptOutputBridge()
            self._script_bridge.append_output.connect(self._script_output.append)
        self._script_bridge.append_output.emit(text)

    def _script_output_clear(self):
        """清空脚本输出（主线程调用）"""
        self._script_output.clear()

    @property
    def _current_scene_key(self) -> str:
        """当前 Tab 对应的 scene_key"""
        tab = self._current_scene_tab()
        if tab:
            return tab.scene_key
        keys = list(self._tabs.keys())
        return keys[0] if keys else ""

    def _current_scene_tab(self) -> SceneTab | None:
        """获取当前激活的 SceneTab"""
        group_widget = self._group_tab_widget.currentWidget()
        if isinstance(group_widget, QTabWidget):
            scene_widget = group_widget.currentWidget()
            if isinstance(scene_widget, SceneTab):
                return scene_widget
        return None

    def _current_group_key(self) -> str:
        """获取当前激活的分组 key"""
        idx = self._group_tab_widget.currentIndex()
        groups = get_registry().get_groups()
        if 0 <= idx < len(groups):
            return groups[idx][0]
        return groups[0][0] if groups else ""

    # ─── Tab 重建 ────────────────────────────────────────

    def _rebuild_group_tabs(self):
        """重建分组 Tab（一级 Tab）"""
        self._group_tab_widget.blockSignals(True)
        self._group_tab_widget.clear()
        self._group_tabs.clear()
        self._tabs.clear()
        registry = get_registry()
        for group_key, group_name in registry.get_groups():
            scene_tab_widget = QTabWidget()
            scene_tab_widget.setMovable(True)
            scene_tab_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            scene_tab_widget.customContextMenuRequested.connect(
                lambda pos, gk=group_key: self._on_scene_tab_context_menu(pos, gk)
            )
            scene_tab_widget.tabBar().tabMoved.connect(
                lambda from_idx, to_idx, gk=group_key: self._on_scene_tab_moved(from_idx, to_idx, gk)
            )
            self._group_tabs[group_key] = scene_tab_widget
            self._group_tab_widget.addTab(scene_tab_widget, group_name)
            # 构建该分组下的场景 Tab
            self._rebuild_scene_tabs(group_key)
        self._group_tab_widget.blockSignals(False)

    def _get_current_scene_key(self) -> str:
        """获取当前激活的场景 key"""
        idx = self._group_tab_widget.currentIndex()
        groups = get_registry().get_groups()
        if not (0 <= idx < len(groups)):
            return ""
        group_key = groups[idx][0]
        scene_tab_widget = self._group_tabs.get(group_key)
        if scene_tab_widget is None:
            return ""
        scene_idx = scene_tab_widget.currentIndex()
        widget = scene_tab_widget.widget(scene_idx) if scene_idx >= 0 else None
        return widget.scene_key if isinstance(widget, SceneTab) else ""

    def _rebuild_scene_tabs(self, group_key: str):
        """重建指定分组下的场景 Tab（二级 Tab）"""
        scene_tab_widget = self._group_tabs.get(group_key)
        if scene_tab_widget is None:
            return
        scene_tab_widget.blockSignals(True)
        scene_tab_widget.clear()
        registry = get_registry()
        for scene_key in registry.get_group_scenes(group_key):
            scene_name = get_scene_name(scene_key)
            tab = SceneTab(scene_key)
            self._tabs[scene_key] = tab
            scene_tab_widget.addTab(tab, scene_name)
        scene_tab_widget.blockSignals(False)

    # ─── 场景 CRUD ────────────────────────────────────────

    def _on_new_scene(self):
        """新建场景：弹窗输入 key 和 name，创建到当前分组"""
        from PyQt6.QtWidgets import QDialog, QFormLayout, QLineEdit, QDialogButtonBox
        current_group = self._current_group_key()
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
            registry.create_scene(key, name, group_key=current_group)
        except ValueError as e:
            QMessageBox.warning(self, "创建失败", str(e))
            return
        registry.save_group_config(APP_CONFIG_PATH)
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        self._status_bar.showMessage(f"已创建场景: {name}")

    # ─── 分组 Tab 右键菜单 ────────────────────────────────

    def _on_group_tab_context_menu(self, pos):
        """分组 Tab 右键菜单：重命名 / 删除"""
        tab_index = self._group_tab_widget.tabBar().tabAt(pos)
        if tab_index < 0:
            return
        groups = get_registry().get_groups()
        if tab_index >= len(groups):
            return
        group_key, group_name = groups[tab_index]
        menu = QMenu(self)
        rename_action = menu.addAction("重命名分组")
        delete_action = menu.addAction("删除分组")
        # 非空分组不允许删除
        if get_registry().get_group_scenes(group_key):
            delete_action.setEnabled(False)
            delete_action.setToolTip("分组非空，无法删除")
        action = menu.exec(self._group_tab_widget.mapToGlobal(pos))
        if action == rename_action:
            self._do_rename_group(group_key)
        elif action == delete_action:
            self._do_delete_group(group_key)

    def _on_new_group(self):
        """创建分组：弹窗输入 key 和 name"""
        from PyQt6.QtWidgets import QDialog, QFormLayout, QLineEdit, QDialogButtonBox
        dialog = QDialog(self)
        dialog.setWindowTitle("新建分组")
        form = QFormLayout(dialog)
        key_edit = QLineEdit()
        key_edit.setPlaceholderText("英文，如 my_group")
        form.addRow("分组 Key:", key_edit)
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("中文名称")
        form.addRow("分组名称:", name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
        # 实时校验
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
            registry.create_group(key, name)
        except ValueError as e:
            QMessageBox.warning(self, "创建失败", str(e))
            return
        registry.save_group_config(APP_CONFIG_PATH)
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        self._status_bar.showMessage(f"已创建分组: {name}")

    def _do_rename_group(self, group_key: str):
        """重命名分组（key 不可变）"""
        from PyQt6.QtWidgets import QDialog, QFormLayout, QLineEdit, QDialogButtonBox
        old_name = get_group_name(group_key)
        dialog = QDialog(self)
        dialog.setWindowTitle("重命名分组")
        form = QFormLayout(dialog)
        key_label = QLineEdit(group_key)
        key_label.setReadOnly(True)
        form.addRow("分组 Key:", key_label)
        name_edit = QLineEdit(old_name)
        form.addRow("分组名称:", name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
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
            registry.rename_group(group_key, new_name)
        except ValueError as e:
            QMessageBox.warning(self, "重命名失败", str(e))
            return
        registry.save_group_config(APP_CONFIG_PATH)
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        self._status_bar.showMessage(f"已重命名分组: {new_name}")

    def _do_delete_group(self, group_key: str):
        """删除空分组"""
        group_name = get_group_name(group_key)
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除分组「{group_name}」({group_key}) 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        registry = get_registry()
        try:
            registry.delete_group(group_key)
        except ValueError as e:
            QMessageBox.warning(self, "删除失败", str(e))
            return
        registry.save_group_config(APP_CONFIG_PATH)
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        self._status_bar.showMessage(f"已删除分组: {group_name}")

    def _on_group_tab_moved(self, from_index: int, to_index: int):
        """分组 Tab 拖拽排序后保存新顺序"""
        new_order = []
        for i in range(self._group_tab_widget.count()):
            widget = self._group_tab_widget.widget(i)
            # 查找对应的 group_key
            for gk, tw in self._group_tabs.items():
                if tw is widget:
                    new_order.append(gk)
                    break
        registry = get_registry()
        registry.reorder_groups(new_order)
        registry.save_group_config(APP_CONFIG_PATH)
        reload_scene_registry()
        logger.info(f"分组顺序已更新: {new_order}")

    def _on_group_tab_changed(self, index: int):
        """分组 Tab 切换时，应用布局数据"""
        self._apply_layout_to_tabs()

    # ─── 场景 Tab 右键菜单 ────────────────────────────────

    def _on_scene_tab_context_menu(self, pos, group_key: str):
        """场景 Tab 右键菜单：重命名 / 删除 / 更改分组"""
        scene_tab_widget = self._group_tabs.get(group_key)
        if scene_tab_widget is None:
            return
        tab_index = scene_tab_widget.tabBar().tabAt(pos)
        if tab_index < 0:
            return
        scene_keys = get_registry().get_group_scenes(group_key)
        if tab_index >= len(scene_keys):
            return
        scene_key = scene_keys[tab_index]
        menu = QMenu(self)
        rename_action = menu.addAction("重命名")
        delete_action = menu.addAction("删除")
        # 更改分组子菜单
        move_menu = menu.addMenu("更改分组")
        registry = get_registry()
        current_group = registry.get_scene_group(scene_key)
        for gk, gn in registry.get_groups():
            if gk != current_group:
                move_action = move_menu.addAction(gn)
                move_action.setData(gk)
        action = menu.exec(scene_tab_widget.mapToGlobal(pos))
        if action == rename_action:
            self._do_rename_scene(scene_key)
        elif action == delete_action:
            self._do_delete_scene(scene_key)
        elif action and action.data() is not None:
            target_group = action.data()
            if target_group:
                self._do_move_scene_group(scene_key, target_group)

    def _do_move_scene_group(self, scene_key: str, target_group: str):
        """移动场景到其他分组"""
        registry = get_registry()
        try:
            registry.move_scene_to_group(scene_key, target_group)
        except ValueError as e:
            QMessageBox.warning(self, "移动失败", str(e))
            return
        registry.save_group_config(APP_CONFIG_PATH)
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        target_name = get_group_name(target_group)
        scene_name = get_scene_name(scene_key)
        self._status_bar.showMessage(f"已移动场景「{scene_name}」到分组「{target_name}」")

    def _do_rename_scene(self, scene_key: str):
        """重命名场景（只允许修改名称，key 不可变）"""
        from PyQt6.QtWidgets import QDialog, QFormLayout, QLineEdit, QDialogButtonBox
        old_name = get_scene_name(scene_key)
        dialog = QDialog(self)
        dialog.setWindowTitle("重命名场景")
        form = QFormLayout(dialog)
        key_label = QLineEdit(scene_key)
        key_label.setReadOnly(True)
        form.addRow("场景 Key:", key_label)
        name_edit = QLineEdit(old_name)
        form.addRow("场景名称:", name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
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
        registry.save_group_config(APP_CONFIG_PATH)
        reload_scene_registry()
        self._rebuild_group_tabs()
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
        registry.save_group_config(APP_CONFIG_PATH)
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        self._status_bar.showMessage(f"已删除场景: {scene_name}")

    def _on_scene_tab_moved(self, from_index: int, to_index: int, group_key: str):
        """场景 Tab 拖拽排序后保存新顺序"""
        scene_tab_widget = self._group_tabs.get(group_key)
        if scene_tab_widget is None:
            return
        new_order = []
        for i in range(scene_tab_widget.count()):
            widget = scene_tab_widget.widget(i)
            if isinstance(widget, SceneTab):
                new_order.append(widget.scene_key)
        # 合并所有分组的顺序
        registry = get_registry()
        all_order = []
        for gk, _ in registry.get_groups():
            if gk == group_key:
                all_order.extend(new_order)
            else:
                all_order.extend(registry.get_group_scenes(gk))
        registry.save_scene_order(all_order, APP_CONFIG_PATH)
        reload_scene_registry()
        logger.info(f"分组 {group_key} 场景顺序已更新: {new_order}")
