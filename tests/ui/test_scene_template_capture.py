"""模板始终截取完整区域，点击落点不能影响截取范围。"""

import numpy as np

from lvjiang.core.layout_models import Region
from lvjiang.ui.scene_editor.canvas import RegionCanvas


def test_template_capture_uses_whole_region_and_preserves_editor_mode(qtbot):
    canvas = RegionCanvas()
    qtbot.addWidget(canvas)
    image = np.arange(100 * 200 * 3, dtype=np.uint8).reshape(100, 200, 3)
    canvas.set_image(image)
    canvas.set_regions([
        Region("icon", 0.2, 0.3, 0.4, 0.5, click_rect=(0.25, 0.25, 0.5, 0.5))
    ])
    canvas.select_region(0)
    captured = []
    canvas.on_template_crop_ready = lambda *args: captured.append(args)
    mode = canvas._edit_mode

    assert canvas.capture_selected_region_template()
    key, crop, width, height = captured[0]
    assert key == "icon"
    np.testing.assert_array_equal(crop, image[30:80, 40:120])
    assert (width, height) == (200, 100)
    assert canvas._edit_mode == mode
