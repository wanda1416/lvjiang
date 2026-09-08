import pytest

from lvjiang.core.key_validation import validate_layout_activation_keys
from lvjiang.core.layout_models import Layout, Region


def _region(key: str) -> Region:
    return Region(
        key=key,
        x_ratio=0.0,
        y_ratio=0.0,
        w_ratio=0.1,
        h_ratio=0.1,
        activation_key="SPACE",
    )


def test_same_key_is_rejected_within_same_scene_view():
    layout = Layout(
        regions={
            "training_xinfa": [_region("purchase"), _region("sanben_1")],
        }
    )

    with pytest.raises(ValueError, match="zhihuan.*SPACE.*purchase.*sanben_1"):
        validate_layout_activation_keys(layout)


def test_same_key_is_allowed_across_different_views():
    layout = Layout(
        regions={
            "training_xinfa": [_region("purchase"), _region("confirm")],
        }
    )

    validate_layout_activation_keys(layout)


def test_same_key_is_allowed_across_reference_views():
    layout = Layout(
        regions={
            "equip_tune_detail": [
                _region("confirm"),
                _region("blank_area"),
            ],
        }
    )

    validate_layout_activation_keys(layout)
