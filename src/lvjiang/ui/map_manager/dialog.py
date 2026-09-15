"""地图管理对话框

职责边界：

- **管**：新建 / 复制到本地 / 删除地图；导入底图（文件或当前画面截取）；
  在底图上标注 POI；地图基本信息与 HUD 场景/实体绑定。
- **不管**：小地图、大地图、开关按钮在屏幕上的位置——那是布局层的坐标，
  只有场景编辑器一个入口。这里只提供"标定 HUD…"按钮跳过去。
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.config.resolver import LAYER_LOCAL, LAYER_REMOTE, SystemContentProtected
from ...core.maps import (
    NAV_MODE_CLOSED_LOOP,
    NAV_MODE_PATHFIND,
    MapDef,
    MapError,
    MapManager,
    MapPoi,
    validate_map_key,
    validate_poi_key,
)
from ...core.recognizers.heading import detect_arrow_heading, draw_heading_overlay
from ...i18n import tr
from ..button_styles import apply_button_style
from ..dialog_guards import EscapeCloseConfirmationMixin
from ..theme import get_theme_manager
from .poi_canvas import PoiCanvas, bgr_to_qimage

_NAV_MODE_LABELS = (
    (NAV_MODE_CLOSED_LOOP, "闭环导航（无寻路，如渡尘墟）"),
    (NAV_MODE_PATHFIND, "游戏寻路（大世界）"),
)
_POI_COLUMNS = ("key", "kind", "name", "x", "y")
_LAYER_LABELS = {LAYER_LOCAL: "本地", LAYER_REMOTE: "远程"}


def _encode_png(img: np.ndarray) -> bytes:
    import cv2

    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise MapError(tr("底图编码失败"))
    return bytes(buf)


def _decode_image(data: bytes) -> np.ndarray | None:
    import cv2

    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


class MapManagerDialog(EscapeCloseConfirmationMixin, QDialog):
    """地图管理：左栏地图列表，右栏底图/POI 与基本信息两个 Tab。"""

    def __init__(
        self,
        parent=None,
        *,
        screenshot_callback: Callable | None = None,
        layout_manager=None,
        manager: MapManager | None = None,
        open_scene_editor: Callable[[str], None] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("地图管理"))
        self.resize(1200, 800)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self._manager = manager or MapManager()
        self._screenshot_callback = screenshot_callback
        self._layout_manager = layout_manager
        self._open_scene_editor = open_scene_editor
        self._current: MapDef | None = None
        self._base_image: np.ndarray | None = None
        self._pending_image: bytes | None = None   # 新导入、尚未保存的底图
        self._dirty = False
        self._loading = False
        self._selected_poi = ""
        self.data_changed = False
        self._setup_ui()
        self._reload_list()

    # ─── UI ────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # 左栏：地图列表
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.map_list = QListWidget()
        self.map_list.currentItemChanged.connect(self._on_map_selected)
        left_layout.addWidget(self.map_list, 1)
        list_buttons = QHBoxLayout()
        self.btn_new = QPushButton(tr("新建"))
        self.btn_copy = QPushButton(tr("复制到本地"))
        self.btn_delete = QPushButton(tr("删除"))
        apply_button_style(self.btn_new)
        apply_button_style(self.btn_copy, variant="neutral")
        apply_button_style(self.btn_delete, variant="danger")
        self.btn_new.clicked.connect(self._on_new)
        self.btn_copy.clicked.connect(self._on_copy_to_local)
        self.btn_delete.clicked.connect(self._on_delete)
        for button in (self.btn_new, self.btn_copy, self.btn_delete):
            list_buttons.addWidget(button)
        left_layout.addLayout(list_buttons)
        splitter.addWidget(left)

        # 右栏
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.lbl_header = QLabel()
        self.lbl_header.setWordWrap(True)
        right_layout.addWidget(self.lbl_header)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_canvas_tab(), tr("底图与 POI"))
        self.tabs.addTab(self._build_info_tab(), tr("基本信息与绑定"))
        self.tabs.addTab(self._build_heading_tab(), tr("小地图朝向"))
        right_layout.addWidget(self.tabs, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 940])

        bottom = QHBoxLayout()
        bottom.addStretch()
        self.btn_save = QPushButton(tr("保存"))
        apply_button_style(self.btn_save)
        self.btn_save.clicked.connect(self._on_save)
        btn_close = QPushButton(tr("关闭"))
        apply_button_style(btn_close, variant="neutral")
        btn_close.clicked.connect(self.close)
        bottom.addWidget(self.btn_save)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

    def _build_canvas_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        toolbar = QHBoxLayout()
        self.btn_import_file = QPushButton(tr("导入底图文件…"))
        self.btn_import_capture = QPushButton(tr("从当前画面截取"))
        self.btn_fit = QPushButton(tr("适应窗口"))
        apply_button_style(self.btn_import_file, self.btn_import_capture)
        apply_button_style(self.btn_fit, variant="neutral")
        self.btn_import_file.clicked.connect(self._on_import_file)
        self.btn_import_capture.clicked.connect(self._on_import_capture)
        self.btn_fit.clicked.connect(lambda: self.canvas.fit())
        for button in (self.btn_import_file, self.btn_import_capture, self.btn_fit):
            toolbar.addWidget(button)
        toolbar.addStretch()
        hint = QLabel(tr("在底图上点击新增 POI；点击已有标记选中它。坐标为底图归一化坐标。"))
        hint.setStyleSheet("color: palette(mid);")
        toolbar.addWidget(hint)
        layout.addLayout(toolbar)

        body = QSplitter(Qt.Orientation.Horizontal)
        self.canvas = PoiCanvas()
        self.canvas.canvas_clicked.connect(self._on_canvas_clicked)
        self.canvas.poi_clicked.connect(self._select_poi)
        body.addWidget(self.canvas)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        self.poi_table = QTableWidget(0, len(_POI_COLUMNS))
        self.poi_table.setHorizontalHeaderLabels(
            [tr("key"), tr("种类"), tr("名称"), "x", "y"])
        self.poi_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.poi_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.poi_table.itemChanged.connect(self._on_poi_item_changed)
        self.poi_table.itemSelectionChanged.connect(self._on_poi_row_selected)
        side_layout.addWidget(self.poi_table, 1)
        self.btn_delete_poi = QPushButton(tr("删除选中 POI"))
        apply_button_style(self.btn_delete_poi, variant="danger")
        self.btn_delete_poi.clicked.connect(self._on_delete_poi)
        side_layout.addWidget(self.btn_delete_poi)
        body.addWidget(side)
        body.setStretchFactor(0, 1)
        body.setStretchFactor(1, 0)
        body.setSizes([700, 340])
        layout.addWidget(body, 1)
        return page

    def _build_info_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        basic = QGroupBox(tr("基本信息"))
        form = QFormLayout(basic)
        self.edit_key = QLineEdit()
        self.edit_key.setReadOnly(True)
        self.edit_name = QLineEdit()
        self.edit_name.textEdited.connect(self._mark_dirty)
        self.combo_mode = QComboBox()
        for value, label in _NAV_MODE_LABELS:
            self.combo_mode.addItem(tr(label), value)
        self.combo_mode.currentIndexChanged.connect(self._mark_dirty)
        self.check_north_up = QCheckBox(tr("大地图正北朝上"))
        self.check_north_up.toggled.connect(self._mark_dirty)
        form.addRow("key", self.edit_key)
        form.addRow(tr("名称"), self.edit_name)
        form.addRow(tr("导航模式"), self.combo_mode)
        form.addRow("", self.check_north_up)
        layout.addWidget(basic)

        binding = QGroupBox(tr("HUD 场景绑定（坐标在场景编辑器里标定）"))
        bform = QFormLayout(binding)
        self.combo_scene = QComboBox()
        self.combo_scene.setEditable(False)
        self.combo_scene.currentIndexChanged.connect(self._on_scene_changed)
        bform.addRow(tr("HUD 场景"), self.combo_scene)
        self._entity_combos: dict[str, QComboBox] = {}
        for attr, label, kind in (
            ("minimap", "小地图区域", "region"),
            ("minimap_center", "小地图中心点", "point"),
            ("open_map", "打开地图", "region"),
            ("full_map", "大地图区域", "region"),
            ("close_map", "关闭地图", "region"),
        ):
            combo = QComboBox()
            combo.setEditable(True)
            combo.setProperty("entity_kind", kind)
            combo.currentTextChanged.connect(self._mark_dirty)
            self._entity_combos[attr] = combo
            bform.addRow(tr(label), combo)
        self.btn_calibrate = QPushButton(tr("标定 HUD…（打开场景编辑器）"))
        apply_button_style(self.btn_calibrate, variant="neutral")
        self.btn_calibrate.clicked.connect(self._on_calibrate)
        bform.addRow("", self.btn_calibrate)
        layout.addWidget(binding)
        layout.addStretch()
        return page

    def _build_heading_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        toolbar = QHBoxLayout()
        self.btn_heading_file = QPushButton(tr("载入截图文件…"))
        self.btn_heading_capture = QPushButton(tr("截取当前画面"))
        self.btn_heading_run = QPushButton(tr("重新识别"))
        apply_button_style(self.btn_heading_file, self.btn_heading_capture)
        apply_button_style(self.btn_heading_run, variant="neutral")
        self.btn_heading_file.clicked.connect(self._on_heading_file)
        self.btn_heading_capture.clicked.connect(self._on_heading_capture)
        self.btn_heading_run.clicked.connect(self._run_heading_detection)
        for button in (self.btn_heading_file, self.btn_heading_capture, self.btn_heading_run):
            toolbar.addWidget(button)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        hint = QLabel(tr(
            "载入整张局内截图后，按当前布局标定的小地图区域裁剪，解析居中的黄色内凹箭头。"
            "0° = 正北（屏幕上方），顺时针为正。"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(hint)

        body = QHBoxLayout()
        self.lbl_heading_preview = QLabel(tr("（尚未载入截图）"))
        self.lbl_heading_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_heading_preview.setMinimumSize(320, 320)
        self.lbl_heading_preview.setStyleSheet("border: 1px solid palette(mid);")
        body.addWidget(self.lbl_heading_preview, 1)

        params = QGroupBox(tr("箭头颜色与搜索参数（保存到地图）"))
        form = QFormLayout(params)
        self._hsv_spins: dict[str, list[QSpinBox]] = {"lower": [], "upper": []}
        for bound, label in (("lower", "HSV 下限"), ("upper", "HSV 上限")):
            row = QHBoxLayout()
            for maximum in (180, 255, 255):
                spin = QSpinBox()
                spin.setRange(0, maximum)
                spin.valueChanged.connect(self._mark_dirty)
                self._hsv_spins[bound].append(spin)
                row.addWidget(spin)
            form.addRow(tr(label), row)
        self.spin_window_ratio = QDoubleSpinBox()
        self.spin_window_ratio.setRange(0.05, 1.0)
        self.spin_window_ratio.setSingleStep(0.05)
        self.spin_window_ratio.setDecimals(2)
        self.spin_window_ratio.valueChanged.connect(self._mark_dirty)
        form.addRow(tr("搜索窗口（相对短边）"), self.spin_window_ratio)
        self.spin_min_area = QSpinBox()
        self.spin_min_area.setRange(1, 100000)
        self.spin_min_area.valueChanged.connect(self._mark_dirty)
        form.addRow(tr("最小面积（像素）"), self.spin_min_area)
        self.lbl_heading_result = QLabel(tr("—"))
        self.lbl_heading_result.setWordWrap(True)
        form.addRow(tr("识别结果"), self.lbl_heading_result)
        body.addWidget(params, 0)
        layout.addLayout(body, 1)
        self._heading_source: np.ndarray | None = None
        self._heading_crop: np.ndarray | None = None
        return page

    def _fill_heading_form(self, map_def: MapDef) -> None:
        for bound in ("lower", "upper"):
            values = map_def.heading.hsv_lower if bound == "lower" else map_def.heading.hsv_upper
            for spin, value in zip(self._hsv_spins[bound], values, strict=True):
                spin.setValue(int(value))
        self.spin_window_ratio.setValue(map_def.heading.window_ratio)
        self.spin_min_area.setValue(map_def.heading.min_area)
        self._heading_source = None
        self._heading_crop = None
        self.lbl_heading_preview.setText(tr("（尚未载入截图）"))
        self.lbl_heading_preview.setPixmap(QPixmap())
        self.lbl_heading_result.setText("—")

    def _collect_heading_form(self) -> None:
        assert self._current is not None
        lower = tuple(spin.value() for spin in self._hsv_spins["lower"])
        upper = tuple(spin.value() for spin in self._hsv_spins["upper"])
        self._current.heading.hsv_lower = lower  # type: ignore[assignment]
        self._current.heading.hsv_upper = upper  # type: ignore[assignment]
        self._current.heading.window_ratio = float(self.spin_window_ratio.value())
        self._current.heading.min_area = int(self.spin_min_area.value())

    def _on_heading_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择局内截图"), "", tr("图片文件 (*.png *.jpg *.jpeg *.bmp *.webp)"))
        if not path:
            return
        from pathlib import Path

        img = _decode_image(Path(path).read_bytes())
        if img is None:
            QMessageBox.warning(self, tr("读取失败"), tr("无法解码图片: {path}").format(path=path))
            return
        self._set_heading_source(img)

    def _on_heading_capture(self) -> None:
        if self._screenshot_callback is None:
            QMessageBox.information(
                self, tr("提示"), tr("截图功能不可用，请先在主窗口定位窗口或连接设备"))
            return
        try:
            result = self._screenshot_callback()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, tr("截图失败"), str(exc))
            return
        frame, error = result if isinstance(result, tuple) else (result, None)
        if frame is None:
            QMessageBox.warning(self, tr("截图失败"), error or tr("无法获取截图"))
            return
        self._set_heading_source(frame)

    def _set_heading_source(self, frame: np.ndarray) -> None:
        self._heading_source = frame
        minimap_key = self._entity_combos["minimap"].currentText().strip()
        crop, note = self._crop_region(frame, minimap_key)
        self._heading_crop = crop
        if note:
            self.lbl_heading_result.setText(note)
        self._run_heading_detection()

    def _run_heading_detection(self) -> None:
        if self._heading_crop is None or self._current is None:
            return
        self._collect_heading_form()
        cfg = self._current.heading
        result = detect_arrow_heading(
            self._heading_crop,
            window_ratio=cfg.window_ratio,
            hsv_lower=cfg.hsv_lower, hsv_upper=cfg.hsv_upper,
            min_area=cfg.min_area,
        )
        shown = self._heading_crop
        if result is None:
            self.lbl_heading_result.setText(tr("未找到箭头：检查颜色阈值或小地图区域标定"))
        else:
            shown = draw_heading_overlay(self._heading_crop, result)
            method = tr("燕尾缺口") if result.method == "notch" else tr("尖端（退化）")
            self.lbl_heading_result.setText(
                tr("朝向 {deg:.1f}°　置信度 {conf:.2f}　方法：{method}　面积 {area}px")
                .format(deg=result.heading_deg, conf=result.confidence,
                        method=method, area=result.area))
        pixmap = QPixmap.fromImage(bgr_to_qimage(shown))
        target = self.lbl_heading_preview.size()
        self.lbl_heading_preview.setPixmap(pixmap.scaled(
            target, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    # ─── 列表 ──────────────────────────────────────────────

    def _reload_list(self, select_key: str | None = None) -> None:
        target = select_key or (self._current.key if self._current else None)
        self.map_list.blockSignals(True)
        self.map_list.clear()
        for map_def in self._manager.list_maps():
            layer_tag = _LAYER_LABELS.get(map_def.layer, "系统")
            item = QListWidgetItem(f"{map_def.name}  ({map_def.key})  [{tr(layer_tag)}]")
            item.setData(Qt.ItemDataRole.UserRole, map_def.key)
            self.map_list.addItem(item)
        self.map_list.blockSignals(False)
        row = -1
        if target:
            for index in range(self.map_list.count()):
                entry = self.map_list.item(index)
                if entry is not None and entry.data(Qt.ItemDataRole.UserRole) == target:
                    row = index
                    break
        if row < 0 and self.map_list.count():
            row = 0
        self.map_list.setCurrentRow(row)
        if row < 0:
            self._show_map(None)

    def _on_map_selected(self, current: QListWidgetItem | None, _previous) -> None:
        if not self._confirm_discard():
            return
        key = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        if key is None:
            self._show_map(None)
            return
        try:
            self._show_map(self._manager.load(key))
        except Exception as exc:  # noqa: BLE001 — 坏文件只影响自己
            QMessageBox.warning(self, tr("加载失败"), str(exc))
            self._show_map(None)

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self, tr("未保存"), tr("当前地图有未保存的修改，放弃吗？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self._dirty = False
            return True
        return False

    # ─── 显示 ──────────────────────────────────────────────

    @property
    def editable(self) -> bool:
        if self._current is None:
            return False
        if self._manager.resolver.is_dev_mode():
            return not self._current.is_remote
        return self._current.layer == LAYER_LOCAL

    def _show_map(self, map_def: MapDef | None) -> None:
        self._loading = True
        try:
            self._current = map_def
            self._pending_image = None
            self._selected_poi = ""
            self._dirty = False
            if map_def is None:
                self.lbl_header.setText(tr("没有地图。点击“新建”创建一张。"))
                self._base_image = None
                self.canvas.set_image(None)
                self._fill_poi_table()
                self._set_editing_enabled(False)
                return
            self._base_image = self._load_base_image(map_def)
            self.canvas.set_image(self._base_image)
            self.canvas.set_pois(map_def.pois)
            self._fill_poi_table()
            self.edit_key.setText(map_def.key)
            self.edit_name.setText(map_def.name)
            index = self.combo_mode.findData(map_def.navigation_mode)
            self.combo_mode.setCurrentIndex(max(index, 0))
            self.check_north_up.setChecked(map_def.north_up)
            self._fill_scene_combo(map_def.ui.scene)
            self._fill_entity_combos(map_def)
            self._fill_heading_form(map_def)
            self._update_header()
            self._set_editing_enabled(self.editable)
        finally:
            self._loading = False

    def _update_header(self) -> None:
        if self._current is None:
            return
        tokens = get_theme_manager().tokens
        parts = [f"<b>{self._current.name}</b>（{self._current.key}）"]
        layer = _LAYER_LABELS.get(self._current.layer, "系统")
        parts.append(tr("来源：{layer}").format(layer=tr(layer)))
        if not self.editable:
            parts.append(
                f'<span style="color:{tokens.warning}">'
                + tr("只读，请先“复制到本地”再修改")
                + "</span>")
        if self._base_image is None:
            parts.append(
                f'<span style="color:{tokens.warning}">' + tr("尚未导入底图") + "</span>")
        self.lbl_header.setText("　".join(parts))

    def _set_editing_enabled(self, enabled: bool) -> None:
        for widget in (
            self.btn_import_file, self.btn_import_capture, self.btn_delete_poi,
            self.edit_name, self.combo_mode, self.check_north_up, self.combo_scene,
            self.btn_save, *self._entity_combos.values(),
            self.spin_window_ratio, self.spin_min_area,
            *self._hsv_spins["lower"], *self._hsv_spins["upper"],
        ):
            widget.setEnabled(enabled)
        # 朝向识别是只读分析，系统地图也能试；参数改动才需要可编辑
        has_map = self._current is not None
        for widget in (self.btn_heading_file, self.btn_heading_capture, self.btn_heading_run):
            widget.setEnabled(has_map)
        self.poi_table.setEnabled(enabled)
        self.canvas.set_editable(enabled)
        has_map = self._current is not None
        self.btn_copy.setEnabled(has_map and not self.editable and not (
            self._current is not None and self._current.layer == LAYER_LOCAL))
        self.btn_delete.setEnabled(has_map)
        self.btn_calibrate.setEnabled(has_map and self._open_scene_editor is not None)

    def _load_base_image(self, map_def: MapDef) -> np.ndarray | None:
        path = self._manager.base_image_path(map_def)
        if path is None:
            return None
        try:
            return _decode_image(path.read_bytes())
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"读取底图失败 {path}: {exc}")
            return None

    def _mark_dirty(self, *_args) -> None:
        if not self._loading and self._current is not None:
            self._dirty = True

    # ─── 场景绑定 ──────────────────────────────────────────

    def _scene_registry(self):
        from ...core.scene_registry import get_registry
        return get_registry()

    def _fill_scene_combo(self, current: str) -> None:
        self.combo_scene.blockSignals(True)
        self.combo_scene.clear()
        keys = list(self._scene_registry().all_scene_keys())
        if current and current not in keys:
            keys.append(current)
        for key in keys:
            scene = self._scene_registry().get_scene(key)
            label = f"{scene.name}（{key}）" if scene is not None else key
            self.combo_scene.addItem(label, key)
        index = self.combo_scene.findData(current)
        self.combo_scene.setCurrentIndex(max(index, 0))
        self.combo_scene.blockSignals(False)

    def _fill_entity_combos(self, map_def: MapDef) -> None:
        scene_key = self.combo_scene.currentData() or map_def.ui.scene
        scene = self._scene_registry().get_scene(scene_key)
        regions = [r.key for r in scene.regions] if scene else []
        points = [p.key for p in scene.points] if scene else []
        for attr, combo in self._entity_combos.items():
            current = getattr(map_def.ui, attr)
            options = points if combo.property("entity_kind") == "point" else regions
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(options)
            combo.setCurrentText(current)
            combo.blockSignals(False)

    def _on_scene_changed(self, _index: int) -> None:
        if self._loading or self._current is None:
            return
        self._fill_entity_combos(self._current)
        self._mark_dirty()

    def _on_calibrate(self) -> None:
        if self._current is None or self._open_scene_editor is None:
            return
        scene_key = self.combo_scene.currentData() or self._current.ui.scene
        self._open_scene_editor(scene_key)

    # ─── POI ───────────────────────────────────────────────

    def _fill_poi_table(self) -> None:
        pois = self._current.pois if self._current else []
        self.poi_table.blockSignals(True)
        self.poi_table.setRowCount(len(pois))
        for row, poi in enumerate(pois):
            values = (poi.key, poi.kind, poi.name, f"{poi.x:.4f}", f"{poi.y:.4f}")
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                self.poi_table.setItem(row, col, item)
        self.poi_table.blockSignals(False)
        if self._selected_poi:
            for row, poi in enumerate(pois):
                if poi.key == self._selected_poi:
                    self.poi_table.selectRow(row)
                    break

    def _on_canvas_clicked(self, rx: float, ry: float) -> None:
        if self._current is None or not self.editable:
            return
        existing = {p.key for p in self._current.pois}
        index = 1
        while f"poi_{index}" in existing:
            index += 1
        poi = MapPoi(key=f"poi_{index}", x=rx, y=ry)
        self._current.pois.append(poi)
        self._selected_poi = poi.key
        self._dirty = True
        self.canvas.set_pois(self._current.pois, poi.key)
        self._fill_poi_table()

    def _select_poi(self, key: str) -> None:
        if self._current is None:
            return
        self._selected_poi = key
        self.canvas.set_pois(self._current.pois, key)
        self._fill_poi_table()

    def _on_poi_row_selected(self) -> None:
        if self._current is None:
            return
        rows = {item.row() for item in self.poi_table.selectedItems()}
        if len(rows) != 1:
            return
        row = rows.pop()
        if 0 <= row < len(self._current.pois):
            self._selected_poi = self._current.pois[row].key
            self.canvas.set_pois(self._current.pois, self._selected_poi)

    def _on_poi_item_changed(self, item: QTableWidgetItem) -> None:
        if self._current is None or self._loading:
            return
        row, col = item.row(), item.column()
        if not (0 <= row < len(self._current.pois)):
            return
        poi = self._current.pois[row]
        text = item.text().strip()
        try:
            field = _POI_COLUMNS[col]
            if field == "key":
                validate_poi_key(text)
                if any(p.key == text for p in self._current.pois if p is not poi):
                    raise MapError(tr("POI key 重复: {key}").format(key=text))
                poi.key = text
                self._selected_poi = text
            elif field == "kind":
                poi.kind = text or "poi"
            elif field == "name":
                poi.name = text
            else:
                value = float(text)
                if not 0.0 <= value <= 1.0:
                    raise MapError(tr("坐标必须在 0–1 之间"))
                setattr(poi, field, value)
        except (MapError, ValueError) as exc:
            QMessageBox.warning(self, tr("无效的 POI"), str(exc))
            self._fill_poi_table()
            return
        self._dirty = True
        self.canvas.set_pois(self._current.pois, self._selected_poi)
        if col in (3, 4):
            self._fill_poi_table()

    def _on_delete_poi(self) -> None:
        if self._current is None or not self._selected_poi:
            return
        self._current.pois = [p for p in self._current.pois if p.key != self._selected_poi]
        self._selected_poi = ""
        self._dirty = True
        self.canvas.set_pois(self._current.pois)
        self._fill_poi_table()

    # ─── 底图 ──────────────────────────────────────────────

    def _on_import_file(self) -> None:
        if self._current is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择底图"), "", tr("图片文件 (*.png *.jpg *.jpeg *.bmp *.webp)"))
        if not path:
            return
        try:
            from pathlib import Path

            img = _decode_image(Path(path).read_bytes())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, tr("读取失败"), str(exc))
            return
        if img is None:
            QMessageBox.warning(self, tr("读取失败"), tr("无法解码图片: {path}").format(path=path))
            return
        self._apply_new_base_image(img)

    def _on_import_capture(self) -> None:
        if self._current is None:
            return
        if self._screenshot_callback is None:
            QMessageBox.information(
                self, tr("提示"), tr("截图功能不可用，请先在主窗口定位窗口或连接设备"))
            return
        try:
            result = self._screenshot_callback()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, tr("截图失败"), str(exc))
            return
        frame, error = result if isinstance(result, tuple) else (result, None)
        if frame is None:
            QMessageBox.warning(self, tr("截图失败"), error or tr("无法获取截图"))
            return
        cropped, note = self._crop_full_map(frame)
        self._apply_new_base_image(cropped)
        if note:
            QMessageBox.information(self, tr("提示"), note)

    def _crop_full_map(self, frame: np.ndarray) -> tuple[np.ndarray, str]:
        """按当前布局的大地图区域裁剪；没标定就用整帧并提示。"""
        return self._crop_region(
            frame, self._entity_combos["full_map"].currentText().strip())

    def _crop_region(self, frame: np.ndarray, region_key: str) -> tuple[np.ndarray, str]:
        """按当前布局里 HUD 场景的某个区域裁剪帧；没标定就返回整帧并给出提示。"""
        if self._current is None or self._layout_manager is None:
            return frame, ""
        try:
            layout_key = self._layout_manager.get_active_layout_key()
            layout = self._layout_manager.load_layout(layout_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"读取当前布局失败: {exc}")
            return frame, ""
        if layout is None:
            return frame, ""
        scene_key = self.combo_scene.currentData() or self._current.ui.scene
        region = next(
            (r for r in layout.get_scene_regions(scene_key)
             if r.key == region_key and not r.disabled and r.has_position),
            None)
        if region is None:
            return frame, tr(
                "当前布局尚未标定 {scene}.{region}，已使用整帧作为底图。"
            ).format(scene=scene_key, region=region_key)
        h, w = frame.shape[:2]
        canvas = layout.get_canvas()
        x0 = int((canvas.x_ratio + region.x_ratio * canvas.w_ratio) * w)
        y0 = int((canvas.y_ratio + region.y_ratio * canvas.h_ratio) * h)
        x1 = int(x0 + region.w_ratio * canvas.w_ratio * w)
        y1 = int(y0 + region.h_ratio * canvas.h_ratio * h)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, max(x1, x0 + 1)), min(h, max(y1, y0 + 1))
        return frame[y0:y1, x0:x1].copy(), ""

    def _apply_new_base_image(self, img: np.ndarray) -> None:
        self._base_image = img
        self._pending_image = _encode_png(img)
        self._dirty = True
        self.canvas.set_image(img)
        self.canvas.set_pois(self._current.pois if self._current else [], self._selected_poi)
        self._update_header()

    # ─── 保存 / 新建 / 复制 / 删除 ─────────────────────────

    def _collect_form(self) -> None:
        assert self._current is not None
        self._current.name = self.edit_name.text().strip()
        self._current.navigation_mode = self.combo_mode.currentData()
        self._current.north_up = self.check_north_up.isChecked()
        self._current.ui.scene = self.combo_scene.currentData() or self._current.ui.scene
        for attr, combo in self._entity_combos.items():
            value = combo.currentText().strip()
            if value:
                setattr(self._current.ui, attr, value)
        self._collect_heading_form()

    def _on_save(self) -> None:
        if self._current is None or not self.editable:
            return
        self._collect_form()
        try:
            self._manager.save(self._current)
            if self._pending_image is not None:
                self._manager.import_base_image(self._current, self._pending_image)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, tr("保存失败"), str(exc))
            return
        self._pending_image = None
        self._dirty = False
        self.data_changed = True
        self._reload_list(self._current.key)

    def _on_new(self) -> None:
        if not self._confirm_discard():
            return
        key, ok = QInputDialog.getText(
            self, tr("新建地图"), tr("地图 key（小写字母开头，字母/数字/下划线）:"))
        if not ok:
            return
        key = key.strip()
        try:
            validate_map_key(key)
        except MapError as exc:
            QMessageBox.warning(self, tr("无效的 key"), str(exc))
            return
        name, ok = QInputDialog.getText(self, tr("新建地图"), tr("地图名称:"), text=key)
        if not ok or not name.strip():
            return
        try:
            self._manager.create(key, name.strip())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, tr("创建失败"), str(exc))
            return
        self.data_changed = True
        self._reload_list(key)
        QMessageBox.information(
            self, tr("已创建"),
            tr("已生成 HUD 场景 map_{key}。请先在场景编辑器中标定小地图与大地图区域，"
               "再导入底图。").format(key=key))

    def _on_copy_to_local(self) -> None:
        if self._current is None or not self._confirm_discard():
            return
        try:
            self._manager.copy_to_local(self._current.key)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, tr("复制失败"), str(exc))
            return
        self.data_changed = True
        self._reload_list(self._current.key)

    def _on_delete(self) -> None:
        if self._current is None:
            return
        answer = QMessageBox.question(
            self, tr("删除地图"),
            tr("删除地图「{name}」及其底图？自动生成的 HUD 场景 map_{key} 也会一并删除。")
            .format(name=self._current.name, key=self._current.key),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._manager.delete(self._current.key)
        except SystemContentProtected as exc:
            QMessageBox.warning(self, tr("不能删除"), str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, tr("删除失败"), str(exc))
            return
        self._dirty = False
        self.data_changed = True
        self._current = None
        self._reload_list()

    def closeEvent(self, event):  # noqa: N802 — Qt 虚函数
        if not self._confirm_discard():
            event.ignore()
            return
        super().closeEvent(event)
