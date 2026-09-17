"""场景编辑器右侧 Tab 的实体计数与尺寸。"""

from types import SimpleNamespace

from lvjiang.ui.scene_editor import scene_tab
from lvjiang.ui.scene_editor.scene_tab import SceneTab, _UniformWidthTabBar


class _TabTextRecorder:
    def __init__(self):
        self.texts: dict[int, str] = {}

    def setTabText(self, index: int, text: str) -> None:  # noqa: N802 - Qt API
        self.texts[index] = text


def test_entity_tab_counts_include_valid_references(monkeypatch):
    refs = [
        SimpleNamespace(scene="source", entity="shared_region"),
        SimpleNamespace(scene="source", entity="shared_point"),
        SimpleNamespace(scene="missing", entity="broken"),
    ]
    scene = SimpleNamespace(
        regions=[object(), object()],
        points=[object()],
        panels=[object()],
        subscene_refs=[object(), object()],
        references=refs,
    )
    registry = SimpleNamespace(get_scene=lambda _key: scene)
    monkeypatch.setattr(scene_tab, "get_registry", lambda: registry)
    monkeypatch.setattr(
        scene_tab, "get_region_def",
        lambda _scene, entity: object() if entity == "shared_region" else None,
    )
    monkeypatch.setattr(
        scene_tab, "get_point_def",
        lambda _scene, entity: object() if entity == "shared_point" else None,
    )
    tabs = _TabTextRecorder()
    host = SimpleNamespace(
        _scene_key="target",
        _right_tab_labels=("区域", "坐标", "方向", "网格", "引用"),
        _right_tabs=tabs,
        _canvas=SimpleNamespace(get_arrows=lambda: [object()] * 4),
    )

    SceneTab._refresh_entity_tab_titles(host)

    assert tabs.texts == {
        0: "区域(3)",
        1: "坐标(2)",
        2: "方向(4)",
        3: "网格(1)",
        4: "引用(2)",
    }


def test_entity_tab_counts_omit_zero(monkeypatch):
    scene = SimpleNamespace(
        regions=[], points=[], panels=[], subscene_refs=[], references=[])
    registry = SimpleNamespace(get_scene=lambda _key: scene)
    monkeypatch.setattr(scene_tab, "get_registry", lambda: registry)
    tabs = _TabTextRecorder()
    host = SimpleNamespace(
        _scene_key="empty",
        _right_tab_labels=("区域", "坐标", "方向", "网格", "引用"),
        _right_tabs=tabs,
        _canvas=SimpleNamespace(get_arrows=lambda: []),
    )

    SceneTab._refresh_entity_tab_titles(host)

    assert tabs.texts == {
        0: "区域", 1: "坐标", 2: "方向", 3: "网格", 4: "引用",
    }


def test_scene_tab_bar_has_equal_two_digit_width(qapp):
    labels = ("区域", "坐标", "方向", "网格", "引用")
    bar = _UniformWidthTabBar(labels)
    for label in labels:
        bar.addTab(label)

    widths = [bar.tabSizeHint(index).width() for index in range(bar.count())]

    assert len(set(widths)) == 1
    assert widths[0] >= bar.fontMetrics().horizontalAdvance("区域(99)") + 24
