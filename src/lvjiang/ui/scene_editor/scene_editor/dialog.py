"""编辑器对话框 - 场景编辑器主框架、Tab/画布管理"""

from math import gcd

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ....core.layout_manager import (
    LayoutConfigManager,
    load_scene_screenshot,
    migrate_layout_item,
    save_scene_screenshot,
)
from ....core.scene_registry import (
    Layout,
    get_registry,
    get_scene_name,
)
from .layout_ops import LayoutOpsMixin
from .recognition_ops import RecognitionOpsMixin
from .scene_ops import SceneOpsMixin
from .scene_tab import SceneTab
from .script_ops import ScriptOpsMixin, _SceneKeyButton


class SceneEditorDialog(LayoutOpsMixin, SceneOpsMixin, RecognitionOpsMixin, ScriptOpsMixin, QDialog):
    """场景编辑器对话框 - 布局→场景 层级结构"""

    def __init__(
        self,
        layout_manager=None,
        refresh_callback=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("场景管理")
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
        # 截图懒加载：(layout_name, scene_key, view) -> ndarray|None 缓存；
        # _loaded_scenes 记录当前布局下已上屏底图的场景，布局切换时重置
        self._img_cache: dict[tuple[str, str, str], object] = {}
        self._loaded_scenes: set[str] = set()

        self._setup_ui()
        self._auto_load_script()
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

        # ─── 尺寸信息栏（当前 TAB 截图 / 画布尺寸与横纵比）───
        self._info_label = QLabel()
        self._info_label.setTextFormat(Qt.TextFormat.RichText)
        self._info_label.setStyleSheet("font-size: 12px; padding: 2px 2px 4px 2px;")
        self._info_label.setText('<span style="color:#888;">截图：—　　画布：—</span>')
        layout.addWidget(self._info_label)

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
        self._btn_save_script = QPushButton("保存文件")
        self._btn_save_script.clicked.connect(self._on_save_script_file)
        script_btn_row.addWidget(self._btn_save_script)
        # 当前场景 key 按钮
        self._scene_key_btn = _SceneKeyButton(self._get_current_scene_key)
        self._scene_key_btn.clicked.connect(self._scene_key_btn._on_clicked)
        script_btn_row.addWidget(self._scene_key_btn)
        script_btn_row.addStretch()
        script_layout.addLayout(script_btn_row)

        self._script_text = QTextEdit()
        self._script_text.setPlaceholderText("输入 DSL 脚本内容...")
        self._script_text.setAcceptRichText(False)
        self._script_text.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13px;"
        )
        script_layout.addWidget(self._script_text)

        # 设置按钮目标为脚本编辑器
        self._scene_key_btn.set_target(self._script_text)

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
        """将当前布局的区域/坐标/方向/面板数据、画布配置分发到各 Tab

        向量数据（区域/坐标/方向/面板）低廉，仍全量下发；截图（磁盘读 + 解码）
        改为懒加载：仅加载当前可见 Tab，其余等切到时再加载。
        """
        if self._current_layout is None:
            return
        canvas = self._current_layout.get_canvas()
        layout_name = self._current_layout.name
        self._loaded_scenes = set()  # 布局变更，所有底图待重新加载
        for scene_key, tab in self._tabs.items():
            regions = self._current_layout.get_scene_regions(scene_key)
            points = self._current_layout.get_scene_points(scene_key)
            arrows = self._current_layout.get_scene_arrows(scene_key)
            panels = self._current_layout.get_scene_panels(scene_key)
            tab.set_regions(regions)
            tab.set_points(points)
            tab.set_arrows(arrows)
            tab.set_panels(panels)
            tab.set_canvas_config(canvas)
            tab.canvas.on_region_changed = self._on_any_region_changed
            tab.canvas.on_canvas_changed = self._on_any_canvas_changed
            tab.canvas.on_poi_changed = self._on_any_poi_changed
            tab.canvas.on_panel_changed = self._on_any_panel_changed
            tab.canvas.on_status_message = lambda msg: self._status_bar.showMessage(msg, 5000)
            tab.on_view_changed = self._on_tab_view_changed
        self._set_dirty(False)
        self._status_bar.showMessage(f"当前布局: {layout_name}")
        # 只加载当前可见 Tab 的底图，其余在切到时懒加载
        self._ensure_tab_image(self._current_scene_key)
        self._update_info_label()

    def _get_cached_screenshot(self, layout_name: str, scene_key: str, view: str):
        """取截图，命中缓存则直接返回；None（无图）也缓存以免反复读盘"""
        cache_key = (layout_name, scene_key, view)
        if cache_key not in self._img_cache:
            self._img_cache[cache_key] = load_scene_screenshot(layout_name, scene_key, view)
        return self._img_cache[cache_key]

    def _ensure_tab_image(self, scene_key: str):
        """按需为指定场景 Tab 加载底图（懒加载），已加载则跳过"""
        if self._current_layout is None or not scene_key:
            return
        tab = self._tabs.get(scene_key)
        if tab is None or scene_key in self._loaded_scenes:
            return
        img = self._get_cached_screenshot(self._current_layout.name, scene_key, tab.current_view)
        if img is not None:
            tab.canvas.set_image(img)
        else:
            tab.canvas.clear_image()
        self._loaded_scenes.add(scene_key)

    def _on_scene_tab_changed(self, _idx: int = 0):
        """二级场景 Tab 切换：按需加载底图 + 刷新尺寸信息"""
        self._ensure_tab_image(self._current_scene_key)
        self._update_info_label()

    def _clear_all_tabs(self):
        """清空所有 Tab 的区域/坐标/方向/面板"""
        for tab in self._tabs.values():
            tab.set_regions([])
            tab.set_points([])
            tab.set_arrows([])
            tab.set_panels([])

    def _on_tab_view_changed(self, scene_key: str, view: str):
        """某 Tab 切换视图：换上该视图的底图（走缓存）"""
        if self._current_layout is None:
            return
        tab = self._tabs.get(scene_key)
        if tab is None:
            return
        img = self._get_cached_screenshot(self._current_layout.name, scene_key, view)
        if img is not None:
            tab.canvas.set_image(img)
        else:
            tab.canvas.clear_image()
        self._update_info_label()

    # ─── 跨场景迁移 ────────────────────────────────────

    def _on_item_migrated(self, kind: str, key: str, source: str, target: str):
        """编辑弹窗跨场景迁移后的同步（场景 YAML 已由弹窗侧迁移完成）

        1. 磁盘：迁移全部布局 JSON 中的坐标数据
        2. 内存：同步 source/target 两个 Tab 的画布数据，
           否则「保存」的全量覆盖写盘会把迁移结果回滚
        """
        changed = self._manager.migrate_item_across_layouts(source, target, kind, key)
        src_tab = self._tabs.get(source)
        dst_tab = self._tabs.get(target)
        if src_tab is not None and dst_tab is not None:
            temp = Layout(name="")
            for sk, tab in ((source, src_tab), (target, dst_tab)):
                temp.set_scene_regions(sk, tab.get_regions())
                temp.set_scene_points(sk, tab.get_points())
                temp.set_scene_arrows(sk, tab.get_arrows())
                temp.set_scene_panels(sk, tab.get_panels())
            if migrate_layout_item(temp, source, target, kind, key):
                for sk, tab in ((source, src_tab), (target, dst_tab)):
                    tab.set_regions(temp.get_scene_regions(sk))
                    tab.set_points(temp.get_scene_points(sk))
                    tab.set_arrows(temp.get_scene_arrows(sk))
                    tab.set_panels(temp.get_scene_panels(sk))
            if kind == "panel":
                # 网格参数同步到已迁移的画布 Panel（与同场景编辑行为一致）
                scene = get_registry().get_scene(target)
                pdef = next((p for p in scene.panels if p.key == key), None) if scene else None
                if pdef is not None:
                    panels = dst_tab.get_panels()
                    for p in panels:
                        if p.key == key:
                            p.cols, p.rows = pdef.cols, pdef.rows
                            p.min_visible = pdef.min_visible
                    dst_tab.set_panels(panels)
            dst_tab._refresh_lists()
        self._status_bar.showMessage(
            f"已将「{key}」从「{get_scene_name(source)}」迁移到「{get_scene_name(target)}」，"
            f"同步更新 {len(changed)} 个布局"
        )
        logger.info(f"跨场景迁移完成: {kind}「{key}」 {source} -> {target}")

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
            current_tab = self._tabs.get(scene_key)
            view = current_tab.current_view if current_tab else ""
            save_scene_screenshot(layout_name, scene_key, new_image, view)
            self._img_cache[(layout_name, scene_key, view)] = new_image
            self._loaded_scenes.add(scene_key)
            if current_tab:
                current_tab.canvas.set_image(new_image)
            scene_name = get_scene_name(scene_key)
            self._status_bar.showMessage(f"已保存「{scene_name}」场景截图")
            self._update_info_label()
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
        """任一 Tab 的画布被修改时，以当前正在编辑的 Tab 为源，同步到其他所有 Tab

        切换画布编辑模式时所有 Tab 都处于 CANVAS 模式，不能用 edit_mode 判断源 Tab（
        否则永远选中第一个 Tab，将其旧值推回用户实际编辑的 Tab 造成“跳回原位”）。
        鼠标只能作用于当前可见的画布，故当前激活 Tab 即为被编辑的 Tab。
        """
        source_tab = self._current_scene_tab()
        if source_tab is None:
            source_tab = next(iter(self._tabs.values()))
        canvas = source_tab.get_canvas_config()
        for tab in self._tabs.values():
            if tab is not source_tab:
                tab.set_canvas_config(canvas)
        self._set_dirty(True)
        self._update_info_label()

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

    def _on_any_panel_changed(self):
        """任一 Tab 的 panel 被修改时，标记 dirty + 刷新当前 Tab 的面板列表"""
        self._set_dirty(True)
        current = self._current_scene_tab()
        if current and hasattr(current, '_on_panel_changed'):
            current._on_panel_changed()

    def _set_dirty(self, dirty: bool):
        """设置/清除修改状态指示"""
        self._dirty = dirty
        self._dirty_label.setVisible(dirty)

    def reject(self):
        """关闭对话框（X 按钮 / Esc）前检查未保存修改

        QDialog.closeEvent 会调用 reject()，且当 reject 后对话框仍可见时
        会 ignore 关闭事件，因此只需覆盖 reject 即可同时拦截 X 与 Esc。
        """
        if not self._confirm_discard_changes("关闭场景编辑器"):
            return
        super().reject()

    # ─── 尺寸信息栏 ─────────────────────────────

    def _update_info_label(self, *args):
        """刷新尺寸信息栏：当前 TAB 截图尺寸/比例 + 画布相对截图的坐标尺寸/比例"""
        if not hasattr(self, "_info_label"):
            return
        tab = self._current_scene_tab()
        if tab is None or tab.canvas.image_size[0] <= 0:
            self._info_label.setText(
                '<span style="color:#888;">截图：—　　画布：—</span>'
            )
            return
        img_w, img_h = tab.canvas.image_size
        cfg = tab.get_canvas_config()
        canvas_w = max(1, round(cfg.w_ratio * img_w))
        canvas_h = max(1, round(cfg.h_ratio * img_h))

        ss_txt = f"{img_w}×{img_h} ({self._fmt_ratio(img_w, img_h)})"
        cv_txt = f"{canvas_w}×{canvas_h} ({self._fmt_ratio(canvas_w, canvas_h)})"

        self._info_label.setText(
            f'<span style="color:#ccc;">截图</span> '
            f'<b style="color:#4da6ff;">{ss_txt}</b>'
            f'<span style="color:#666;"> │ </span>'
            f'<span style="color:#ccc;">画布</span> '
            f'<b style="color:#ffc850;">{cv_txt}</b>'
        )

    @staticmethod
    def _fmt_ratio(w: int, h: int) -> str:
        """格式化横纵比：最简整数比 + 小数"""
        if w <= 0 or h <= 0:
            return "-"
        g = gcd(w, h)
        return f"{w // g}:{h // g}, {w / h:.2f}"

    # ─── 当前场景辅助 ────────────────────────────────────

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
