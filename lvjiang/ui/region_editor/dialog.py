"""编辑器对话框 - 区域编辑器对话框 - 布局→场景 层级结构"""

import numpy as np
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QInputDialog, QStatusBar,
    QTextEdit, QApplication, QTabWidget, QMessageBox,
)
from loguru import logger

from ...core.region_config import (
    FIELD_GROUPS, Region, CanvasConfig, Layout, LayoutConfigManager,
    get_scene_name, get_scene_fields,
)
from .scene_tab import SceneTab
from .canvas import EditMode


class RegionEditorDialog(QDialog):
    """区域编辑器对话框 - 布局→场景 层级结构"""

    def __init__(
        self,
        image: np.ndarray,
        refresh_callback=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("区域编辑器")
        self.setMinimumSize(900, 700)

        self._image = image
        self._manager = LayoutConfigManager()
        self._refresh_callback = refresh_callback
        self._tabs: dict[str, SceneTab] = {}  # scene_key -> SceneTab
        self._current_layout: Layout | None = None

        self._setup_ui()
        self._auto_load_active()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ─── 顶部布局栏 ───
        top_bar = QHBoxLayout()

        # 左侧：当前布局 + 下拉框
        top_bar.addWidget(QLabel("当前布局"))
        self._layout_combo = QComboBox()
        self._layout_combo.setMinimumWidth(140)
        self._layout_combo.currentIndexChanged.connect(self._on_combo_changed)
        top_bar.addWidget(self._layout_combo)

        # 中间：功能按钮
        self._btn_activate = QPushButton("激活")
        self._btn_activate.clicked.connect(self._on_activate_layout)
        top_bar.addWidget(self._btn_activate)

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

        # 画布模式切换按钮
        self._btn_canvas_mode = QPushButton("编辑画布")
        self._btn_canvas_mode.setCheckable(True)
        self._btn_canvas_mode.clicked.connect(self._on_toggle_canvas_mode)
        self._btn_canvas_mode.setToolTip("切换画布编辑模式，调整画布范围以排除窗口边框")
        top_bar.addWidget(self._btn_canvas_mode)

        top_bar.addStretch()

        # 右侧：激活布局标签
        self._active_label = QLabel("默认布局：无")
        self._active_label.setStyleSheet("color: #555; font-weight: bold;")
        top_bar.addWidget(self._active_label)

        layout.addLayout(top_bar)

        # ─── 场景 Tab ───
        self._tab_widget = QTabWidget()
        for scene_key, (scene_name, _) in FIELD_GROUPS.items():
            tab = SceneTab(scene_key, self._image)
            self._tabs[scene_key] = tab
            self._tab_widget.addTab(tab, scene_name)
        layout.addWidget(self._tab_widget, stretch=1)

        # ─── 底部：识别 + OCR 结果 + 状态栏 ───
        bottom_row = QHBoxLayout()
        self._btn_recognize = QPushButton("识别全部字段")
        self._btn_recognize.clicked.connect(self._on_recognize)
        bottom_row.addWidget(self._btn_recognize)
        bottom_row.addStretch()
        layout.addLayout(bottom_row)

        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setMinimumHeight(180)
        self._result_text.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13px;"
        )
        self._result_text.setPlaceholderText("点击「识别全部字段」查看 OCR 结果")
        layout.addWidget(self._result_text, stretch=1)

        self._status_bar = QStatusBar()
        self._status_bar.showMessage("请先新建或加载布局")
        layout.addWidget(self._status_bar)

    # ─── 布局栏操作 ──────────────────────────────────────

    def _refresh_combo(self):
        """刷新下拉框，保持当前选中"""
        current = self._layout_combo.currentText()
        self._layout_combo.blockSignals(True)
        self._layout_combo.clear()
        self._layout_combo.addItems(self._manager.list_layouts())
        idx = self._layout_combo.findText(current)
        if idx >= 0:
            self._layout_combo.setCurrentIndex(idx)
        self._layout_combo.blockSignals(False)

    def _update_ui_state(self):
        """统一刷新所有 UI 状态：下拉框、按钮可用性、激活标签"""
        self._refresh_combo()
        active = self._manager.get_active_layout_name()
        # 激活标签
        if active:
            self._active_label.setText(f"默认布局：{active}")
            self._active_label.setStyleSheet("color: #333; font-weight: bold;")
        else:
            self._active_label.setText("默认布局：无")
            self._active_label.setStyleSheet("color: red; font-weight: bold;")
        # 按钮可用性
        has_layout = self._current_layout is not None
        self._btn_save.setEnabled(has_layout)
        self._btn_save_as.setEnabled(has_layout)
        # 已激活的布局禁用激活按钮
        is_active = has_layout and self._current_layout.name == active
        self._btn_activate.setEnabled(has_layout and not is_active)
        # 激活布局不可删除
        self._btn_delete.setEnabled(has_layout and not is_active)

    def _on_combo_changed(self, index: int):
        """下拉框切换时加载对应布局到画布（不激活）"""
        name = self._layout_combo.currentText()
        if not name:
            return
        layout = self._manager.load_layout(name)
        if layout is None:
            return
        self._current_layout = layout
        self._apply_layout_to_tabs()
        self._update_ui_state()
        self._status_bar.showMessage(f"已加载布局「{name}」到画布")

    def _auto_load_active(self):
        """启动时自动加载激活布局"""
        self._refresh_combo()
        name = self._manager.get_active_layout_name()
        if name:
            idx = self._layout_combo.findText(name)
            if idx >= 0:
                self._layout_combo.setCurrentIndex(idx)
            layout = self._manager.load_layout(name)
            if layout:
                self._current_layout = layout
                self._apply_layout_to_tabs()
        self._update_ui_state()

    def _on_activate_layout(self):
        """将当前加载的布局设为激活"""
        if self._current_layout is None:
            self._status_bar.showMessage("没有已加载的布局")
            return
        name = self._current_layout.name
        self._manager.set_active_layout(name)
        self._update_ui_state()
        self._status_bar.showMessage(f"已激活布局「{name}」")

    def _on_new_layout(self):
        """新建空布局并切换到画布（不自动激活）"""
        name, ok = QInputDialog.getText(self, "新建布局", "请输入布局名称：")
        if not ok or not name:
            return
        name = name.strip()
        if not name:
            return
        # 保存当前激活布局，新建后恢复
        prev_active = self._manager.get_active_layout_name()
        layout = self._manager.new_layout(name)
        # new_layout 会自动设为 active，恢复原来的
        if prev_active and prev_active != name:
            self._manager.set_active_layout(prev_active)
        self._current_layout = layout
        self._apply_layout_to_tabs()
        # 下拉框定位到新布局
        self._refresh_combo()
        idx = self._layout_combo.findText(name)
        if idx >= 0:
            self._layout_combo.setCurrentIndex(idx)
        self._update_ui_state()
        self._status_bar.showMessage(f"已新建布局「{name}」")

    def _on_save_layout(self):
        """从所有 Tab 收集 regions + canvas，全量写入当前布局文件"""
        if self._current_layout is None:
            self._status_bar.showMessage("没有已加载的布局")
            return
        name = self._current_layout.name
        # 收集画布配置（从当前 Tab 获取，所有 Tab 共享同一画布）
        current_tab = next(iter(self._tabs.values()))
        self._current_layout.set_canvas(current_tab.get_canvas_config())
        # 收集各场景区域
        for scene_key, tab in self._tabs.items():
            self._current_layout.set_scene_regions(scene_key, tab.get_regions())
        self._manager.save_layout(self._current_layout)
        self._update_ui_state()
        total = sum(len(tab.get_regions()) for tab in self._tabs.values())
        self._status_bar.showMessage(f"已保存布局「{name}」，共 {total} 个区域")
        logger.info(f"布局已保存: {name}, {total} 个区域")

    def _on_save_as_layout(self):
        """另存为：输入新名称，若已存在则提示确认覆盖（保存后加载新布局）"""
        if self._current_layout is None:
            self._status_bar.showMessage("没有已加载的布局")
            return
        temp = Layout(name="")
        # 收集画布配置
        current_tab = next(iter(self._tabs.values()))
        temp.set_canvas(current_tab.get_canvas_config())
        for scene_key, tab in self._tabs.items():
            temp.set_scene_regions(scene_key, tab.get_regions())

        existing = self._manager.list_layouts()
        name, ok = QInputDialog.getText(
            self, "另存为", "请输入布局名称：",
        )
        if not ok or not name:
            return
        name = name.strip()
        if not name:
            return

        if name in existing:
            reply = QMessageBox.question(
                self, "确认覆盖",
                f"布局「{name}」已存在，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        temp.name = name
        self._manager.save_layout(temp)
        self._current_layout = temp
        # 下拉框定位到新布局
        self._refresh_combo()
        idx = self._layout_combo.findText(name)
        if idx >= 0:
            self._layout_combo.setCurrentIndex(idx)
        self._update_ui_state()
        total = sum(len(r) for r in temp.scenes.values())
        self._status_bar.showMessage(f"已另存为布局「{name}」，共 {total} 个区域")
        logger.info(f"布局已另存为: {name}, {total} 个区域")

    def _on_delete_layout(self):
        """删除当前下拉框选中的布局（激活的不可删除），删除后加载默认激活布局"""
        if self._current_layout is None:
            return
        active = self._manager.get_active_layout_name()
        name = self._current_layout.name
        if name == active:
            self._status_bar.showMessage("激活布局不可删除")
            return
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除布局「{name}」吗？此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self._manager.delete_layout(name):
            self._current_layout = None
            self._clear_all_tabs()
            # 加载默认激活布局
            if active:
                layout = self._manager.load_layout(active)
                if layout:
                    self._current_layout = layout
                    self._apply_layout_to_tabs()
            # 先同步下拉框到激活布局，再刷新 UI 状态
            self._layout_combo.blockSignals(True)
            self._refresh_combo()
            idx = self._layout_combo.findText(active) if active else -1
            if idx >= 0:
                self._layout_combo.setCurrentIndex(idx)
            self._layout_combo.blockSignals(False)
            self._update_ui_state()
            self._status_bar.showMessage(f"已删除布局「{name}」，已切换到默认布局")
        else:
            self._status_bar.showMessage(f"删除失败：布局「{name}」不存在")

    # ─── Tab 操作 ────────────────────────────────────────

    def _apply_layout_to_tabs(self):
        """将当前布局的区域数据和画布配置分发到各 Tab"""
        if self._current_layout is None:
            return
        canvas = self._current_layout.get_canvas()
        for scene_key, tab in self._tabs.items():
            regions = self._current_layout.get_scene_regions(scene_key)
            tab.set_regions(regions)
            tab.set_canvas_config(canvas)
            # 连接画布修改回调，同步到其他 Tab
            tab.canvas.on_canvas_changed = self._on_any_canvas_changed

    def _clear_all_tabs(self):
        """清空所有 Tab 的区域"""
        for tab in self._tabs.values():
            tab.set_regions([])

    # ─── 刷新截图 ────────────────────────────────────────

    def _on_refresh_image(self):
        """刷新截图（调用外部回调获取新截图）"""
        if self._refresh_callback is None:
            self._status_bar.showMessage("无截图源，请先在主窗口定位窗口")
            return
        new_image = self._refresh_callback()
        if new_image is not None:
            self._image = new_image
            for tab in self._tabs.values():
                tab.canvas.set_image(new_image)
            self._status_bar.showMessage("截图已刷新")
        else:
            self._status_bar.showMessage("刷新截图失败")

    # ─── 画布模式切换 ───────────────────────────────

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
        # 从当前活跃的 Tab 获取画布配置，同步到其他 Tab
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
        if self._image is None:
            self._status_bar.showMessage("没有截图图片")
            return

        self._status_bar.showMessage("正在识别...")
        QApplication.processEvents()

        from ...core.ocr import OCREngine
        engine = OCREngine()
        h, w = self._image.shape[:2]

        # 画布配置（叠加两层变换）
        canvas = current_tab.get_canvas_config()
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h

        self._result_text.clear()
        results = {}
        for region in regions:
            # 区域坐标（画布相对） -> 截图像素
            x1 = int(canvas_x + region.x_ratio * canvas_w)
            y1 = int(canvas_y + region.y_ratio * canvas_h)
            x2 = int(canvas_x + (region.x_ratio + region.w_ratio) * canvas_w)
            y2 = int(canvas_y + (region.y_ratio + region.h_ratio) * canvas_h)
            crop = self._image[y1:y2, x1:x2]
            ocr_results = engine.recognize(crop)
            text = " | ".join(r.text for r in ocr_results) if ocr_results else "(未识别到)"
            results[region.name] = text
            self._result_text.append(f"{region.name}: {text}")

        self._status_bar.showMessage(
            f"识别完成，共 {len(results)} 个字段"
        )
        logger.info(
            f"OCR 识别完成 (场景={get_scene_name(current_tab.scene_key)}): {results}"
        )

    @property
    def _current_scene_key(self) -> str:
        """当前 Tab 对应的 scene_key"""
        idx = self._tab_widget.currentIndex()
        keys = list(FIELD_GROUPS.keys())
        return keys[idx] if 0 <= idx < len(keys) else keys[0]
