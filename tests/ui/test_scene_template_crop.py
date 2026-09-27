"""子场景录制使用子画布尺寸，编辑器测试与运行时使用同一模板口径。"""

import numpy as np

from lvjiang.core.layout_models import CanvasConfig, Region, TemplateBinding
from lvjiang.core.recognizers.template_locator import (
    locate_in_region,
    template_from_image,
)
from lvjiang.ui.scene_editor.canvas import RegionCanvas
from lvjiang.ui.scene_editor.scene_tab import SceneTab


def test_subscene_crop_records_child_canvas_size(qtbot):
    canvas = RegionCanvas()
    qtbot.addWidget(canvas)
    frame = np.random.default_rng(7).integers(
        0, 256, size=(360, 640, 3), dtype=np.uint8)
    canvas.set_image(frame)
    child_canvas = CanvasConfig(0.25, 0.2, 0.25, 160 / 360)
    canvas.set_canvas_config(child_canvas)
    region = Region("refresh", 0.5, 0.25, 0.25, 0.25)
    recorded = []
    canvas.on_template_crop_ready = lambda *args: recorded.append(args)

    assert canvas._emit_template_crop(region, 0, 0, 1, 1)

    _, crop, record_w, record_h = recorded[0]
    assert (record_w, record_h) == (160, 160)
    template = template_from_image("child/refresh", crop, record_w, record_h)
    hit = locate_in_region(frame, template, child_canvas, region, 0.8)
    assert hit is not None and hit.scale == 1.0 and hit.score > 0.99


def test_recropping_preserves_inverted_template_option(qtbot):
    tab = SceneTab("jianghu_card")
    qtbot.addWidget(tab)
    tab.set_layout_name("desktop", "layouts/desktop/jianghu_card.json")
    tab.set_regions([
        Region("refresh", 0.1, 0.1, 0.2, 0.2,
               template=TemplateBinding("desktop/jianghu_card/refresh",
                                        0.8, 542, 250, True)),
    ])
    tab.canvas.select_region(0)
    crop = np.random.default_rng(7).integers(
        0, 256, size=(25, 26, 3), dtype=np.uint8)

    tab._on_template_crop_ready("refresh", crop, 542, 250)

    assert tab.canvas.selected_region().template.allow_inverted is True
