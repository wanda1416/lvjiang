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


def test_same_key_is_allowed_within_same_scene_view():
    """绑定方向是「区域 → 按键」，同一个键服务多个区域是真实存在的。

    暗杀和拾取都用 F：同一画面上两个不同区域（各有自己的 OCR 范围），只是共用
    触发键。需要唯一的是反方向的「按键 → 区域」，而布局里没有这个查询。
    """
    layout = Layout(
        regions={
            "training_xinfa": [_region("purchase"), _region("sanben_1")],
        }
    )

    validate_layout_activation_keys(layout)


def test_invalid_key_name_is_still_rejected():
    """放开重复不等于放开乱写：键名仍要能被 press 认出来。"""
    region = _region("purchase")
    region.activation_key = "NOT_A_KEY"
    layout = Layout(regions={"training_xinfa": [region]})

    with pytest.raises(ValueError, match="NOT_A_KEY"):
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


def test_mouse_button_is_valid_layout_activation():
    region = _region("purchase")
    region.activation_key = "MOUSE_LEFT"
    layout = Layout(regions={"training_xinfa": [region]})

    validate_layout_activation_keys(layout)
