"""地图管理对话框：新建、POI 标注、底图导入、朝向识别与保存的端到端冒烟。"""

from types import SimpleNamespace

import cv2
import numpy as np
import yaml
from PyQt6.QtWidgets import QMessageBox

from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.maps import MapManager
from lvjiang.core.scene_config import load_scene_manifest
from lvjiang.core.scene_definition import SceneRegistry


def _env(tmp_path, monkeypatch):
    system = tmp_path / "system"
    local = tmp_path / "local"
    (system).mkdir(parents=True)
    (system / "scenes.yaml").write_text(yaml.safe_dump({
        "schema_version": 2,
        "scenes": {"general": {"name": "通用", "items": []}},
    }, allow_unicode=True), encoding="utf-8")
    resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=True)
    manifest = load_scene_manifest(resolver)
    registry = SceneRegistry(
        resolver=resolver, scene_order=manifest.order,
        group_config=manifest.groups, group_names=manifest.group_names,
        disabled_scenes=manifest.disabled)

    def reload_in_place(rel_path: str) -> None:
        if not rel_path.startswith("scenes/"):
            return
        current = load_scene_manifest(resolver)
        refreshed = SceneRegistry(
            resolver=resolver, scene_order=current.order,
            group_config=current.groups, group_names=current.group_names,
            disabled_scenes=current.disabled)
        registry.__dict__.clear()
        registry.__dict__.update(refreshed.__dict__)

    resolver.add_change_listener(reload_in_place)
    monkeypatch.setattr("lvjiang.core.scene_registry.get_registry", lambda: registry)
    return resolver, registry, MapManager(resolver=resolver, registry=registry)


def _frame_with_arrow(heading_deg: float, size=(400, 700)) -> np.ndarray:
    """整帧截图：右上角一块小地图，居中一个朝 heading_deg 的黄色燕尾箭头。"""
    import math

    h, w = size
    frame = np.full((h, w, 3), (70, 90, 70), dtype=np.uint8)
    # 布局未标定时按整帧分析，箭头放在帧中心附近才落在搜索窗口内
    cx, cy, s = w // 2 + 30, h // 2 - 20, 14
    shape = [(0, -s), (0.7 * s, 0.6 * s), (0, 0.15 * s), (-0.7 * s, 0.6 * s)]
    rad = math.radians(heading_deg)
    pts = np.array([
        (cx + x * math.cos(rad) - y * math.sin(rad), cy + x * math.sin(rad) + y * math.cos(rad))
        for x, y in shape], dtype=np.int32)
    cv2.fillPoly(frame, [pts], (40, 210, 250))
    return frame


def test_map_manager_end_to_end(qtbot, tmp_path, monkeypatch):
    resolver, registry, manager = _env(tmp_path, monkeypatch)
    from lvjiang.ui.map_manager import MapManagerDialog

    frame = _frame_with_arrow(120)
    dialog = MapManagerDialog(
        manager=manager, screenshot_callback=lambda: (frame, None))
    qtbot.addWidget(dialog)
    assert dialog.map_list.count() == 0

    # 新建：一个表单一次问齐 key / 名称 / 模式
    monkeypatch.setattr(
        "lvjiang.ui.form_dialog.ask_form",
        lambda *a, **k: {"key": "duchenxu", "name": "渡尘墟", "mode": "closed_loop"})
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    # 任何断言失败都不能让 qtbot 回收时的“放弃修改？”模态框把测试挂死
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog._on_new()

    assert dialog.map_list.count() == 1
    assert dialog._current is not None and dialog._current.key == "duchenxu"
    assert dialog.editable
    assert registry.get_scene("map_duchenxu") is not None
    assert dialog.combo_scene.currentData() == "map_duchenxu"
    assert dialog._entity_combos["minimap"].currentText() == "minimap"

    # 从当前画面截取底图（布局未标定 → 整帧），然后点两处加 POI
    dialog._on_import_capture()
    assert dialog.canvas.has_image and dialog._pending_image is not None
    dialog._on_canvas_clicked(0.25, 0.5)
    dialog._on_canvas_clicked(0.75, 0.5)
    assert [p.key for p in dialog._current.pois] == ["poi_1", "poi_2"]
    assert dialog.poi_table.rowCount() == 2
    # 表格改 key 与种类
    dialog.poi_table.item(0, 0).setText("exit_a")
    dialog.poi_table.item(0, 1).setText("exit")
    assert dialog._current.pois[0].key == "exit_a" and dialog._current.pois[0].kind == "exit"

    # 小地图朝向：布局未标定，用整帧分析
    dialog._on_heading_capture()
    text = dialog.lbl_heading_result.text()
    assert "朝向" in text, text
    deg = float(text.split("朝向 ")[1].split("°")[0])
    assert abs(deg - 120) <= 3.0

    # 保存并回读
    dialog._on_save()
    assert not dialog._dirty and dialog.data_changed
    saved = manager.load("duchenxu")
    assert [p.key for p in saved.pois] == ["exit_a", "poi_2"]
    assert saved.heading.window_ratio == 0.5
    assert len(saved.base_image_sha256) == 64
    assert manager.base_image_path(saved) is not None
    assert (tmp_path / "system/maps/duchenxu/base.png").stat().st_size > 0


def test_map_manager_readonly_for_system_map_in_user_mode(qtbot, tmp_path, monkeypatch):
    _, _, dev = _env(tmp_path, monkeypatch)
    dev.create("duchenxu", "渡尘墟")
    user_resolver = ConfigResolver(
        system_dir=tmp_path / "system", local_dir=tmp_path / "local", dev_mode=False)
    from lvjiang.ui.map_manager import MapManagerDialog

    dialog = MapManagerDialog(manager=MapManager(resolver=user_resolver, registry=object()))
    qtbot.addWidget(dialog)

    assert dialog._current is not None and not dialog.editable
    assert not dialog.btn_save.isEnabled()
    assert dialog.btn_copy.isEnabled()
    assert "只读" in dialog.lbl_header.text()

    dialog._on_copy_to_local()

    assert dialog.editable and dialog._current.layer == "local"
    assert not dialog.btn_save.isEnabled()
    dialog.edit_name.setText("新名称")
    dialog.edit_name.textEdited.emit("新名称")
    assert dialog.btn_save.isEnabled()
    dialog._set_dirty(False)


def test_rejecting_discard_restores_previous_map_selection(qtbot, tmp_path, monkeypatch):
    _, _, manager = _env(tmp_path, monkeypatch)
    manager.create("first", "第一张")
    manager.create("second", "第二张")
    from lvjiang.ui.map_manager import MapManagerDialog

    dialog = MapManagerDialog(manager=manager)
    qtbot.addWidget(dialog)
    assert dialog._current is not None and dialog._current.key == "first"
    dialog._set_dirty(True)
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.StandardButton.No,
    )

    dialog.map_list.setCurrentRow(1)

    assert dialog.map_list.currentRow() == 0
    assert dialog._current is not None and dialog._current.key == "first"
    dialog._set_dirty(False)


def test_unbound_capture_requires_confirmation_before_using_full_frame(
        qtbot, tmp_path, monkeypatch):
    _, _, manager = _env(tmp_path, monkeypatch)
    manager.create("duchenxu", "渡尘墟")
    from lvjiang.ui.map_manager import MapManagerDialog

    frame = _frame_with_arrow(90)
    dialog = MapManagerDialog(
        manager=manager, screenshot_callback=lambda: (frame, None),
        layout_manager=None,
    )
    qtbot.addWidget(dialog)
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.StandardButton.No,
    )

    dialog._on_import_capture()

    assert dialog._pending_image is None
    assert not dialog._dirty


def test_heading_uses_bound_minimap_center(qtbot, tmp_path, monkeypatch):
    _, _, manager = _env(tmp_path, monkeypatch)
    manager.create("duchenxu", "渡尘墟")
    from lvjiang.ui.map_manager import dialog as dialog_module

    layout = SimpleNamespace(
        get_canvas=lambda: SimpleNamespace(
            x_ratio=0.0, y_ratio=0.0, w_ratio=1.0, h_ratio=1.0),
        get_scene_regions=lambda _scene: [SimpleNamespace(
            key="minimap", disabled=False, has_position=True,
            x_ratio=0.1, y_ratio=0.1, w_ratio=0.5, h_ratio=0.5)],
        get_scene_points=lambda _scene: [SimpleNamespace(
            key="minimap_center", disabled=False, has_position=True,
            cx_ratio=0.2, cy_ratio=0.25)],
    )
    layout_manager = SimpleNamespace(
        get_active_layout_key=lambda: "desktop",
        load_layout=lambda _key: layout,
    )
    captured = {}

    def fake_detect(_img, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(dialog_module, "detect_arrow_heading", fake_detect)
    dialog = dialog_module.MapManagerDialog(
        manager=manager, layout_manager=layout_manager)
    qtbot.addWidget(dialog)

    dialog._set_heading_source(np.zeros((400, 400, 3), dtype=np.uint8))

    assert captured["center"] == (40.0, 60.0)
