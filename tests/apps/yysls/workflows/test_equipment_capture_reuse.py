"""装备详情扫描（yysls 内置函数 + 锁定状态分类）复用同一帧截图。

宿主的截图复用机制由 ``tests/workflows/test_capture_reuse.py`` 覆盖。
"""
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from lvjiang.core.layout.models import Region
from lvjiang.workflows.grammar import parse_text
from lvjiang.workflows.workflow_references import collect_refs
from tests.workflows.test_capture_reuse import _engine, _run


@pytest.mark.parametrize("fail_first", [False, True])
def test_equipment_cooldown_lock_and_detail_use_one_frame(monkeypatch, fail_first):
    # Register the real domain builtin; fake only its pixel classifier.
    import lvjiang.apps.yysls.workflows.builtins.equipment  # noqa: F401

    engine, frame = _engine()
    engine.wait_seconds = MagicMock()
    if fail_first:
        engine._capture.capture.side_effect = [None, frame]
    script = (Path(__file__).parents[4] / "config" / "system" / "workflows"
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
    assert engine._capture.capture.call_count == (2 if fail_first else 1)
    assert engine.variables["raw"]["affix_gong"] == "cooldown_affix_gong"
    assert engine.variables["raw"]["equip_detail"] == "equip_detail"
    assert engine.variables["raw"]["lock_status"] == "locked"
    assert len(lock_crops) == 1
    assert engine.last_capture_seq == 1
