"""Protect same-frame recognition and WF-controlled diagnostic recording.

These contracts prevent diagnosing a later screenshot, mixing equipment fields
from different frames, and silently reusing an old frame after capture failure.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest
from loguru import logger

from lvjiang.core.layout_models import Region, TemplateBinding
from lvjiang.core.recognizers import template_locator
from lvjiang.workflows.engine import WorkflowUserError
from lvjiang.workflows.grammar import parse_text
from lvjiang.workflows.workflow_references import collect_refs
from tests.workflows.conftest import make_engine


def _engine():
    engine = make_engine()
    engine._layout.get_scene_regions.return_value = [
        Region(key="left", x_ratio=0, y_ratio=0, w_ratio=0.5, h_ratio=1),
        Region(key="right", x_ratio=0.5, y_ratio=0, w_ratio=0.5, h_ratio=1),
    ]
    engine._layout.get_scene_panels.return_value = []
    frame = np.full((20, 40, 3), 10, dtype=np.uint8)
    frame[:, 20:] = 20
    engine._capture.capture.return_value = frame
    engine._ocr.ocr_scene_regions.side_effect = lambda img, canvas, regions, scene, **kw: {
        r.key: str(int(img[0, int(r.x_ratio * img.shape[1]), 0])) for r in regions}
    engine._ocr.recognize.side_effect = lambda img, **kw: [SimpleNamespace(
        text=str(int(img[0, 0, 0])), confidence=1.0,
        bbox=[[0, 0], [5, 0], [5, 5], [0, 5]])]
    engine._reference_recognizer = MagicMock()
    engine._reference_recognizer.recognize.side_effect = lambda img, **kw: SimpleNamespace(
        label=str(int(img[0, 0, 0])), confidence=1.0)
    return engine, frame


def _run(engine, source):
    engine._exec_body(parse_text(source).body)


def test_from_last_grammar_preserves_all_vision_forms():
    # Different dispatch contracts must all accept the common source clause.
    source = '''
scan [s].$field as $raw from last with "equip"
scan [s].[panel][1...2][1] as $cells from last by contains "20"
scan [s].[child].[field] as $child from last by image
recognize [s].[field] as rich $rich from last with parse
recognize [s].[panel][1][1] as $cell from last on group "g"
recognize [s].[child].[field] as $child from last by contains "20"
find [s].[right] as $found from last by contains "20" where confidence >= 0.8 with "equip"
find as $found from last by image "icon"
screenshot from last
screenshot
'''
    nodes = parse_text(source).body
    assert all(node.from_last for node in nodes[:-1])
    assert not nodes[-1].from_last
    assert nodes[6].cleaning_group == "equip"
    assert nodes[6].where.min_confidence.value == 0.8


def test_reuse_cross_region_recognition_and_screenshot_share_frame(tmp_path, monkeypatch):
    monkeypatch.setattr("lvjiang.workflows.engine.core.PROJECT_ROOT", tmp_path)
    engine, frame = _engine()
    records = []
    sink = logger.add(records.append, level="DEBUG")
    try:
        _run(engine, 'scan [s].[left] as $raw\n')
        original_time = engine._last_capture_time_ns
        _run(engine, '''
scan [s].[right] as $other from last
scan [s].[right] as $key from last by contains "20"
find [s].[right] as $found from last by contains "20"
recognize [s].[right] as $reference from last
screenshot from last
''')
    finally:
        logger.remove(sink)
    engine._capture.capture.assert_called_once()
    assert engine.variables["raw"] == {"left": "10"}
    assert engine.variables["other"] == {"right": "20"}
    assert engine.variables["key"] == "right"
    assert engine.variables["found"].text == "20"
    assert engine.variables["reference"] == {"right": "20"}
    assert engine.get_last_capture_frame() is frame
    assert engine._last_capture_time_ns == original_time
    assert engine.last_capture_seq == 1
    assert engine.last_capture_source == "ocr_scene"
    path, = (tmp_path / "logs" / "image").glob("*.png")
    np.testing.assert_array_equal(cv2.imread(str(path)), frame)
    saved = next(r for r in records if "screenshot: 已保存" in r.record["message"])
    assert saved.record["level"].name == "INFO"
    assert str(path.resolve()) in saved.record["message"]
    assert "frame_seq=1" in saved.record["message"]
    assert any("frame_seq=1 by OCR 命中" in r.record["message"]
               and r.record["level"].name == "DEBUG" for r in records)
    engine._capture.capture.return_value = np.full_like(frame, 40)
    _run(engine, 'scan [s].[right] as $fresh\n')
    assert engine.variables["fresh"] == {"right": "40"}
    assert engine.last_capture_seq == 2
    _run(engine, 'screenshot\n')
    assert engine.last_capture_seq == 3
    assert engine.last_capture_source == "screenshot"
    assert engine._capture.capture.call_count == 3
    assert len(list((tmp_path / "logs" / "image").glob("*.png"))) == 2


def test_panel_alignment_and_cells_reuse_original_frame():
    engine, _ = _engine()
    panel = SimpleNamespace(key="grid", rows=1, cols=2, calibration="even",
                            x_ratio=0, y_ratio=0, w_ratio=1, h_ratio=1)
    engine._layout.get_scene_panels.return_value = [panel]
    _run(engine, '''
scan [s].[left] as $raw
scan [s].[grid] as $whole from last
scan [s].[grid][1][2] as $cell from last
recognize [s].[grid][1][2] as $reference from last
''')
    engine._capture.capture.assert_called_once()
    assert engine.variables["whole"] == {"1": {"1": "10", "2": "20"}}
    assert engine.variables["cell"] == "20"
    assert engine.variables["reference"] == "20"


def test_missing_frame_is_catchable_and_does_not_recapture():
    engine, _ = _engine()
    _run(engine, '''
try
    scan [s].[right] as $raw from last
catch $err
    eval $handled = 1
end
''')
    engine._capture.capture.assert_not_called()
    assert "没有可复用截图" in engine.variables["err"]
    assert engine.variables["handled"] == 1


def test_failed_capture_and_new_run_invalidate_previous_frame(tmp_path):
    engine, _ = _engine()
    engine.capture_frame(source="seed")
    engine._capture.capture.return_value = None
    assert engine.capture_frame(source="failed") is None
    with pytest.raises(WorkflowUserError, match="没有可复用截图"):
        _run(engine, 'find as $found from last by contains "20"\n')
    engine._capture.capture.return_value = np.zeros((20, 40, 3), dtype=np.uint8)
    engine.capture_frame(source="seed")
    engine._capture.capture.side_effect = RuntimeError("capture failed")
    with pytest.raises(RuntimeError, match="capture failed"):
        engine.capture_frame(source="failed")
    assert engine.get_last_capture_frame() is None
    engine._capture.capture.side_effect = None
    engine._capture.capture.return_value = np.zeros((20, 40, 3), dtype=np.uint8)
    engine.capture_frame(source="seed")
    script = tmp_path / "new_run.wf"
    script.write_text('scan [s].[left] as $raw from last\n', encoding="utf-8")
    with pytest.raises(WorkflowUserError, match="没有可复用截图"):
        engine.execute(script)
    assert engine.get_last_capture_frame() is None


def test_recognition_error_restores_capture_source():
    engine, frame = _engine()
    engine.capture_frame(source="seed")
    engine._ocr.ocr_scene_regions.side_effect = ValueError("OCR failed")
    _run(engine, '''
try
    scan [s].[left] as $raw from last
catch $err
    eval $handled = 1
end
''')
    fresh = np.full_like(frame, 30)
    engine._capture.capture.return_value = fresh
    assert engine.capture_frame(source="fresh") is fresh
    assert engine.last_capture_seq == 2


def test_screenshot_failure_is_nonfatal_and_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr("lvjiang.workflows.engine.core.PROJECT_ROOT", tmp_path)
    engine, _ = _engine()
    _run(engine, 'screenshot from last\neval $continued = 1\n')
    engine._capture.capture.assert_not_called()
    assert not (tmp_path / "logs").exists()
    engine.capture_frame(source="seed")
    monkeypatch.setattr("lvjiang.workflows.engine.core.cv2.imwrite", lambda *args: False)
    _run(engine, 'screenshot from last\neval $continued = 2\n')
    assert engine.variables["continued"] == 2
    engine._capture.capture.assert_called_once()
    assert not list((tmp_path / "logs" / "image").glob("*.png"))


def test_menu_navigation_records_failed_observation_before_pause(tmp_path, monkeypatch):
    monkeypatch.setattr("lvjiang.workflows.engine.core.PROJECT_ROOT", tmp_path)
    engine, frame = _engine()
    engine._layout.get_scene_regions.return_value = [
        Region(key="baoguo", x_ratio=0, y_ratio=0, w_ratio=0.5, h_ratio=1),
        Region(key="peiyang", x_ratio=0.5, y_ratio=0, w_ratio=0.5, h_ratio=1),
    ]
    root = Path(__file__).parents[2] / "config" / "system" / "workflows" / "subcall"
    for name in ("page_detection.wf", "navigation.wf"):
        engine._procs.update(parse_text((root / name).read_text(encoding="utf-8")).procs)
    monkeypatch.setattr(engine, "_exec_click", MagicMock())
    monkeypatch.setattr(engine, "_exec_wait", MagicMock())
    pauses = []

    def pause(action, **kwargs):
        assert action == "pause"
        image, = (tmp_path / "logs" / "image").glob("*.png")
        np.testing.assert_array_equal(cv2.imread(str(image)), frame)
        pauses.append(kwargs["message"])

    engine._ui_callback = pause
    _run(engine, 'call $result = nav_main_to_menu()\n')
    assert engine.variables["result"] == -1
    assert len(pauses) == 1
    assert engine._capture.capture.call_count == 3
    engine._exec_click.assert_called_once()


def test_template_scan_and_find_reuse_original_frame(tmp_path, monkeypatch):
    engine, frame = _engine()
    icon = np.random.default_rng(0).integers(0, 255, (8, 8, 3), dtype=np.uint8)
    frame[6:14, 24:32] = icon
    assert cv2.imwrite(str(tmp_path / "icon.png"), icon)
    store = template_locator.TemplateStore(tmp_path)
    monkeypatch.setattr(template_locator, "get_template_store", lambda: store)
    engine._layout.get_scene_regions.return_value[1].template = TemplateBinding(name="icon")
    _run(engine, '\n'.join([
        'scan [s].[left] as $raw',
        'scan [s].[right] as $hit from last by image',
        'find [s].[right] as $found from last by image "icon"',
        '',
    ]))
    engine._capture.capture.assert_called_once()
    assert engine.variables["hit"] == "right"
    assert engine.variables["found"].text == "icon"
    assert engine.last_capture_seq == 1


def test_equipment_cooldown_lock_and_detail_use_one_frame(monkeypatch):
    # Register the real domain builtin; fake only its pixel classifier.
    import lvjiang.apps.yysls.workflows.builtins.equipment  # noqa: F401

    engine, frame = _engine()
    script = (Path(__file__).parents[2] / "config" / "system" / "workflows"
              / "subcall" / "loadout" / "equipment_scan.wf")
    engine._procs.update(parse_text(script.read_text(encoding="utf-8")).procs)
    refs = collect_refs([], engine._procs, reachable_only=False)
    engine._layout.get_scene_regions.side_effect = lambda scene: [
        Region(key=key, x_ratio=0, y_ratio=0, w_ratio=1, h_ratio=1)
        for key in sorted({ref.key for ref in refs if ref.scene == scene and ref.key}
                          | ({"lock"} if scene == "equip_detail" else set()))]

    def ocr(img, canvas, regions, scene, **kwargs):
        assert img is frame
        return {r.key: "冷却" if r.key == "cooldown_tips" else r.key for r in regions}

    engine._ocr.ocr_scene_regions.side_effect = ocr
    lock_crops = []

    def classify(crop):
        np.testing.assert_array_equal(crop, frame)
        lock_crops.append(crop)
        return "locked"

    monkeypatch.setattr(
        "lvjiang.apps.yysls.core.equip_parser.lock_state.classify_lock_status", classify)
    _run(engine, 'call $raw = scan_equipment_detail("equip_weapon_detail")\n')
    engine._capture.capture.assert_called_once()
    assert engine.variables["raw"]["affix_gong"] == "cooldown_affix_gong"
    assert engine.variables["raw"]["equip_detail"] == "equip_detail"
    assert engine.variables["raw"]["lock_status"] == "locked"
    assert len(lock_crops) == 1
    assert engine.last_capture_seq == 1
