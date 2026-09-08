"""画布网格面板标签。"""

from lvjiang.core.layout_models import Panel
from lvjiang.ui.scene_editor.canvas import RegionCanvas


def test_panel_label_uses_scene_name_instead_of_key(qtbot, monkeypatch):
    canvas = RegionCanvas()
    qtbot.addWidget(canvas)
    canvas.set_scene_key("bag_equip_detail")
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.canvas.get_panel_name",
        lambda scene_key, panel_key: "背包网格",
    )
    panel = Panel("bag_grid", 0.1, 0.1, 0.5, 0.5)

    assert canvas._panel_label(panel) == "背包网格 (3x6)"
