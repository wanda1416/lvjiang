import pytest

from lvjiang.core.layout_models import CanvasConfig, Layout, Region, SubsceneRef


def test_subscene_layout_round_trip_keeps_crop_and_instances():
    layout = Layout(name="desktop")
    layout.set_scene_crop_canvas(
        "card", CanvasConfig(0.1, 0.2, 0.3, 0.4))
    layout.set_scene_subscene_refs(
        "parent", [SubsceneRef("card1", 0.2, 0.3, 0.4, 0.5)])

    restored = Layout.from_dict("desktop", layout.to_dict())

    assert restored.get_scene_crop_canvas("card").to_dict() == {
        "x_ratio": 0.1, "y_ratio": 0.2, "w_ratio": 0.3, "h_ratio": 0.4,
    }
    assert restored.get_scene_subscene_refs("parent")[0].to_dict() == {
        "key": "card1", "x_ratio": 0.2, "y_ratio": 0.3,
        "w_ratio": 0.4, "h_ratio": 0.5,
    }


def test_subscene_coordinate_composition(monkeypatch):
    from lvjiang.core import scene_registry
    from lvjiang.core.scene_definition_models import SubsceneRefDef
    from lvjiang.workflows.runtime_layout import resolve_subscene_region

    layout = Layout(name="desktop")
    layout.set_scene_subscene_refs(
        "parent", [SubsceneRef("card1", 0.2, 0.3, 0.4, 0.5)])
    layout.set_scene_regions(
        "card", [Region("label", 0.1, 0.2, 0.3, 0.4)])
    monkeypatch.setattr(
        scene_registry, "get_subscene_ref_def",
        lambda _scene, _key: SubsceneRefDef("card1", "卡片1", "card"))
    monkeypatch.setattr(scene_registry, "is_subscene", lambda key: key == "card")

    region = resolve_subscene_region(layout, "parent", "card1", "label")

    assert region.x_ratio == pytest.approx(0.24)
    assert region.y_ratio == pytest.approx(0.4)
    assert region.w_ratio == pytest.approx(0.12)
    assert region.h_ratio == pytest.approx(0.2)
