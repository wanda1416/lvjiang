"""视图管理展示页面切换契约：入口与转移。"""
from __future__ import annotations

from PyQt6.QtWidgets import QScrollArea

from lvjiang.core.scene_definition_models import (
    RegionDef,
    SceneDef,
    ViewDef,
)
from lvjiang.ui.scene_editor.scene_view_dialog import ViewManagerDialog


class _FakeRegistry:
    def __init__(self, scenes):
        self._scenes = scenes

    def all_scenes(self):
        return self._scenes

    def get_scene_views(self, key):
        scene = self._scenes.get(key)
        return list(scene.views) if scene else []

    def get_scene(self, key):
        return self._scenes.get(key)

    def save_scene_views(self, key):
        self.saved = key


def _scenes():
    return {
        "bag": SceneDef(key="bag", name="背包",
                        views=[ViewDef("base", "基底")],
                        regions=[RegionDef(key="tune", name="调律",
                                           is_clickable=True,
                                           to="tune/result"),
                                 RegionDef(key="open_single", name="打开单页",
                                           is_clickable=True,
                                           to="single")]),
        "single": SceneDef(key="single", name="单页"),
        "tune": SceneDef(
            key="tune", name="调律",
            views=[ViewDef("base", "基底"),
                   ViewDef("result", "结果", same_layer=False),
                   ViewDef("return_good", "狗粮返还", same_layer=False)],
            regions=[RegionDef(key="close_btn", name="关闭",
                               is_clickable=True, views=["result"],
                               to="/base")]),
    }


def _dialog(qtbot, monkeypatch, scene_key="tune"):
    registry = _FakeRegistry(_scenes())
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.scene_view_dialog.get_registry",
        lambda: registry)
    dlg = ViewManagerDialog(scene_key)
    qtbot.addWidget(dlg)
    return dlg


def _select(dlg, view_key):
    for row in range(dlg._list.count()):
        item = dlg._list.item(row)
        if item is not None and item.data(0x0100) == view_key:
            dlg._list.setCurrentRow(row)
            return
    raise AssertionError(f"视图不在列表里: {view_key}")


def _lines(layout):
    return [layout.itemAt(i).widget().text() for i in range(layout.count())]


def _contract_text(dlg):
    return "\n".join(_lines(dlg._entry_lines) + _lines(dlg._exit_lines))


def test_view_with_an_entry_shows_where_it_comes_from(qtbot, monkeypatch):
    dlg = _dialog(qtbot, monkeypatch)
    assert dlg.windowTitle() == "视图管理"
    _select(dlg, "result")

    assert _lines(dlg._entry_lines) == ["背包 / 基底 · 调律"]
    assert _lines(dlg._exit_lines) == ["关闭 → 调律 / 基底"]
    # 主界面只显示名称，key 放 tooltip 供排查。
    assert dlg._entry_lines.itemAt(0).widget().toolTip() == "bag [tune]"


def test_selected_view_has_explicit_readable_colours(qtbot, monkeypatch):
    """局部列表样式不能把全局主题的选中背景或文字色覆盖掉。"""
    dlg = _dialog(qtbot, monkeypatch)

    style = dlg._list.styleSheet()
    assert "QListWidget::item:selected" in style
    assert "background-color: palette(highlight)" in style
    assert "color: palette(highlighted-text)" in style


def test_view_without_an_entry_is_flagged_as_dead(qtbot, monkeypatch):
    """没有任何 to: 指过来的非基底视图 = 死视图，视图管理要直说。"""
    dlg = _dialog(qtbot, monkeypatch)
    _select(dlg, "return_good")

    assert "死视图" in _contract_text(dlg)


def test_base_view_without_incoming_edges_is_called_the_scene_entry(
        qtbot, monkeypatch):
    """基底没有入边时说"场景入口"，而不是判成死视图。"""
    dlg = _dialog(qtbot, monkeypatch, scene_key="bag")
    _select(dlg, "base")

    text = _contract_text(dlg)
    assert "死视图" not in text
    assert "场景入口" in text
    # 它自己的出边照常展示
    assert "调律 → 调律 / 结果" in text


def test_base_view_with_incoming_edges_shows_them(qtbot, monkeypatch):
    """基底有入边时就展示真实入口，不用兜底文案盖掉。

    tune/base 被 result 视图的 close_btn 指回来，属于这种情况。
    """
    dlg = _dialog(qtbot, monkeypatch)
    _select(dlg, "base")

    text = _contract_text(dlg)
    assert "死视图" not in text
    assert "场景入口" not in text
    assert "关闭" in text


def test_same_layer_view_is_not_flagged_as_dead(qtbot, monkeypatch):
    """同层视图（如菜单翻页）没有入口是正常的，不该报死视图。"""
    registry = _FakeRegistry({
        "menu": SceneDef(key="menu", name="菜单",
                         views=[ViewDef("base", "基底"),
                                ViewDef("page_2", "第二屏")])})
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.scene_view_dialog.get_registry",
        lambda: registry)
    dlg = ViewManagerDialog("menu")
    qtbot.addWidget(dlg)
    _select(dlg, "page_2")

    text = _contract_text(dlg)
    assert "死视图" not in text
    assert "同层视图" in text
    assert dlg._cb_same_layer.isChecked()


def test_single_view_is_exposed_as_base_and_shows_its_entries(
        qtbot, monkeypatch):
    dlg = _dialog(qtbot, monkeypatch, scene_key="single")

    assert dlg._selected_view_key() == "base"
    assert dlg._list.currentItem().text() == "基底  （单视图）"
    assert _lines(dlg._entry_lines) == ["背包 / 基底 · 打开单页"]


def test_each_entry_and_exit_uses_its_own_label(qtbot, monkeypatch):
    scenes = _scenes()
    scenes["bag"].regions.append(
        RegionDef(key="tune_again", name="再次调律", is_clickable=True,
                  to="tune/result"))
    scenes["tune"].regions.append(
        RegionDef(key="open_bag", name="打开背包", is_clickable=True,
                  views=["result"], to="bag"))
    registry = _FakeRegistry(scenes)
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.scene_view_dialog.get_registry",
        lambda: registry)
    dlg = ViewManagerDialog("tune")
    qtbot.addWidget(dlg)
    _select(dlg, "result")

    assert _lines(dlg._entry_lines) == [
        "背包 / 基底 · 调律", "背包 / 基底 · 再次调律"]
    assert _lines(dlg._exit_lines) == [
        "关闭 → 调律 / 基底", "打开背包 → 背包 / 基底"]


def test_contract_is_plain_labels_without_text_area(qtbot, monkeypatch):
    dlg = _dialog(qtbot, monkeypatch)

    assert dlg.findChildren(QScrollArea) == []
    assert dlg._contract.styleSheet() == ""


def test_refresh_removes_old_contract_labels_immediately(qtbot, monkeypatch):
    dlg = _dialog(qtbot, monkeypatch)
    _select(dlg, "result")
    old_entry = dlg._entry_lines.itemAt(0).widget()

    _select(dlg, "return_good")

    assert old_entry.parent() is None
    assert not old_entry.isVisible()


def test_unchecking_same_layer_drops_the_tag_from_the_list_row(
        qtbot, monkeypatch):
    """取消勾选同层后列表行要立刻移除「· 同层」，不能停在勾选前的文案。"""
    scenes = _scenes()
    scenes["tune"].views[1].same_layer = True
    registry = _FakeRegistry(scenes)
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.scene_view_dialog.get_registry",
        lambda: registry)
    dlg = ViewManagerDialog("tune")
    qtbot.addWidget(dlg)
    _select(dlg, "result")
    assert "· 同层" in dlg._list.currentItem().text()

    dlg._cb_same_layer.setChecked(False)

    assert dlg._list.currentItem().data(0x0100) == "result"
    assert "· 同层" not in dlg._list.currentItem().text()

    dlg._cb_same_layer.setChecked(True)

    assert "· 同层" in dlg._list.currentItem().text()
