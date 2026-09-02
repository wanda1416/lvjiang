"""单个场景的编辑 Tab：左侧画布 + 右侧四 Tab（区域 / 坐标 / 方向 / 面板）"""

from collections.abc import Callable

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.layout_models import (
    Arrow,
    CanvasConfig,
    Panel,
    Point,
    Region,
)
from ...core.scene_registry import (
    get_registry,
    get_scene_point_pairs,
    get_scene_regions,
    get_scene_views,
    get_view_visible_keys,
    is_subscene,
)
from ...i18n import tr
from ..button_styles import apply_button_style, apply_dialog_button_box_style
from .canvas import EditMode, RegionCanvas
from .scene_panel_editor import PanelEditorMixin
from .scene_poi_panel import PoiPanelMixin
from .scene_reference_editor import SceneReferenceEditorMixin
from .scene_region_panel import RegionPanelMixin
from .scene_view_dialog import ViewManagerDialog


class SceneTab(RegionPanelMixin, PoiPanelMixin, PanelEditorMixin,
               SceneReferenceEditorMixin, QWidget):
    """单个场景的编辑 Tab：左侧画布 + 右侧四 Tab（区域列表 / 坐标列表 / 方向列表 / 面板列表）"""

    def __init__(self, scene_key: str, image: np.ndarray | None = None, parent=None):
        super().__init__(parent)
        self._scene_key = scene_key
        # 跨场景迁移回调：(kind, key, source_scene, target_scene)，由 dialog 注入
        self.on_item_migrated: Callable[[str, str, str, str], None] | None = None
        # 当前视图 key（空 = 看全部）；视图切换回调：(scene_key, view)，由 dialog 注入换底图
        self._current_view: str = ""
        self.on_view_changed: Callable[[str, str], None] | None = None
        self.on_scene_type_changed: Callable[[str], None] | None = None
        # 当前布局名，由 dialog 经 set_layout_name 注入（解析布局坐标文件来源用）
        self._layout_name: str = ""
        self._layout_rel_path: str = ""
        # 版本提升是编辑状态，不在点击链接时写盘。dialog 保存成功后统一清除；
        # 切换/关闭选择 Discard 时控件随 Tab 状态一起丢弃。
        self._pending_scene_version: int | None = None
        self._pending_layout_version: int | None = None
        self.on_version_pending_changed: Callable[[str], None] | None = None

        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：画布顶部工具栏 + 画布
        self._canvas = RegionCanvas()
        self._canvas.set_scene_key(scene_key)
        if image is not None:
            self._canvas.set_image(image)
        self._canvas.set_current_regions(get_scene_regions(scene_key))
        self._canvas.set_current_points(get_scene_point_pairs(scene_key))

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(2)
        left_layout.addLayout(self._build_canvas_toolbar())
        left_layout.addWidget(self._canvas)
        self._splitter.addWidget(left)

        # 右侧五列布局组件
        self._right_tabs = QTabWidget()
        self._right_tabs.addTab(self._build_region_panel(), tr("区域"))
        self._right_tabs.addTab(self._build_point_panel(), tr("坐标"))
        self._right_tabs.addTab(self._build_arrow_panel(), tr("方向"))
        self._right_tabs.addTab(self._build_panel_panel(), tr("网格"))
        self._right_tabs.addTab(self._build_reference_panel(), tr("引用"))
        self._refresh_scene_type_ui()
        self._splitter.addWidget(self._right_tabs)
        self._splitter.setSizes([650, 250])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._splitter)

        self._refresh_region_list()
        self._refresh_point_list()
        self._refresh_arrow_list()
        self._refresh_panel_list()
        self._refresh_reference_list()
        self._canvas.on_region_changed = self._refresh_region_list
        self._canvas.on_poi_changed = self._on_poi_changed
        self._canvas.on_panel_changed = self._on_panel_changed
        self._canvas.on_subscene_ref_changed = self._on_subscene_ref_changed
        # 选中态变化只刷新列表高亮，不走 dialog 的 dirty 链路
        self._canvas.on_selection_changed = self._on_selection_changed

    # ─── 面板构建 ────────────────────────────────────────

    def _build_canvas_toolbar(self) -> QHBoxLayout:
        """画布顶部工具栏：视图选择器 + 管理入口 + 来源/版本标识"""
        bar = QHBoxLayout()
        # 作为 SceneTab 内容的首行，保持与上方分组/场景 Tab 紧密衔接。
        bar.setContentsMargins(0, 0, 0, 0)
        self._btn_scene_manage = QPushButton(tr("场景管理"))
        self._btn_scene_manage.clicked.connect(self._on_scene_manage)
        apply_button_style(self._btn_scene_manage, variant="neutral")
        bar.addWidget(self._btn_scene_manage)
        self._view_label = QLabel(tr("视图"))
        self._view_label.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        bar.addWidget(self._view_label)
        self._view_combo = QComboBox()
        self._view_combo.setMinimumWidth(120)
        self._view_combo.currentIndexChanged.connect(self._on_view_combo_changed)
        bar.addWidget(self._view_combo)
        self._btn_manage_views = QPushButton(tr("视图管理"))
        self._btn_manage_views.setToolTip(tr("开启多视图、新增/重命名/删除视图"))
        self._btn_manage_views.clicked.connect(self._on_manage_views)
        apply_button_style(self._btn_manage_views, variant="neutral")
        bar.addWidget(self._btn_manage_views)
        bar.addSpacing(12)

        self._scene_version_title = QLabel(tr("场景版本："))
        self._scene_version_value = QLabel()
        self._scene_version_link = QLabel()
        for label in (
                self._scene_version_title,
                self._scene_version_value,
                self._scene_version_link):
            label.setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Fixed,
            )
        self._configure_version_link(self._scene_version_link, "scene")
        bar.addWidget(self._scene_version_title)
        bar.addWidget(self._scene_version_value)
        bar.addWidget(self._scene_version_link)
        bar.addSpacing(18)

        self._layout_version_title = QLabel(tr("布局版本："))
        self._layout_version_value = QLabel()
        self._layout_version_link = QLabel()
        for label in (
                self._layout_version_title,
                self._layout_version_value,
                self._layout_version_link):
            label.setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Fixed,
            )
        self._configure_version_link(self._layout_version_link, "layout")
        bar.addWidget(self._layout_version_title)
        bar.addWidget(self._layout_version_value)
        bar.addWidget(self._layout_version_link)
        bar.addStretch()

        self._refresh_view_combo()
        return bar

    def _refresh_scene_type_ui(self):
        subscene = is_subscene(self._scene_key)
        for widget in (self._view_label, self._view_combo, self._btn_manage_views):
            widget.setVisible(not subscene)
        if hasattr(self, "_right_tabs"):
            self._right_tabs.setTabEnabled(4, not subscene)

    def _on_scene_manage(self):
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        if scene is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("场景管理"))
        form = QFormLayout(dialog)
        checkbox = QCheckBox(tr("可作为引用子场景"))
        checkbox.setChecked(scene.is_subscene)
        if scene.views and not scene.is_subscene:
            checkbox.setEnabled(False)
            checkbox.setToolTip(tr("多视图场景不能转为子场景，请先取消多视图"))
        form.addRow(checkbox)
        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        apply_dialog_button_box_style(box)
        box.accepted.connect(dialog.accept)
        box.rejected.connect(dialog.reject)
        form.addRow(box)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            registry.set_scene_type(
                self._scene_key, "subscene" if checkbox.isChecked() else "scene")
        except ValueError as exc:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, tr("设置失败"), str(exc))
            return
        self._current_view = ""
        self._refresh_view_combo()
        self._refresh_scene_type_ui()
        self._refresh_reference_list()
        if self.on_scene_type_changed:
            self.on_scene_type_changed(self._scene_key)

    def set_layout_name(self, layout_name: str,
                        rel_path: str | None = None):
        """由 dialog 在应用布局时注入——布局坐标文件的来源要按布局名解析"""
        if layout_name != self._layout_name:
            self._pending_layout_version = None
        self._layout_name = layout_name
        self._layout_rel_path = rel_path or ""
        self._refresh_version_info()

    def _configure_version_link(self, label: QLabel, kind: str) -> None:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        label.setOpenExternalLinks(False)
        label.linkActivated.connect(
            lambda _href, k=kind: self._on_version_link(k))

    def _version_rel_path(self, kind: str) -> str:
        if kind == "scene":
            return f"scenes/{self._scene_key}.yaml"
        if not self._layout_name:
            return ""
        if self._layout_rel_path:
            return self._layout_rel_path
        from ...core.layout_manager import scene_layout_rel
        return scene_layout_rel(self._layout_name, self._scene_key)

    def _refresh_version_control(self, kind: str, value: QLabel,
                                 link: QLabel, title: QLabel) -> None:
        from ...core.config.resolver import get_resolver
        from ..config_origin import layer_style, origin_tooltip

        resolver = get_resolver()
        rel_path = self._version_rel_path(kind)
        origin = resolver.describe_entity(rel_path) if rel_path else None
        available = resolver.list_entity_origins(rel_path) if rel_path else ()
        pending = (self._pending_scene_version if kind == "scene"
                   else self._pending_layout_version)
        layer = origin.layer if origin else ""
        version = origin.version if origin is not None else None
        value.setText("-" if (pending or version) is None
                      else f"v{pending if pending is not None else version}")
        value.setStyleSheet(layer_style(layer))
        # 三个控件都挂同一份说明：没人知道非要悬停在数字上才有提示
        from ...core.config.resolver import EntityOrigin
        tip = origin_tooltip(
            origin or EntityOrigin("", None), available, pending)
        for widget in (title, value, link):
            widget.setToolTip(tip)

        if not rel_path or origin is None or origin.version is None \
                or not resolver.is_dev_mode():
            link.hide()
            return
        if pending is None:
            target = resolver.next_entity_version(rel_path)
            link.setText(f'<a href="bump">{tr("提升至 v{version}").format(version=target)}</a>')
        else:
            link.setText(f'<a href="cancel">{tr("撤销提升")}</a>')
        link.show()

    def _refresh_version_info(self) -> None:
        if not hasattr(self, "_scene_version_value"):
            return
        self._refresh_version_control(
            "scene", self._scene_version_value, self._scene_version_link,
            self._scene_version_title)
        self._refresh_version_control(
            "layout", self._layout_version_value, self._layout_version_link,
            self._layout_version_title)

    def _on_version_link(self, kind: str) -> None:
        from ...core.config.resolver import get_resolver

        rel_path = self._version_rel_path(kind)
        if not rel_path:
            return
        attr = ("_pending_scene_version" if kind == "scene"
                else "_pending_layout_version")
        pending = getattr(self, attr)
        setattr(self, attr, None if pending is not None
                else get_resolver().next_entity_version(rel_path))
        self._refresh_version_info()
        if self.on_version_pending_changed is not None:
            self.on_version_pending_changed(self._scene_key)

    @property
    def pending_scene_version(self) -> int | None:
        return self._pending_scene_version

    @property
    def pending_layout_version(self) -> int | None:
        return self._pending_layout_version

    @property
    def has_pending_version(self) -> bool:
        return (self._pending_scene_version is not None
                or self._pending_layout_version is not None)

    def clear_pending_versions(self) -> None:
        if not self.has_pending_version:
            return
        self._pending_scene_version = None
        self._pending_layout_version = None
        self._refresh_version_info()

    # ─── 视图 ────────────────────────────────────────────

    @property
    def current_view(self) -> str:
        return self._current_view

    def _refresh_view_combo(self):
        """按场景视图定义重建下拉框：无多视图则只有「单视图」占位"""
        combo = self._view_combo
        combo.blockSignals(True)
        combo.clear()
        views = get_scene_views(self._scene_key)
        if not views:
            combo.addItem(tr("单视图"), userData="")
            combo.setEnabled(False)
        else:
            combo.setEnabled(True)
            combo.addItem(tr("全部"), userData="")
            for v in views:
                combo.addItem(v.name, userData=v.key)
            # 尽量保持当前选中视图
            idx = combo.findData(self._current_view)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)
        # 若原选中视图已不存在（被删除/取消多视图），回落到全部
        if combo.findData(self._current_view) < 0:
            self._current_view = ""

    def _on_view_combo_changed(self, _idx: int):
        self._current_view = self._view_combo.currentData() or ""
        self._apply_view_filter()
        self._refresh_region_list()
        self._refresh_point_list()
        self._refresh_arrow_list()
        self._refresh_panel_list()
        if self.on_view_changed:
            self.on_view_changed(self._scene_key, self._current_view)

    def _apply_view_filter(self):
        """把当前视图的可见 key 集合下发给画布"""
        visible = get_view_visible_keys(self._scene_key, self._current_view)
        self._canvas.set_view_filter(visible)

    def _on_manage_views(self):
        dlg = ViewManagerDialog(self._scene_key, self)
        dlg.exec()
        if getattr(dlg, "_changed", False):
            self._refresh_view_combo()
            self._on_view_combo_changed(0)

    # ─── 属性 ────────────────────────────────────────────

    @property
    def canvas(self) -> RegionCanvas:
        return self._canvas

    @property
    def scene_key(self) -> str:
        return self._scene_key

    @property
    def edit_mode(self) -> EditMode:
        return self._canvas.edit_mode

    # ─── region 数据 ─────────────────────────────────────

    def get_regions(self) -> list[Region]:
        return self._canvas.get_regions()

    def get_visible_regions(self) -> list[Region]:
        """当前视图下可见的区域（OCR/识别只应作用于可见区域）"""
        return self._canvas.get_visible_regions()

    def set_regions(self, regions: list[Region]):
        self._canvas.set_regions(regions)
        self._refresh_region_list()

    # ─── point / arrow 数据 ──────────────────────────────

    def get_points(self) -> list[Point]:
        return self._canvas.get_points()

    def set_points(self, points: list[Point]):
        self._canvas.set_points(points)
        self._refresh_point_list()

    def get_arrows(self) -> list[Arrow]:
        return self._canvas.get_arrows()

    def set_arrows(self, arrows: list[Arrow]):
        self._canvas.set_arrows(arrows)
        self._refresh_arrow_list()

    # ─── panel 数据 ───────────────────────────────

    def get_panels(self) -> list[Panel]:
        return self._canvas.get_panels()

    def get_visible_panels(self) -> list[Panel]:
        """当前视图下可见的面板（OCR/识别只应作用于可见面板）"""
        return self._canvas.get_visible_panels()

    def set_panels(self, panels: list[Panel]):
        self._canvas.set_panels(panels)
        self._refresh_panel_list()

    def get_subscene_refs(self):
        return self._canvas.get_subscene_refs()

    def set_subscene_refs(self, refs):
        self._canvas.set_subscene_refs(refs)
        self._refresh_reference_list()

    def set_subscene_contents(self, contents):
        self._canvas.set_subscene_contents(contents)

    # ─── 画布配置 / 模式 ─────────────────────────────────

    def set_canvas_config(self, config: CanvasConfig):
        self._canvas.set_canvas_config(config)

    def get_canvas_config(self) -> CanvasConfig:
        return self._canvas.get_canvas_config()

    def set_canvas_mode(self):
        self._canvas.set_canvas_mode()

    def set_region_mode(self):
        self._canvas.set_region_mode()

    # ─── 列表刷新 ────────────────────────────────────────

    def _refresh_lists(self):
        """刷新区域和坐标列表（场景定义变化后调用）"""
        self._canvas.set_current_regions(get_scene_regions(self._scene_key))
        self._canvas.set_current_points(get_scene_point_pairs(self._scene_key))
        # 归属视图可能变更（如区域改到另一视图），重新下发视图过滤，
        # 否则画布仍按旧可见集继续绘制已移出当前视图的实例
        self._apply_view_filter()
        self._refresh_region_list()
        self._refresh_point_list()
        self._refresh_panel_list()
        self._refresh_reference_list()

    def _on_panel_changed(self):
        """画布 panel 数据变化时刷新面板列表"""
        self._refresh_panel_list()

    def _on_subscene_ref_changed(self):
        self._refresh_reference_list()

    def _on_selection_changed(self):
        """画布选中态变化（非数据修改）：仅刷新各列表显示"""
        self._refresh_region_list()
        self._on_poi_changed()
        self._refresh_panel_list()
