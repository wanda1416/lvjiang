"""布局管理器识别操作包含当前视图中的跨场景引用。"""

from types import SimpleNamespace

import numpy as np

from lvjiang.core.layout_models import CanvasConfig, Region
from lvjiang.core.scene_definition_models import RegionDef
from lvjiang.ui.scene_editor.recognition_ops import RecognitionOpsMixin


class _Output:
    def __init__(self):
        self.lines = []

    def clear(self):
        self.lines.clear()

    def append(self, line):
        self.lines.append(line)


class _Host(RecognitionOpsMixin):
    def __init__(self):
        self._result_text = _Output()
        self._status_bar = SimpleNamespace(showMessage=lambda _message: None)

    def _get_recognition_image(self, _current_tab):
        return np.zeros((100, 100, 3), dtype=np.uint8), None


def test_recognize_all_references_includes_referenced_slot(
        qapp, monkeypatch):
    qapp.processEvents()
    region = Region(
        "shared_slot", 0.1, 0.1, 0.2, 0.2,
        source_scene="shared",
    )
    tab = SimpleNamespace(
        scene_key="target",
        current_view="detail",
        get_visible_regions=lambda: [region],
        get_canvas_config=lambda: CanvasConfig(),
    )
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.recognition_ops.get_effective_region_defs",
        lambda scene, view: [
            RegionDef("shared_slot", "共享槽位", type="slot"),
        ],
    )
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.recognition_ops.get_region_name",
        lambda _scene, _key: "共享槽位",
    )

    calls = []
    recognizer = SimpleNamespace(
        reference_db=SimpleNamespace(get_custom_input_fields=lambda: []),
        recognize=lambda crop, group=None: (
            calls.append((crop.shape, group))
            or SimpleNamespace(
                label="", meta={}, ocr_texts={}, confidence=0.0)
        ),
    )
    monkeypatch.setattr(
        "lvjiang.core.ocr.OCREngine", lambda: object())
    monkeypatch.setattr(
        "lvjiang.core.recognizers.ReferenceRecognizer",
        lambda _ocr: recognizer,
    )

    host = _Host()
    host._on_recognize_region_references(tab)

    assert len(calls) == 1
    assert host._result_text.lines == ["共享槽位: (空槽)"]
