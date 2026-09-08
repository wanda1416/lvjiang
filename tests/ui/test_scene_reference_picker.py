"""跨场景引用的批量选择器：分组 / 场景 / 视图筛选 + 多选。

一次要引十几个 area 是常态（通用控件、公共弹窗），所以验的是「筛得准、
选得动、一次给回一批」，不是控件长什么样。
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt

pytestmark = pytest.mark.usefixtures("qapp")


class _Area:
    def __init__(self, key: str, name: str, views: list[str], region: bool):
        self.key, self.name, self.views = key, name, views
        if region:
            self.is_clickable = True


class _Scene:
    def __init__(self, name, regions=(), points=(), is_subscene=False):
        self.name = name
        self.regions = list(regions)
        self.points = list(points)
        self.is_subscene = is_subscene


class _Registry:
    def __init__(self, scenes: dict):
        self._scenes = scenes

    def all_scenes(self):
        return self._scenes

    def get_scene(self, key):
        return self._scenes.get(key)

    def get_groups(self):
        return [("main", "主分组")]

    def get_group_scenes(self, _group_key):
        return list(self._scenes)


@pytest.fixture
def picker_factory(monkeypatch):
    import lvjiang.ui.scene_editor.scene_select as module

    scenes = {
        "dst": _Scene("目标"),
        "general": _Scene(
            "通用控件",
            regions=[_Area("confirm", "确认", ["base"], True),
                     _Area("cancel", "取消", ["reset"], True)],
            points=[_Area("anchor", "锚点", ["base"], False)],
        ),
        "card": _Scene("子场景卡片",
                       regions=[_Area("title", "标题", [], True)],
                       is_subscene=True),
    }
    registry = _Registry(scenes)
    monkeypatch.setattr(module, "get_registry", lambda: registry)
    monkeypatch.setattr(
        module, "get_scene_views",
        lambda key: [] if key != "general" else [
            type("V", (), {"key": "base", "name": "基底"})(),
            type("V", (), {"key": "reset", "name": "重置"})(),
        ])

    def make(taken=frozenset()):
        return module.SceneAreaReferenceBatchPicker("dst", set(taken))

    return make


def _labels(picker) -> list[str]:
    return [picker.items.item(i).text() for i in range(picker.items.count())]


def _check(picker, index: int) -> None:
    picker.items.item(index).setCheckState(Qt.CheckState.Checked)


def test_regions_and_points_are_listed_together(picker_factory) -> None:
    """两者本质都是 area，add_scene_reference 一视同仁，拆成两处只会让人来回切。"""
    picker = picker_factory()
    picker.scene.setCurrentIndex(picker.scene.findData("general"))

    labels = _labels(picker)
    assert any("确认" in text and "区域" in text for text in labels)
    assert any("锚点" in text and "坐标" in text for text in labels)


def test_the_view_filter_narrows_the_list(picker_factory) -> None:
    picker = picker_factory()
    picker.scene.setCurrentIndex(picker.scene.findData("general"))
    picker.view.setCurrentIndex(picker.view.findData("reset"))

    assert [text.split()[-1] for text in _labels(picker)] == ["cancel"]


def test_subscenes_are_never_offered(picker_factory) -> None:
    """子场景坐标相对外框，搬过来要做变换，是另一回事。"""
    picker = picker_factory()

    assert picker.scene.findData("card") < 0


def test_keys_already_taken_by_this_scene_are_not_offered(picker_factory) -> None:
    """引用项的 key 恒等于源实体 key，改不了，同名就会抢命名空间。"""
    picker = picker_factory(taken={"confirm"})
    picker.scene.setCurrentIndex(picker.scene.findData("general"))

    assert all("confirm" not in text for text in _labels(picker))


def test_checked_entries_come_back_as_a_batch(picker_factory) -> None:
    picker = picker_factory()
    picker.scene.setCurrentIndex(picker.scene.findData("general"))
    for index in range(picker.items.count()):
        _check(picker, index)

    assert picker.values() == [
        ("general", "confirm"), ("general", "cancel"), ("general", "anchor")]


def test_select_all_and_none_cover_the_filtered_list(picker_factory) -> None:
    picker = picker_factory()
    picker.scene.setCurrentIndex(picker.scene.findData("general"))

    picker._set_all(True)
    assert len(picker.values()) == 3
    picker._set_all(False)
    assert picker.values() == []
