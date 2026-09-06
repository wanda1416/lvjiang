"""OCR 对跨场景引用区域的定义解析。"""

import numpy as np

from lvjiang.core.layout_models import CanvasConfig, Region
from lvjiang.core.ocr import OCREngine, OCRResult
from lvjiang.core.scene_definition_models import RegionDef


def test_ocr_scene_regions_includes_referenced_text_definition(monkeypatch):
    monkeypatch.setattr(
        "lvjiang.core.ocr.get_effective_region_defs",
        lambda _scene: [
            RegionDef(
                key="shared_text",
                name="共享字段",
                type="attr",
                is_text=True,
            ),
        ],
    )
    engine = OCREngine()
    monkeypatch.setattr(
        engine,
        "recognize",
        lambda _crop: [OCRResult("识别结果", 1.0, [])],
    )
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    region = Region(
        "shared_text", 0.1, 0.1, 0.2, 0.2,
        source_scene="shared",
    )

    result = engine.ocr_scene_regions(
        image, CanvasConfig(), [region], "target")

    assert result == {"shared_text": "识别结果"}
