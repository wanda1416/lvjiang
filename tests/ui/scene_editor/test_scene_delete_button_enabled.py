"""场景编辑器各 Tab 的删除按钮必须跟着选中状态走。

删除是高风险动作：没有选中项时按钮却是亮着的红色，按下去要么无声无息，要么弹
一个「请先选择」——按 UI 约定应当禁用并由 tooltip 说明原因，而不是等用户点了
再拦。区域和网格一直是这个行为，坐标、方向、引用三处漏了。

这里用真实 mixin 组出宿主，所以验的是「回调确实接上了更新逻辑」，而不只是
「存在一个更新方法」：三个回调都在 row < 0 时提前 return，更新必须发生在
return 之前，否则取消选中后按钮会一直亮着。
"""
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QPushButton

from lvjiang.ui.scene_editor.entity_order_table import EntityOrderTable
from lvjiang.ui.scene_editor.scene_poi_panel import PoiPanelMixin
from lvjiang.ui.scene_editor.scene_reference_editor import (
    SceneReferenceEditorMixin,
)
from lvjiang.ui.scene_editor.scene_region_panel import RegionPanelMixin


class _Host(PoiPanelMixin, SceneReferenceEditorMixin, RegionPanelMixin):
    """只带这几个用例需要的协作者，其余依赖由各 mixin 的契约另行保证。"""

    def __init__(self, qtbot, rows: int):
        for attr in ("_point_list", "_arrow_list",
                     "_reference_table", "_region_table"):
            table = EntityOrderTable(rows, 10)
            qtbot.addWidget(table)
            setattr(self, attr, table)
        for attr in ("_btn_del_point", "_btn_del_arrow",
                     "_btn_delete_reference", "_btn_del_region"):
            button = QPushButton()
            qtbot.addWidget(button)
            button.setEnabled(True)   # 起点设为可用，才看得出是谁收回的
            setattr(self, attr, button)
        self._canvas = SimpleNamespace(clear_poi_selection=lambda: None)

    def _selected_reference(self):
        """区域删除按钮按「本地定义 / 跨场景引用」改写文案，这里只走本地分支。"""
        return None


# (选中回调, 调用参数, 按钮属性名, 表格属性名)
CASES = [
    (PoiPanelMixin._on_point_selection, (-1,),
     "_btn_del_point", "_point_list"),
    (PoiPanelMixin._on_arrow_selection, (-1,),
     "_btn_del_arrow", "_arrow_list"),
    (SceneReferenceEditorMixin._on_reference_selection, (-1, 0, 0, 0),
     "_btn_delete_reference", "_reference_table"),
    (RegionPanelMixin._on_region_table_selection, (-1, 0, 0, 0),
     "_btn_del_region", "_region_table"),
]
_IDS = ["point", "arrow", "reference", "region"]


@pytest.mark.parametrize("handler, args, btn_attr, _table_attr",
                         CASES, ids=_IDS)
def test_delete_button_is_disabled_without_a_selection(
    qtbot, handler, args, btn_attr, _table_attr,
):
    host = _Host(qtbot, rows=0)

    handler(host, *args)

    assert getattr(host, btn_attr).isEnabled() is False


@pytest.mark.parametrize("handler, args, btn_attr, table_attr",
                         CASES, ids=_IDS)
def test_delete_button_is_enabled_once_a_row_is_selected(
    qtbot, handler, args, btn_attr, table_attr,
):
    host = _Host(qtbot, rows=2)
    getattr(host, btn_attr).setEnabled(False)
    getattr(host, table_attr).selectRow(1)

    handler(host, 1, *args[1:])

    assert getattr(host, btn_attr).isEnabled() is True
