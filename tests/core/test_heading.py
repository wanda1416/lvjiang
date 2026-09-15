"""小地图内凹箭头朝向解析：合成不同角度的箭头，验证角度、置信度与失败路径。"""

import math

import cv2
import numpy as np
import pytest

from lvjiang.core.recognizers.heading import (
    bearing_from_vector,
    detect_arrow_heading,
    draw_heading_overlay,
)

YELLOW_BGR = (40, 210, 250)


def _rotate(points, deg, cx, cy):
    rad = math.radians(deg)
    out = []
    for x, y in points:
        # 罗盘角：0=上，顺时针。图像 y 向下，所以先把"上"定义为 (0, -1)
        rx = x * math.cos(rad) - y * math.sin(rad)
        ry = x * math.sin(rad) + y * math.cos(rad)
        out.append((cx + rx, cy + ry))
    return np.array(out, dtype=np.int32)


def _concave_arrow_image(heading_deg, *, size=120, scale=1.0, color=YELLOW_BGR,
                         background=(60, 80, 60), notch=True):
    """居中画一个燕尾形箭头：尖端朝 heading_deg。"""
    img = np.full((size, size, 3), background, dtype=np.uint8)
    cx = cy = size / 2
    s = 18 * scale
    # 以"朝上"为基准的形状：尖端 (0,-s)，两翼 (±0.7s, +0.6s)，尾部缺口 (0, +0.15s)
    if notch:
        shape = [(0, -s), (0.7 * s, 0.6 * s), (0, 0.15 * s), (-0.7 * s, 0.6 * s)]
    else:
        shape = [(0, -s), (0.7 * s, 0.6 * s), (-0.7 * s, 0.6 * s)]
    pts = _rotate(shape, heading_deg, cx, cy)
    cv2.fillPoly(img, [pts], color)
    return img


def _angle_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


@pytest.mark.parametrize("heading", [0, 30, 90, 135, 180, 225, 270, 315, 359])
def test_detects_concave_arrow_heading(heading):
    img = _concave_arrow_image(heading)

    result = detect_arrow_heading(img)

    assert result is not None
    assert result.method == "notch"
    assert result.confidence >= 0.6
    assert _angle_diff(result.heading_deg, heading) <= 3.0, (heading, result)


@pytest.mark.parametrize("scale", [0.6, 1.0, 2.2])
def test_scale_invariant(scale):
    img = _concave_arrow_image(60, size=240, scale=scale)

    result = detect_arrow_heading(img)

    assert result is not None and _angle_diff(result.heading_deg, 60) <= 3.0


def test_plain_triangle_falls_back_to_tip_with_lower_confidence():
    img = _concave_arrow_image(120, notch=False)

    result = detect_arrow_heading(img)

    assert result is not None
    assert result.method == "tip"
    assert result.confidence <= 0.5
    assert _angle_diff(result.heading_deg, 120) <= 4.0


def test_returns_none_when_no_arrow_or_wrong_color():
    assert detect_arrow_heading(np.zeros((80, 80, 3), dtype=np.uint8)) is None
    blue = _concave_arrow_image(45, color=(250, 80, 30))
    assert detect_arrow_heading(blue) is None
    assert detect_arrow_heading(np.zeros((2, 2, 3), dtype=np.uint8)) is None


def test_ignores_far_away_yellow_noise_and_uses_explicit_center():
    img = _concave_arrow_image(200, size=200)
    # 角落放一块更大的黄色，搜索窗口按中心裁剪后不应被它带偏
    cv2.rectangle(img, (0, 0), (40, 40), YELLOW_BGR, -1)

    result = detect_arrow_heading(img, center=(100, 100), window_ratio=0.4)

    assert result is not None and _angle_diff(result.heading_deg, 200) <= 3.0


def test_bearing_convention():
    assert bearing_from_vector(0, -1) == 0.0      # 上 = 北
    assert bearing_from_vector(1, 0) == 90.0      # 右 = 东
    assert bearing_from_vector(0, 1) == 180.0     # 下 = 南
    assert bearing_from_vector(-1, 0) == 270.0    # 左 = 西


def test_overlay_does_not_mutate_input():
    img = _concave_arrow_image(10)
    before = img.copy()
    result = detect_arrow_heading(img)
    assert result is not None

    out = draw_heading_overlay(img, result)

    assert np.array_equal(img, before)
    assert out.shape == img.shape and not np.array_equal(out, img)
