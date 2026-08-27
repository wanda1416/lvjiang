"""单个场景的编辑 Tab：左侧画布 + 右侧四 Tab（区域 / 坐标 / 方向 / 面板）"""

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
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
    get_scene_point_pairs,
    get_scene_regions,
    get_scene_views,
    get_view_visible_keys,
)
from ...i18n import tr
from .canvas import EditMode, RegionCanvas
from .scene_panel_editor import PanelEditorMixin
from .scene_poi_panel import PoiPanelMixin
from .scene_region_panel import RegionPanelMixin
from .scene_view_dialog import ViewManagerDialog


def describe_scene_origin(scene_key: str,
                          layout_name: str = "") -> tuple[str, bool]:
    """场景当前生效配置的「版本号 / 来源」文案，返回 (文本, 是否含远端)。

    场景编辑器实际在编辑**两个**文件：`scenes/{key}.yaml`（场景定义）与
    `layouts/{布局}/{key}.json`（该布局下的坐标）。两者都可能被远端独立
    顶替，只标一个就正好留下盲点——而这个标识存在的意义就是消除盲点，
    让人随时知道自己正在看哪一份。

    独立于 SceneTab 的纯函数：构造 SceneTab 需要整个 Qt 控件树，而它在
    测试里反复创建/析构会触发 item delegate 的析构时序问题（PyQt 已知
    坑，与本功能无关），把文案逻辑摘出来才能直接测。
    """
    from ...core.config.resolver import (
        LAYER_LOCAL,
        LAYER_REMOTE,
        LAYER_SYSTEM,
        get_resolver,
    )
    from ...core.layout_manager import scene_layout_rel

    labels = {
        LAYER_LOCAL: tr("用户"),
        LAYER_REMOTE: tr("远端"),
        LAYER_SYSTEM: tr("系统"),
    }
    resolver = get_resolver()
    targets = [(tr("场景"), f"scenes/{scene_key}.yaml")]
    if layout_name:
        targets.append((tr("布局"), scene_layout_rel(layout_name, scene_key)))

    parts: list[str] = []
    has_remote = False
    for title, rel_path in targets:
        origin = resolver.describe_entity(rel_path)
        if not origin.layer:
            continue
        has_remote = has_remote or origin.layer == LAYER_REMOTE
        source = labels.get(origin.layer, origin.layer)
        version = "" if origin.version is None else f" v{origin.version}"
        parts.append(f"{title}{version}·{source}")
    return "　".join(parts), has_remote


class SceneTab(RegionPanelMixin, PoiPanelMixin, PanelEditorMixin, QWidget):
    """单个场景的编辑 Tab：左侧画布 + 右侧四 Tab（区域列表 / 坐标列表 / 方向列表 / 面板列表）"""

    def __init__(self, scene_key: str, image: np.ndarray | None = None, parent=None):
        super().__init__(parent)
        self._scene_key = scene_key
        # 跨场景迁移回调：(kind, key, source_scene, target_scene)，由 dialog 注入
        self.on_item_migrated = None
        # 当前视图 key（空 = 看全部）；视图切换回调：(scene_key, view)，由 dialog 注入换底图
        self._current_view: str = ""
        self.on_view_changed = None
        # 当前布局名，由 dialog 经 set_layout_name 注入（解析布局坐标文件来源用）
        self._layout_name: str = ""

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

        # 右侧四 Tab
        self._right_tabs = QTabWidget()
        self._right_tabs.addTab(self._build_region_panel(), tr("区域列表"))
        self._right_tabs.addTab(self._build_point_panel(), tr("坐标列表"))
        self._right_tabs.addTab(self._build_arrow_panel(), tr("方向列表"))
        self._right_tabs.addTab(self._build_panel_panel(), tr("面板列表"))
        self._splitter.addWidget(self._right_tabs)
        self._splitter.setSizes([650, 250])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._splitter)

        self._refresh_region_list()
        self._refresh_point_list()
        self._refresh_arrow_list()
        self._refresh_panel_list()
        self._canvas.on_region_changed = self._refresh_region_list
        self._canvas.on_poi_changed = self._on_poi_changed
        self._canvas.on_panel_changed = self._on_panel_changed
        # 选中态变化只刷新列表高亮，不走 dialog 的 dirty 链路
        self._canvas.on_selection_changed = self._on_selection_changed

    # ─── 面板构建 ────────────────────────────────────────

    def _build_canvas_toolbar(self) -> QHBoxLayout:
        """画布顶部工具栏：视图选择器 + 管理入口 + 来源/版本标识"""
        bar = QHBoxLayout()
        # 作为 SceneTab 内容的首行，保持与上方分组/场景 Tab 紧密衔接。
        bar.setContentsMargins(0, 0, 0, 0)
        view_label = QLabel(tr("视图"))
        view_label.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        bar.addWidget(view_label)
        self._view_combo = QComboBox()
        self._view_combo.setMinimumWidth(120)
        self._view_combo.currentIndexChanged.connect(self._on_view_combo_changed)
        bar.addWidget(self._view_combo)
        self._btn_manage_views = QPushButton(tr("管理视图"))
        self._btn_manage_views.setToolTip(tr("开启多视图、新增/重命名/删除视图"))
        self._btn_manage_views.clicked.connect(self._on_manage_views)
        bar.addWidget(self._btn_manage_views)
        bar.addSpacing(12)

        # 正在编辑的是哪一份：远端下发的配置会顶替出厂文件，不标出来的话
        # 开发者本地跑出来的行为和用户不一样却毫不知情，排查问题无从下手。
        self._origin_label = QLabel()
        # 来源文本使用剩余空间，但不能用自身长度反向撑宽画布侧分栏。
        self._origin_label.setMinimumWidth(0)
        self._origin_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        self._origin_label.setStyleSheet("color: palette(mid);")
        bar.addWidget(self._origin_label, stretch=1)
        self._refresh_origin_label()

        self._refresh_view_combo()
        return bar

    def set_layout_name(self, layout_name: str):
        """由 dialog 在应用布局时注入——布局坐标文件的来源要按布局名解析"""
        self._layout_name = layout_name
        self._refresh_origin_label()

    def _refresh_origin_label(self):
        """刷新「版本号 / 来源」标识"""
        text, has_remote = describe_scene_origin(
            self._scene_key, self._layout_name)
        self._origin_label.setText(text)
        self._origin_label.setToolTip(
            tr("配置来源：用户=你改过的；系统=出厂；远端=在线下发（会顶替出厂）"))
        # 远端顶替出厂是最容易被忽略、也最需要被注意到的一种状态
        self._origin_label.setStyleSheet(
            "color: #D97706;" if has_remote else "color: palette(mid);")

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

    def _on_panel_changed(self):
        """画布 panel 数据变化时刷新面板列表"""
        self._refresh_panel_list()

    def _on_selection_changed(self):
        """画布选中态变化（非数据修改）：仅刷新各列表显示"""
        self._refresh_region_list()
        self._on_poi_changed()
        self._refresh_panel_list()
