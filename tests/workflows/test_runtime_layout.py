"""子场景投影入口的防御：外框不合法时要在读坐标之前中断。

外框宽或高为 0 时，子场景里每个元素都会被投影到同一个点上——坐标算得出来，
只是全塌在外框左上角，点下去点到的是别的东西，而且没有任何报错。
"""

import pytest

from lvjiang.core.layout.models import SubsceneRef
from lvjiang.workflows.errors import WorkflowUserError
from lvjiang.workflows.runtime_layout import resolve_subscene_instance


class _Layout:
    def __init__(self, refs):
        self._refs = refs

    def get_scene_subscene_refs(self, _scene):
        return list(self._refs)


@pytest.fixture
def subscene_registry(monkeypatch):
    """让引用定义解析出「这是一张子场景」，不依赖真实配置。"""
    import lvjiang.core.scene.registry as scene_registry

    monkeypatch.setattr(
        scene_registry, "get_subscene_ref_def",
        lambda scene_key, ref_key: type(
            "Ref", (), {"scene": "card"})(),
    )
    monkeypatch.setattr(scene_registry, "is_subscene", lambda _key: True)


@pytest.mark.parametrize("width, height", [(0.0, 0.5), (0.4, 0.0), (-0.2, 0.5)])
def test_degenerate_subscene_frame_is_rejected(
        subscene_registry, width, height):
    layout = _Layout([SubsceneRef("card_box", 0.1, 0.1, width, height)])

    with pytest.raises(WorkflowUserError, match="外框尺寸"):
        resolve_subscene_instance(layout, "parent", "card_box")


def test_usable_frame_is_returned(subscene_registry):
    ref = SubsceneRef("card_box", 0.1, 0.2, 0.4, 0.5)

    assert resolve_subscene_instance(
        _Layout([ref]), "parent", "card_box") is ref
