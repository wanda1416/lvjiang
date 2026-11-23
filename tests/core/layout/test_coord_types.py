"""坐标算术契约：几何错误要在类型层尽早暴露，不能伪装成合法结果。"""

import pytest

from lvjiang.core.layout.coord_types import CircleCoordRef, CoordRef, Offset


def test_offset_division_by_zero_raises():
    """Offset / 0 不能返回零向量。

    返回 (0, 0) 时会静默变成一个「合法」的方向，调用方算错的步数或比例
    就这样混进坐标，最后表现为点到别处而没有任何报错。
    """
    with pytest.raises(ZeroDivisionError):
        Offset(dx=1.0, dy=2.0) / 0


def test_offset_division_scales_both_axes():
    assert Offset(dx=2.0, dy=-4.0) / 4 == Offset(dx=0.5, dy=-1.0)


def test_absolute_coordinates_still_cannot_be_scaled():
    """CoordRef 仍然禁止乘除：坐标点不是向量，缩放没有几何意义。"""
    with pytest.raises(TypeError):
        CircleCoordRef(cx=0.5, cy=0.5, r=0.1) / 2
    with pytest.raises(TypeError):
        CoordRef(cx=0.5, cy=0.5) * 2
