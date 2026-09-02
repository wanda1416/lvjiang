"""场景编辑器区域列表的布局按键展示。"""

from lvjiang.core.layout_models import Region
from lvjiang.ui.scene_editor.scene_tab import SceneTab


def test_region_table_shows_layout_activation_key(qtbot):
    tab = SceneTab("general_control")
    qtbot.addWidget(tab)
    tab.set_regions([
        Region(
            key="confirm",
            x_ratio=0.1,
            y_ratio=0.1,
            w_ratio=0.2,
            h_ratio=0.2,
            activation_key="space",
        ),
    ])

    headers = [
        tab._region_table.horizontalHeaderItem(col).text()
        for col in range(tab._region_table.columnCount())
    ]
    assert headers == ["名称", "Key", "类型", "含文本", "可点击", "按键", "禁用",
                       "跳转", "来源"]

    row = next(
        row
        for row in range(tab._region_table.rowCount())
        if tab._region_table.item(row, 1).text() == "confirm"
    )
    assert tab._region_table.item(row, 5).text() == "SPACE"
    assert tab._region_table.cellWidget(row, 6) is not None
