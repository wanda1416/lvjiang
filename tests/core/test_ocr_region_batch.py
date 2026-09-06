"""OCR 多区域拼图批量识别。"""

import numpy as np

from lvjiang.core.layout_models import CanvasConfig, Region
from lvjiang.core.ocr import OCREngine, OCRResult
from lvjiang.core.scene_definition_models import RegionDef


def _bbox(x1: int, y1: int, x2: int, y2: int) -> list[tuple[int, int]]:
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def _patch_text_defs(monkeypatch, *keys: str) -> None:
    monkeypatch.setattr(
        "lvjiang.core.ocr.get_effective_region_defs",
        lambda _scene: [
            RegionDef(key=key, name=key, type="attr", is_text=True)
            for key in keys
        ],
    )


def test_multiple_regions_share_one_ocr_call_and_restore_fields(monkeypatch):
    _patch_text_defs(monkeypatch, "first", "second")
    engine = OCREngine()
    calls = []

    def recognize(sheet):
        calls.append(sheet.shape)
        # first 高 10，间隔 16，second 从 y=26 开始。返回顺序故意打乱。
        return [
            OCRResult("乙", 0.9, _bbox(1, 29, 8, 34)),
            OCRResult("甲二", 0.9, _bbox(8, 4, 15, 8)),
            OCRResult("甲一", 0.9, _bbox(1, 2, 7, 6)),
        ]

    monkeypatch.setattr(engine, "recognize", recognize)
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    regions = [
        Region("first", 0.0, 0.0, 0.2, 0.1),
        Region("second", 0.0, 0.2, 0.3, 0.1),
    ]

    result = engine.ocr_scene_regions(
        image, CanvasConfig(), regions, "scene")

    assert len(calls) == 1
    assert calls[0] == (736, 736, 3)
    assert result == {"first": "甲一 甲二", "second": "乙"}


def test_batch_preserves_line_breaks_inside_region(monkeypatch):
    _patch_text_defs(monkeypatch, "multi", "other")
    engine = OCREngine()
    monkeypatch.setattr(
        engine,
        "recognize",
        lambda _sheet: [
            OCRResult("第二行", 1.0, _bbox(1, 12, 9, 16)),
            OCRResult("第一行", 1.0, _bbox(1, 1, 9, 5)),
        ],
    )
    image = np.zeros((100, 100, 3), dtype=np.uint8)

    result = engine.ocr_scene_regions(
        image,
        CanvasConfig(),
        [
            Region("multi", 0, 0, 0.2, 0.2),
            Region("other", 0, 0.5, 0.2, 0.2),
        ],
        "scene",
    )

    assert result == {"multi": "第一行 | 第二行", "other": ""}


def test_batch_uses_maximum_bbox_overlap_and_confidence(monkeypatch):
    _patch_text_defs(monkeypatch, "first", "second")
    engine = OCREngine()

    monkeypatch.setattr(
        engine,
        "recognize",
        lambda _sheet: [
            # 跨过 gap，但与 second 的重叠面积更大。
            OCRResult("归第二区", 0.95, _bbox(0, 8, 20, 32)),
            OCRResult("低置信度", 0.2, _bbox(0, 1, 10, 8)),
        ],
    )
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    regions = [
        Region("first", 0.0, 0.0, 0.2, 0.1),
        Region("second", 0.0, 0.2, 0.2, 0.1),
    ]

    result = engine.ocr_scene_regions(
        image, CanvasConfig(), regions, "scene", min_confidence=0.5)

    assert result == {"first": "", "second": "归第二区"}


def test_large_region_set_is_split_into_bounded_batches(monkeypatch):
    _patch_text_defs(monkeypatch, "a", "b", "c")
    engine = OCREngine()
    shapes = []

    def recognize(sheet):
        shapes.append(sheet.shape)
        return []

    monkeypatch.setattr(engine, "recognize", recognize)
    image = np.zeros((1800, 100, 3), dtype=np.uint8)
    regions = [
        Region("a", 0, 0 / 1800, 1, 500 / 1800),
        Region("b", 0, 600 / 1800, 1, 500 / 1800),
        Region("c", 0, 1200 / 1800, 1, 500 / 1800),
    ]

    result = engine.ocr_scene_regions(
        image, CanvasConfig(), regions, "scene")

    assert len(shapes) == 2
    assert all(height <= 1200 for height, _width, _channels in shapes)
    assert result == {"a": "", "b": "", "c": ""}


def test_single_region_keeps_direct_recognition_path(monkeypatch):
    _patch_text_defs(monkeypatch, "only")
    engine = OCREngine()
    shapes = []

    def recognize(crop):
        shapes.append(crop.shape)
        return [OCRResult("单区域", 1.0, [])]

    monkeypatch.setattr(engine, "recognize", recognize)
    image = np.zeros((100, 100, 3), dtype=np.uint8)

    result = engine.ocr_scene_regions(
        image,
        CanvasConfig(),
        [Region("only", 0.1, 0.2, 0.3, 0.4)],
        "scene",
    )

    assert shapes == [(40, 30, 3)]
    assert result == {"only": "单区域"}
