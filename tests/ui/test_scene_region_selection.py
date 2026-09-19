"""场景区域列表与画布单区域展示状态同步。"""

from types import SimpleNamespace

from PyQt6.QtWidgets import QTableWidgetItem

from lvjiang.core.layout_models import Region
from lvjiang.ui.scene_editor import scene_region_panel
from lvjiang.ui.scene_editor.entity_order_table import EntityOrderTable
from lvjiang.ui.scene_editor.scene_region_panel import RegionPanelMixin


class _Canvas:
    def __init__(self, selected_key: str | None):
        self.selected_key = selected_key

    def selected_region_key(self) -> str | None:
        return self.selected_key

    def get_regions(self) -> list[Region]:
        return [Region("target", 0.1, 0.1, 0.2, 0.2)]

    def get_disabled_keys(self, _kind: str) -> set[str]:
        return set()


def _host(qtbot, monkeypatch, *, canvas_key: str | None):
    table = EntityOrderTable(1, 10)
    qtbot.addWidget(table)
    table.setItem(0, 1, QTableWidgetItem("target"))
    table.selectRow(0)

    region_def = SimpleNamespace(
        key="target", name="目标区域", type="text",
        is_text=True, is_clickable=False, to="", views=[],
    )
    scene = SimpleNamespace(regions=[region_def], references=[])
    monkeypatch.setattr(
        scene_region_panel, "get_registry",
        lambda: SimpleNamespace(get_scene=lambda _key: scene),
    )
    return SimpleNamespace(
        _region_table=table,
        _canvas=_Canvas(canvas_key),
        _scene_key="scene",
        _current_view="",
        _append_reference_rows=lambda *_args: None,
        _update_region_delete_button=lambda: None,
        _refresh_template_controls=lambda: None,
    )


def test_canvas_clear_does_not_restore_stale_table_row(qtbot, monkeypatch):
    host = _host(qtbot, monkeypatch, canvas_key=None)

    RegionPanelMixin._refresh_region_list(
        host, preserve_current=False)

    assert host._region_table.selectionModel().selectedRows() == []


def test_active_canvas_selection_survives_toolbar_focus_change(
    qtbot, monkeypatch,
):
    host = _host(qtbot, monkeypatch, canvas_key="target")

    RegionPanelMixin._refresh_region_list(
        host, preserve_current=False)

    selected = host._region_table.selectionModel().selectedRows()
    assert [index.row() for index in selected] == [0]


def test_data_refresh_still_preserves_current_table_row(qtbot, monkeypatch):
    host = _host(qtbot, monkeypatch, canvas_key=None)

    RegionPanelMixin._refresh_region_list(host)

    selected = host._region_table.selectionModel().selectedRows()
    assert [index.row() for index in selected] == [0]
