"""地域截图预览不能把屏幕位置写成底图 POI。"""
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np

from lvjiang.core.maps import MapDef, MapPoi, MapRegion, MapSurvey, image_sha256
from lvjiang.ui.map_manager.dialog import MapManagerDialog


def test_survey_preview_preserves_definition_and_blocks_poi_edits(qtbot, tmp_path, monkeypatch):
    image = np.full((200, 300, 3), 100, dtype=np.uint8)
    _, encoded = cv2.imencode(".png", image)
    data = bytes(encoded)
    path = tmp_path / "survey.png"
    path.write_bytes(data)
    definition = MapDef("north", "北部地图", layer="local")
    definition.regions = [MapRegion("east", "东部")]
    definition.pois = [MapPoi("stone", 0.2, 0.3, name="界碑")]
    definition.surveys = [MapSurvey("focus", "东部定位", "east", image_sha256(data))]
    registry = SimpleNamespace(all_scene_keys=lambda: [], get_scene=lambda _key: None)
    monkeypatch.setattr(MapManagerDialog, "_scene_registry", lambda _self: registry)
    manager = Mock()
    manager.list_maps.return_value = [definition]
    manager.load.return_value = definition
    manager.base_image_path.return_value = path
    manager.survey_image_path.return_value = path
    manager.resolver.is_dev_mode.return_value = False
    dialog = MapManagerDialog(manager=manager)
    qtbot.addWidget(dialog)
    original = definition.to_dict()
    dialog.combo_preview.setCurrentIndex(1)
    assert dialog.canvas.has_image and not dialog.poi_table.isEnabled()
    dialog._on_canvas_clicked(0.8, 0.9)
    assert definition.to_dict() == original and not dialog._dirty
    dialog.combo_preview.setCurrentIndex(0)
    assert dialog.poi_table.isEnabled() and not dialog._dirty
    assert definition.to_dict() == original
