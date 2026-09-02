"""编辑器对话框 - 场景编辑器主框架、Tab/画布管理"""

from math import gcd

from loguru import logger
from PyQt6.QtCore import Qt, QTimer
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

from ...core.layout_manager import (
    LayoutConfigManager,
    load_scene_screenshot,
    migrate_layout_item,
    save_scene_screenshot,
)
from ...core.layout_models import Layout
from ...core.scene_registry import (
    get_registry,
    get_scene_name,
    get_subscene_ref_defs,
    is_subscene,
)
from ...i18n import tr
from ..button_styles import apply_button_style
from ..dialog_guards import EscapeCloseConfirmationMixin
from ..reference.combo_sizing import set_combo_minimum_character_capacity
from ..theme import get_theme_manager
from .layout_ops import LayoutOpsMixin
from .recognition_ops import RecognitionOpsMixin
from .scene_ops import SceneOpsMixin
from .scene_tab import SceneTab
from .script_ops import ScriptOpsMixin, _SceneKeyButton

_REFERENCE_GROUP_COMBO_CHARACTER_CAPACITY = 8


class _LazyReferenceGroupCombo(QComboBox):
    """参考图库较大，只有用户展开筛选框时才读取分组。"""

    def __init__(self, loader):
        super().__init__()
        self._loader = loader
        self._loaded = False

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._loader()

    def showPopup(self) -> None:  # type: ignore[override]
        self.ensure_loaded()
        super().showPopup()


class SceneEditorDialog(
    EscapeCloseConfirmationMixin,
    LayoutOpsMixin,
    SceneOpsMixin,
    RecognitionOpsMixin,
    ScriptOpsMixin,
    QDialog,
):
    """场景编辑器对话框 - 布局→场景 层级结构"""

    def __init__(
        self,
        layout_manager=None,
        refresh_callback=None,
        parent=None,
    ):
        super().__init__(parent)
        # Windows 会在复杂控件树构造期间提前绘制尚未完成布局的原生窗口。
        # 首帧准备好之前禁止重绘，避免用户看到小窗口反复闪烁。
        self.setUpdatesEnabled(False)
        self._initial_show_pending = True
        self.setWindowTitle(tr("场景管理"))
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
        self._dirty_scenes: set[str] = set()  # 当前布局中已变更的场景 key 集合
        self._data_dirty_scenes: set[str] = set()  # 真正改过布局数据的场景
        # 截图懒加载：(layout_name, scene_key, view) -> ndarray|None 缓存；
        # _loaded_scenes 记录当前布局下已上屏底图的场景，布局切换时重置
        self._img_cache: dict[tuple[str, str, str], object] = {}
        self._loaded_scenes: set[str] = set()
        self._scene_layout_paths: dict[str, str] = {}
        self._applying_layout = False

        self._setup_ui()
        get_theme_manager().theme_changed.connect(self._update_info_label)
        self._auto_load_script()
        self._auto_load_active()
        self._restore_window_size()

    def showEvent(self, event):  # type: ignore[override]
        super().showEvent(event)
        if self._initial_show_pending:
            self._initial_show_pending = False
            QTimer.singleShot(0, self._enable_initial_updates)

    def _enable_initial_updates(self) -> None:
        self.setUpdatesEnabled(True)
        self.update()

    def _restore_window_size(self):
        """从 session.json 恢复窗口位置 + 大小 + 分割器尺寸"""
        from ...core.config import load_ui_page_state
        se = load_ui_page_state("scene_editor")
        if not isinstance(se, dict):
            logger.debug("场景编辑器恢复：scene_editor 子节点非 dict")
            return
        logger.debug(f"场景编辑器恢复：scene_editor = {se}")
        # 窗口位置
        pos = se.get("pos")
        if isinstance(pos, list) and len(pos) == 2:
            logger.debug(f"场景编辑器恢复位置：{pos}")
            self.move(int(pos[0]), int(pos[1]))
        # 窗口大小
        size = se.get("size")
        if isinstance(size, list) and len(size) == 2:
            logger.debug(f"场景编辑器恢复大小：{size}")
            self.resize(int(size[0]), int(size[1]))
        # 垂直分割器（上 Tab + 下面板）
        vs = se.get("vsplit")
        if isinstance(vs, list) and len(vs) == 2 and all(s > 0 for s in vs):
            logger.debug(f"场景编辑器恢复垂直分割器：{vs}")
            self._splitter.setSizes([int(s) for s in vs])
        # 水平分割器（左 OCR + 右脚本）
        hs = se.get("hsplit")
        if isinstance(hs, list) and len(hs) == 2 and all(s > 0 for s in hs):
            logger.debug(f"场景编辑器恢复水平分割器：{hs}")
            self._bottom_splitter.setSizes([int(s) for s in hs])
        # Tab 内部分割器（画布 vs 右侧列表）—— 延迟到 Tab 创建后应用
        self._pending_tab_split = se.get("tab_split")
        logger.debug(f"场景编辑器恢复 Tab 分割器（延迟）：{self._pending_tab_split}")

    def _save_window_size(self):
        """保存窗口位置 + 大小 + 分割器尺寸到 session.json（写入 ui_state.scene_editor）"""
        se = {
            "pos": [self.x(), self.y()],
            "size": [self.width(), self.height()],
            "vsplit": self._splitter.sizes(),
            "hsplit": self._bottom_splitter.sizes(),
        }
        # Tab 内部分割器（取第一个可用 Tab）
        for tab in self._tabs.values():
            se["tab_split"] = tab._splitter.sizes()
            break
        logger.debug(f"场景编辑器保存：scene_editor = {se}")
        try:
            from ...core.config import update_ui_page_state
            update_ui_page_state("scene_editor", se)
        except Exception as e:
            logger.warning(f"保存场景编辑器窗口大小失败: {e}")

    def closeEvent(self, event):
        self._save_window_size()
        super().closeEvent(event)

    # ─── UI 构建 ───────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ─── 顶部布局栏 ───
        top_bar = QHBoxLayout()

        top_bar.addWidget(QLabel(tr("当前布局")))
        self._layout_combo = QComboBox()
        self._layout_combo.setMinimumWidth(140)
        self._layout_combo.currentIndexChanged.connect(self._on_combo_changed)
        top_bar.addWidget(self._layout_combo)

        self._btn_save = QPushButton(tr("保存"))
        self._btn_save.clicked.connect(self._on_save_layout)
        top_bar.addWidget(self._btn_save)

        self._btn_new = QPushButton(tr("新建"))
        self._btn_new.clicked.connect(self._on_new_layout)
        top_bar.addWidget(self._btn_new)

        self._btn_save_as = QPushButton(tr("另存为"))
        self._btn_save_as.clicked.connect(self._on_save_as_layout)
        top_bar.addWidget(self._btn_save_as)

        self._btn_delete = QPushButton(tr("删除"))
        self._btn_delete.clicked.connect(self._on_delete_layout)
        top_bar.addWidget(self._btn_delete)

        self._btn_refresh = QPushButton(tr("刷新截图"))
        self._btn_refresh.clicked.connect(self._on_refresh_image)
        top_bar.addWidget(self._btn_refresh)

        top_bar.addSpacing(20)

        self._btn_canvas_mode = QPushButton(tr("编辑画布"))
        self._btn_canvas_mode.setCheckable(True)
        self._btn_canvas_mode.clicked.connect(self._on_toggle_canvas_mode)
        self._btn_canvas_mode.setToolTip(tr("切换画布编辑模式，调整画布范围以排除窗口边框"))
        top_bar.addWidget(self._btn_canvas_mode)

        top_bar.addSpacing(20)

        self._btn_new_group = QPushButton(tr("创建分组"))
        self._btn_new_group.setToolTip(tr("新建场景分组"))
        self._btn_new_group.clicked.connect(self._on_new_group)
        top_bar.addWidget(self._btn_new_group)

        top_bar.addSpacing(20)

        self._btn_new_scene = QPushButton(tr("创建场景"))
        self._btn_new_scene.setToolTip(tr("在当前分组下新建场景"))
        self._btn_new_scene.clicked.connect(self._on_new_scene)
        top_bar.addWidget(self._btn_new_scene)

        apply_button_style(
            self._btn_save,
            self._btn_new,
            self._btn_save_as,
            self._btn_new_group,
            self._btn_new_scene,
        )
        apply_button_style(
            self._btn_refresh,
            self._btn_canvas_mode,
            variant="neutral",
        )
        apply_button_style(self._btn_delete, variant="danger")

        top_bar.addStretch()

        self._dirty_label = QLabel(tr("● 有改动"))
        self._dirty_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
        self._dirty_label.setVisible(False)
        top_bar.addWidget(self._dirty_label)

        layout.addLayout(top_bar)

        # ─── 第二行：继承标识 + 尺寸信息栏 ───
        second_line = QHBoxLayout()

        # 继承标识（别名布局时显示）
        self._inherit_label = QLabel()
        self._inherit_label.setStyleSheet("color: palette(mid); font-size: 11px;")
        self._inherit_label.hide()
        second_line.addWidget(self._inherit_label)

        # 尺寸信息栏（当前 TAB 截图 / 画布尺寸与横纵比）
        self._info_label = QLabel()
        self._info_label.setTextFormat(Qt.TextFormat.RichText)
        self._info_label.setStyleSheet("font-size: 12px; padding: 2px 2px 4px 2px;")
        self._set_empty_info_label()
        second_line.addWidget(self._info_label)
        second_line.addStretch()

        layout.addLayout(second_line)

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
        self._bottom_splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 左侧：OCR 结果区 ──
        ocr_panel = QWidget()
        ocr_layout = QVBoxLayout(ocr_panel)
        ocr_layout.setContentsMargins(0, 0, 0, 0)

        btn_row = QHBoxLayout()
        self._btn_recognize = QPushButton(tr("识别全部字段"))
        self._btn_recognize.clicked.connect(self._on_recognize)
        btn_row.addWidget(self._btn_recognize)
        self._btn_recognize_ref = QPushButton(tr("识别全部参考图"))
        self._btn_recognize_ref.clicked.connect(self._on_recognize_references)
        btn_row.addWidget(self._btn_recognize_ref)
        apply_button_style(self._btn_recognize, self._btn_recognize_ref)
        # 参考图分组筛选下拉
        self._combo_ref_group = _LazyReferenceGroupCombo(
            self._refresh_ref_group_combo)
        self._combo_ref_group.addItem(tr("全部"), None)
        self._combo_ref_group.setToolTip(tr("限定参考图识别的分组范围"))
        set_combo_minimum_character_capacity(
            self._combo_ref_group,
            _REFERENCE_GROUP_COMBO_CHARACTER_CAPACITY,
        )
        btn_row.addWidget(self._combo_ref_group)
        from PyQt6.QtWidgets import QCheckBox
        self._chk_live_image = QCheckBox(tr("使用实时图像"))
        self._chk_live_image.setToolTip(tr("勾选后直接从设备实时截屏进行识别，不保存到场景文件"))
        btn_row.addWidget(self._chk_live_image)
        btn_row.addStretch()
        ocr_layout.addLayout(btn_row)

        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setMinimumHeight(60)
        self._result_text.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13px;"
        )
        self._result_text.setPlaceholderText(tr("点击「识别全部字段」查看 OCR 结果，点击「识别全部参考图」查看匹配结果"))
        ocr_layout.addWidget(self._result_text)

        self._bottom_splitter.addWidget(ocr_panel)

        # ── 右侧：脚本测试器 ──
        script_panel = QWidget()
        script_layout = QVBoxLayout(script_panel)
        script_layout.setContentsMargins(0, 0, 0, 0)

        script_btn_row = QHBoxLayout()
        self._btn_run_script = QPushButton(tr("运行脚本"))
        self._btn_run_script.clicked.connect(self._on_script_test)
        script_btn_row.addWidget(self._btn_run_script)
        # 当前用户下拉列表（仅影响脚本测试执行，不切换主页面用户）
        script_btn_row.addWidget(QLabel(tr("当前用户:")))
        self._script_user_combo = QComboBox()
        self._script_user_combo.setToolTip(tr("脚本测试执行时使用的用户，默认取主页面当前用户。切换不影响主页面。"))
        script_btn_row.addWidget(self._script_user_combo)
        self._btn_load_script = QPushButton(tr("加载文件"))
        self._btn_load_script.clicked.connect(self._on_load_script_file)
        script_btn_row.addWidget(self._btn_load_script)
        self._btn_save_script = QPushButton(tr("保存文件"))
        self._btn_save_script.clicked.connect(self._on_save_script_file)
        script_btn_row.addWidget(self._btn_save_script)
        # 当前场景 key 按钮
        self._scene_key_btn = _SceneKeyButton(self._get_current_scene_key)
        self._scene_key_btn.clicked.connect(self._scene_key_btn._on_clicked)
        script_btn_row.addWidget(self._scene_key_btn)
        apply_button_style(self._btn_run_script, self._btn_save_script)
        apply_button_style(
            self._btn_load_script,
            self._scene_key_btn,
            variant="neutral",
        )
        script_btn_row.addStretch()
        script_layout.addLayout(script_btn_row)

        self._script_text = QTextEdit()
        self._script_text.setPlaceholderText(tr("输入 DSL 脚本内容..."))
        self._script_text.setAcceptRichText(False)
        self._script_text.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13px;"
        )
        script_layout.addWidget(self._script_text)

        # 设置按钮目标为脚本编辑器
        self._scene_key_btn.set_target(self._script_text)

        self._bottom_splitter.addWidget(script_panel)
        self._bottom_splitter.setSizes([500, 500])

        self._splitter.addWidget(self._bottom_splitter)
        self._splitter.setStretchFactor(0, 2)
        self._splitter.setStretchFactor(1, 1)

        layout.addWidget(self._splitter, stretch=1)

        # ─── 状态栏 ───
        self._status_bar = QStatusBar()
        self._status_bar.showMessage(tr("请先新建或加载布局"))
        layout.addWidget(self._status_bar)

    # ─── Tab 操作 ────────────────────────────────────────

    def _apply_layout_to_tabs(self):
        """将当前布局的区域/坐标/方向/面板数据、画布配置分发到各 Tab

        只创建并初始化当前可见 Tab；其他场景保留轻量标题占位，区域数据和
        截图都等用户首次切换到该场景时再下发。
        """
        if self._current_layout is None:
            return
        layout_name = self._current_layout.name
        self._loaded_scenes = set()  # 布局变更，所有底图待重新加载
        from ...core.layout_manager import scene_layout_rels
        scene_keys = get_registry().all_scene_keys()
        self._scene_layout_paths = scene_layout_rels(layout_name, scene_keys)
        self._applying_layout = True
        try:
            self._ensure_scene_tab_loaded(self._get_current_scene_key())
            for scene_key, tab in self._tabs.items():
                self._apply_layout_to_tab(scene_key, tab)
        finally:
            self._applying_layout = False
        self._set_dirty(False)
        self._status_bar.showMessage(f"当前布局: {layout_name}")
        # 只加载当前可见 Tab 的底图，其余在切到时懒加载
        self._ensure_tab_image(self._current_scene_key)
        self._update_info_label()

    def _bind_scene_tab(self, scene_key: str, tab: SceneTab) -> None:
        """为刚刚按需创建的 Tab 绑定宿主回调并下发当前布局。"""
        tab.canvas.on_region_changed = (
            lambda sk=scene_key: self._on_scene_data_changed(sk))
        tab.canvas.on_canvas_changed = self._on_any_canvas_changed
        tab.canvas.on_poi_changed = (
            lambda sk=scene_key: self._on_scene_data_changed(sk))
        tab.canvas.on_panel_changed = (
            lambda sk=scene_key: self._on_scene_data_changed(sk))
        tab.canvas.on_subscene_ref_changed = (
            lambda sk=scene_key: self._on_scene_data_changed(sk))
        tab.canvas.on_status_message = (
            lambda msg: self._status_bar.showMessage(msg, 5000))
        tab.on_view_changed = self._on_tab_view_changed
        tab.on_scene_type_changed = self._on_scene_type_changed
        tab.on_version_pending_changed = self._on_version_pending_changed
        if self._current_layout is not None and not self._applying_layout:
            self._apply_layout_to_tab(scene_key, tab)

    def _apply_layout_to_tab(self, scene_key: str, tab: SceneTab) -> None:
        """只初始化一个已经创建的场景 Tab。"""
        if self._current_layout is None:
            return
        tab.set_regions(self._current_layout.get_scene_regions(scene_key))
        tab.set_points(self._current_layout.get_scene_points(scene_key))
        tab.set_arrows(self._current_layout.get_scene_arrows(scene_key))
        tab.set_panels(self._current_layout.get_scene_panels(scene_key))
        tab.set_subscene_refs(
            self._current_layout.get_scene_subscene_refs(scene_key))
        tab.set_subscene_contents(self._subscene_contents(scene_key))
        tab.set_canvas_config(
            self._current_layout.get_scene_crop_canvas(scene_key)
            if is_subscene(scene_key)
            else self._current_layout.get_canvas())
        tab.set_layout_name(
            self._current_layout.name,
            self._scene_layout_paths.get(scene_key),
        )
        if self._btn_canvas_mode.isChecked():
            tab.set_canvas_mode()
        else:
            tab.set_region_mode()
        tab._refresh_region_list()
        tab._refresh_point_list()
        tab._refresh_arrow_list()
        tab._refresh_panel_list()
        tab._refresh_reference_list()

    def _subscene_contents(self, scene_key: str) -> dict[str, dict[str, list]]:
        """根据当前 Layout 快照构建父场景内的只读子场景投影。"""
        if self._current_layout is None:
            return {}
        return {
            ref.key: {
                "regions": self._current_layout.get_scene_regions(ref.scene),
                "points": self._current_layout.get_scene_points(ref.scene),
                "panels": self._current_layout.get_scene_panels(ref.scene),
            }
            for ref in get_subscene_ref_defs(scene_key)
        }

    def _refresh_loaded_subscene_contents(self) -> None:
        """子场景坐标入 Layout 后，刷新所有已加载父场景的投影。"""
        for scene_key, tab in self._tabs.items():
            if get_subscene_ref_defs(scene_key):
                tab.set_subscene_contents(self._subscene_contents(scene_key))

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
            if is_subscene(scene_key):
                tab.canvas.focus_canvas()
        else:
            tab.canvas.clear_image()
        self._loaded_scenes.add(scene_key)

    def _on_scene_tab_changed(self, _idx: int = 0):
        """二级场景 Tab 切换：按需加载底图 + 刷新尺寸信息"""
        scene_key = self._get_current_scene_key()
        self._ensure_scene_tab_loaded(scene_key)
        self._ensure_tab_image(scene_key)
        self._update_canvas_button_text()
        self._update_info_label()

    def _on_scene_type_changed(self, scene_key: str):
        tab = self._tabs.get(scene_key)
        if tab is not None and self._current_layout is not None:
            tab.set_canvas_config(
                self._current_layout.get_scene_crop_canvas(scene_key)
                if is_subscene(scene_key) else self._current_layout.get_canvas())
        self._update_canvas_button_text()

    def _clear_all_tabs(self):
        """清空所有 Tab 的区域/坐标/方向/面板"""
        for tab in self._tabs.values():
            tab.set_regions([])
            tab.set_points([])
            tab.set_arrows([])
            tab.set_panels([])
            tab.set_subscene_refs([])

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
        2. 内存：同步完整 Layout；目标 Tab 尚未创建时也不能丢迁移结果
        """
        changed = self._manager.migrate_item_across_layouts(source, target, kind, key)
        src_tab = self._tabs.get(source)
        dst_tab = self._tabs.get(target)
        if self._current_layout is not None:
            temp = Layout.from_dict("", self._current_layout.to_dict())
            for sk, tab in ((source, src_tab), (target, dst_tab)):
                if tab is None:
                    continue
                temp.set_scene_regions(sk, tab.get_regions())
                temp.set_scene_points(sk, tab.get_points())
                temp.set_scene_arrows(sk, tab.get_arrows())
                temp.set_scene_panels(sk, tab.get_panels())
                temp.set_scene_subscene_refs(sk, tab.get_subscene_refs())
                if is_subscene(sk):
                    temp.set_scene_crop_canvas(sk, tab.get_canvas_config())
            migrated = migrate_layout_item(temp, source, target, kind, key)
            if migrated:
                for sk in (source, target):
                    self._current_layout.set_scene_regions(
                        sk, temp.get_scene_regions(sk))
                    self._current_layout.set_scene_points(
                        sk, temp.get_scene_points(sk))
                    self._current_layout.set_scene_arrows(
                        sk, temp.get_scene_arrows(sk))
                    self._current_layout.set_scene_panels(
                        sk, temp.get_scene_panels(sk))
                for sk, tab in ((source, src_tab), (target, dst_tab)):
                    if tab is None:
                        continue
                    tab.set_regions(temp.get_scene_regions(sk))
                    tab.set_points(temp.get_scene_points(sk))
                    tab.set_arrows(temp.get_scene_arrows(sk))
                    tab.set_panels(temp.get_scene_panels(sk))
            if migrated and kind == "panel":
                # 网格参数同步到已迁移的画布 Panel（与同场景编辑行为一致）
                scene = get_registry().get_scene(target)
                pdef = next((p for p in scene.panels if p.key == key), None) if scene else None
                if pdef is not None:
                    panels = self._current_layout.get_scene_panels(target)
                    for p in panels:
                        if p.key == key:
                            # rows/cols 属于布局级配置，不从 PanelDef 同步
                            p.min_visible = pdef.min_visible
                    self._current_layout.set_scene_panels(target, panels)
                    if dst_tab is not None:
                        dst_tab.set_panels(panels)
            if dst_tab is not None:
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
            self._status_bar.showMessage(tr("无截图源，请先在主窗口定位窗口"))
            return
        if self._current_layout is None:
            self._status_bar.showMessage(tr("没有已加载的布局"))
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
            self._status_bar.showMessage(error_msg or tr("刷新截图失败"))

    def _refresh_ref_group_combo(self):
        """刷新参考图分组下拉：保留当前选中（如有），重新加载图库分组"""
        from lvjiang.core.reference_db import ReferenceDatabase

        current_data = self._combo_ref_group.currentData()
        self._combo_ref_group.blockSignals(True)
        self._combo_ref_group.clear()
        self._combo_ref_group.addItem(tr("全部"), None)
        try:
            groups = ReferenceDatabase().get_groups()
        except Exception:
            groups = []
        for g in groups:
            self._combo_ref_group.addItem(g, g)
        # 恢复之前的选中
        if current_data is not None:
            idx = self._combo_ref_group.findData(current_data)
            if idx >= 0:
                self._combo_ref_group.setCurrentIndex(idx)
        self._combo_ref_group.blockSignals(False)

    # ─── 画布模式切换 ───────────────────────────────────

    def _on_toggle_canvas_mode(self, checked: bool):
        """切换画布编辑模式，同步到所有 Tab"""
        for tab in self._tabs.values():
            if checked:
                tab.set_canvas_mode()
            else:
                tab.set_region_mode()
        self._update_canvas_button_text()
        if checked:
            self._status_bar.showMessage(tr("画布编辑模式：拖拽/缩放黄色画布框以排除窗口边框"))
        else:
            self._status_bar.showMessage(tr("已退出画布编辑模式"))

    def _update_canvas_button_text(self):
        crop = is_subscene(self._get_current_scene_key())
        if self._btn_canvas_mode.isChecked():
            self._btn_canvas_mode.setText(
                tr("退出裁剪画布") if crop else tr("退出画布编辑"))
        else:
            self._btn_canvas_mode.setText(
                tr("裁剪画布") if crop else tr("编辑画布"))

    def _on_any_canvas_changed(self):
        """任一 Tab 的画布被修改时，以当前正在编辑的 Tab 为源，同步到其他所有 Tab

        切换画布编辑模式时所有 Tab 都处于 CANVAS 模式，不能用 edit_mode 判断源 Tab（
        否则永远选中第一个 Tab，将其旧值推回用户实际编辑的 Tab 造成“跳回原位”）。
        鼠标只能作用于当前可见的画布，故当前激活 Tab 即为被编辑的 Tab。
        """
        source_tab = self._current_scene_tab()
        if source_tab is None:
            source_tab = next(iter(self._tabs.values()), None)
        if source_tab is None:
            return
        canvas = source_tab.get_canvas_config()
        if self._current_layout is not None:
            if is_subscene(source_tab.scene_key):
                self._current_layout.set_scene_crop_canvas(
                    source_tab.scene_key, canvas)
                source_tab.canvas.focus_canvas()
                self._mark_scene_dirty(source_tab.scene_key)
            else:
                self._current_layout.set_canvas(canvas)
                for key, tab in self._tabs.items():
                    if tab is not source_tab and not is_subscene(key):
                        tab.set_canvas_config(canvas)
                self._set_dirty(True)
        self._update_info_label()

    def _on_scene_data_changed(self, scene_key: str):
        """任一场景 Tab 的数据被修改时，标记该场景 dirty + 刷新当前 Tab 的列表"""
        self._mark_scene_dirty(scene_key)
        current = self._current_scene_tab()
        if current and current.scene_key == scene_key:
            if hasattr(current, '_refresh_region_list'):
                current._refresh_region_list()
            if hasattr(current, '_on_poi_changed'):
                current._on_poi_changed()
            if hasattr(current, '_on_panel_changed'):
                current._on_panel_changed()
            if hasattr(current, '_refresh_reference_list'):
                current._refresh_reference_list()

    def _mark_scene_dirty(self, scene_key: str):
        """标记指定场景为已变更，更新 Tab 标题绿点 + 全局 dirty 指示"""
        self._data_dirty_scenes.add(scene_key)
        self._set_scene_dirty_visual(scene_key, True)

    def _set_scene_dirty_visual(self, scene_key: str, dirty: bool):
        """只维护 dirty 并集与显示，不改变变更来源。"""
        if not dirty:
            self._dirty_scenes.discard(scene_key)
            self._update_scene_tab_title(scene_key, dirty=False)
            self._dirty_label.setVisible(bool(self._dirty_scenes))
            return
        if scene_key in self._dirty_scenes:
            return
        self._dirty_scenes.add(scene_key)
        self._dirty_label.setVisible(True)
        self._update_scene_tab_title(scene_key, dirty=True)

    def _on_version_pending_changed(self, scene_key: str):
        """版本链接进入现有保存/放弃状态，但不冒充布局数据修改。"""
        tab = self._tabs.get(scene_key)
        dirty = scene_key in self._data_dirty_scenes or bool(
            tab and tab.has_pending_version)
        self._set_scene_dirty_visual(scene_key, dirty)

    def _mark_all_scenes_clean(self):
        """清除所有场景的变更标记"""
        for sk in self._dirty_scenes:
            self._update_scene_tab_title(sk, dirty=False)
        for tab in self._tabs.values():
            tab.clear_pending_versions()
        self._data_dirty_scenes.clear()
        self._dirty_scenes.clear()
        self._dirty_label.setVisible(False)

    def _set_dirty(self, dirty: bool):
        """兼容层：True = 标记所有场景 dirty，False = 清除全部 dirty"""
        if dirty:
            for sk in get_registry().all_scene_keys():
                self._mark_scene_dirty(sk)
        else:
            self._mark_all_scenes_clean()

    def _update_scene_tab_title(self, scene_key: str, dirty: bool):
        """更新场景 Tab 标题：dirty 时追加绿点指示"""
        for _group_key, tab_widget in self._group_tabs.items():
            for i in range(tab_widget.count()):
                w = tab_widget.widget(i)
                if getattr(w, "scene_key", "") == scene_key:
                    base_name = get_scene_name(scene_key)
                    tab_widget.setTabText(i, f"{base_name} ●" if dirty else base_name)
                    tabBar = tab_widget.tabBar()
                    assert tabBar is not None
                    if dirty:
                        tabBar.setTabTextColor(i, Qt.GlobalColor.green)
                    else:
                        tabBar.setTabTextColor(i, Qt.GlobalColor.black)
                    return

    def _get_dirty_scene_names(self) -> str:
        """获取变更场景的可读名称列表（用于提示文案）"""
        if not self._dirty_scenes:
            return ""
        names = [get_scene_name(sk) for sk in sorted(self._dirty_scenes)]
        return "、".join(names)

    def reject(self):
        """关闭对话框（X 按钮 / Esc）前检查未保存修改

        QDialog.closeEvent 会调用 reject()，且当 reject 后对话框仍可见时
        会 ignore 关闭事件，因此只需覆盖 reject 即可同时拦截 X 与 Esc。
        """
        if not self._confirm_discard_changes(tr("关闭场景编辑器")):
            return
        super().reject()

    # ─── 尺寸信息栏 ─────────────────────────────

    def _update_info_label(self, *args):
        """刷新尺寸信息栏：当前 TAB 截图尺寸/比例 + 画布相对截图的坐标尺寸/比例"""
        if not hasattr(self, "_info_label"):
            return
        tab = self._current_scene_tab()
        if tab is None or tab.canvas.image_size[0] <= 0:
            self._set_empty_info_label()
            return
        img_w, img_h = tab.canvas.image_size
        cfg = tab.get_canvas_config()
        canvas_w = max(1, round(cfg.w_ratio * img_w))
        canvas_h = max(1, round(cfg.h_ratio * img_h))

        ss_txt = f"{img_w}×{img_h} ({self._fmt_ratio(img_w, img_h)})"
        cv_txt = f"{canvas_w}×{canvas_h} ({self._fmt_ratio(canvas_w, canvas_h)})"

        muted = get_theme_manager().tokens.text_muted
        self._info_label.setText(
            f'<span style="color:{muted};">截图</span> '
            f'<b style="color:#4da6ff;">{ss_txt}</b>'
            f'<span style="color:{muted};"> │ </span>'
            f'<span style="color:{muted};">画布</span> '
            f'<b style="color:#ffc850;">{cv_txt}</b>'
        )

    def _set_empty_info_label(self) -> None:
        muted = get_theme_manager().tokens.text_muted
        text = tr("截图：—　　画布：—")
        self._info_label.setText(f'<span style="color:{muted};">{text}</span>')

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
        return self._get_current_scene_key()

    def _current_scene_tab(self) -> SceneTab | None:
        """获取当前激活的 SceneTab"""
        scene_key = self._get_current_scene_key()
        if scene_key:
            return self._ensure_scene_tab_loaded(scene_key)
        group_widget = self._group_tab_widget.currentWidget()
        if isinstance(group_widget, QTabWidget):
            scene_widget = group_widget.currentWidget()
            if isinstance(scene_widget, SceneTab):
                return scene_widget
        return None
