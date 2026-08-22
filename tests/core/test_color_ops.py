"""图色原语 color_ops 测试

合成图验证每个原语的精确语义（边界、采样、通道约定）。
真机截图回放不进仓库（含游戏画面），本地另置。
"""
from __future__ import annotations

import numpy as np
import pytest

from lvjiang.core.recognizers import color_ops as co


def _bgr(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    return rgb[2], rgb[1], rgb[0]


def _blank(w: int = 100, h: int = 60, rgb=(0, 0, 0)) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = _bgr(rgb)
    return img


# ─── parse_hex ────────────────────────────────────────────

def test_parse_hex_forms():
    assert co.parse_hex("#2ecc71") == (0x2E, 0xCC, 0x71)
    assert co.parse_hex("2ECC71") == (0x2E, 0xCC, 0x71)
    assert co.parse_hex((1, 2, 3)) == (1, 2, 3)
    with pytest.raises(ValueError):
        co.parse_hex("#fff")


# ─── pixel / brightness ───────────────────────────────────

def test_pixel_rgb_is_rgb_order_on_bgr_image():
    img = _blank(rgb=(10, 20, 30))
    assert co.pixel_rgb(img, 5, 5) == (10, 20, 30)
    assert co.brightness(img, 5, 5) == 60


def test_pixel_out_of_range_clamps_to_edge():
    img = _blank(w=10, h=10, rgb=(1, 1, 1))
    img[9, 9] = _bgr((200, 0, 0))
    assert co.pixel_rgb(img, 99, 99) == (200, 0, 0)
    assert co.pixel_rgb(img, -5, -5) == (1, 1, 1)


# ─── color_ratio ──────────────────────────────────────────

def test_color_ratio_inclusive_bounds_and_partial_fill():
    img = _blank(w=100, h=10, rgb=(0, 0, 0))
    img[:, :25] = _bgr((0, 200, 0))   # 左 1/4 纯绿
    ratio = co.color_ratio(img, 0, 0, 99, 9, lo=(0, 130, 0), hi=(90, 255, 160))
    assert ratio == pytest.approx(0.25)
    # 上界是闭区间：g=200 落在 [200, 255] 内
    assert co.color_ratio(img, 0, 0, 99, 9, lo=(0, 200, 0), hi=(0, 255, 0)) == pytest.approx(0.25)
    # 区间不含：g=200 不在 [201, 255]
    assert co.color_ratio(img, 0, 0, 99, 9, lo=(0, 201, 0), hi=(0, 255, 0)) == 0.0


def test_color_ratio_step_sampling_is_statistical():
    img = _blank(w=100, h=100, rgb=(0, 0, 0))
    img[:, :50] = _bgr((255, 255, 255))
    full = co.color_ratio(img, 0, 0, 99, 99, (200, 200, 200), (255, 255, 255), step=1)
    sampled = co.color_ratio(img, 0, 0, 99, 99, (200, 200, 200), (255, 255, 255), step=3)
    assert full == pytest.approx(0.5)
    assert sampled == pytest.approx(0.5, abs=0.03)


def test_color_ratio_empty_or_inverted_region():
    img = _blank()
    assert co.color_ratio(img, 50, 50, 10, 10, (0, 0, 0), (255, 255, 255)) == 0.0


def test_color_ratio_tol_symmetric():
    img = _blank(rgb=(100, 100, 100))
    assert co.color_ratio_tol(img, 0, 0, 99, 59, (110, 90, 100), tol=10) == pytest.approx(1.0)
    assert co.color_ratio_tol(img, 0, 0, 99, 59, (120, 100, 100), tol=10) == 0.0


# ─── bright_segments ──────────────────────────────────────

def test_bright_segments_counts_on_off_transitions():
    img = _blank(w=100, h=3, rgb=(0, 0, 0))
    # 三段亮块：[10,20) [40,50) [70,80)
    for a, b in ((10, 20), (40, 50), (70, 80)):
        img[:, a:b] = _bgr((255, 255, 255))
    assert co.bright_segments(img, 1, 0, 99, on_min=300, off_max=150) == 3


def test_bright_segments_trailing_bright_not_counted():
    img = _blank(w=100, h=3, rgb=(0, 0, 0))
    img[:, 10:20] = _bgr((255, 255, 255))
    img[:, 90:] = _bgr((255, 255, 255))  # 到行尾仍亮 → 不计
    assert co.bright_segments(img, 1, 0, 99, 300, 150) == 1


def test_bright_segments_hysteresis_band_ignored():
    img = _blank(w=100, h=3, rgb=(0, 0, 0))
    img[:, 10:20] = _bgr((255, 255, 255))
    img[:, 20:30] = _bgr((70, 70, 70))   # 亮度 210：在 (150, 300) 迟滞带内，不退出
    img[:, 30:40] = _bgr((255, 255, 255))
    assert co.bright_segments(img, 1, 0, 99, 300, 150) == 1


# ─── color_vec ────────────────────────────────────────────

def test_color_vec_direction_up_is_zero_deg():
    img = _blank(w=101, h=101, rgb=(0, 0, 0))
    img[10:40, 48:53] = _bgr((0, 220, 0))  # 中心正上方一条绿
    deg, n = co.color_vec(img, 0, 0, 100, 100, 50, 50, 120, 255, 40)
    assert n > 0
    assert deg == pytest.approx(0.0, abs=1.0)


def test_color_vec_direction_right_is_90_deg_and_radius_band():
    img = _blank(w=101, h=101, rgb=(0, 0, 0))
    img[48:53, 70:90] = _bgr((0, 220, 0))   # 右侧远处
    img[48:53, 55:60] = _bgr((0, 220, 0))   # 右侧近处
    deg, n = co.color_vec(img, 0, 0, 100, 100, 50, 50, 120, 255, 40)
    assert deg == pytest.approx(90.0, abs=1.0)
    # 只数远环带：近处 5..10 px 排除
    _deg, n_far = co.color_vec(img, 0, 0, 100, 100, 50, 50, 120, 255, 40, min_r=15)
    assert 0 < n_far < n


def test_color_vec_symmetric_noise_cancels_and_channel_param():
    img = _blank(w=101, h=101, rgb=(0, 0, 0))
    img[20:25, 48:53] = _bgr((0, 220, 0))   # 上
    img[76:81, 48:53] = _bgr((0, 220, 0))   # 下（对称）
    img[48:53, 76:81] = _bgr((0, 0, 220))   # 右，蓝
    deg_g, _ = co.color_vec(img, 0, 0, 100, 100, 50, 50, 120, 255, 40, channel=co.CH_GREEN)
    # 上下抵消后合成向量 ~0，方向不稳定但 count > 0；此处只验蓝通道选择正确
    deg_b, n_b = co.color_vec(img, 0, 0, 100, 100, 50, 50, 120, 255, 40, channel=co.CH_BLUE)
    assert n_b > 0 and deg_b == pytest.approx(90.0, abs=1.0)
    assert deg_g != -1.0


def test_color_vec_no_match():
    assert co.color_vec(_blank(), 0, 0, 99, 59, 50, 30, 120, 255, 40) == (-1.0, 0)


# ─── find_multi_color ─────────────────────────────────────

def test_find_multi_color_anchor_and_offsets():
    img = _blank(w=60, h=40, rgb=(0, 0, 0))
    img[10, 10] = _bgr((255, 200, 0))
    img[10, 14] = _bgr((255, 255, 255))
    img[16, 10] = _bgr((50, 50, 50))
    pts = [(4, 0, (255, 255, 255)), (0, 6, (50, 50, 50))]
    assert co.find_multi_color(img, 0, 0, 59, 39, (255, 200, 0), pts, tol=12) == (10, 10)
    # 偏移点不符 → None
    assert co.find_multi_color(img, 0, 0, 59, 39, (255, 200, 0), [(4, 0, (0, 0, 0))], tol=12) is None
    # 偏移越界 → None
    assert co.find_multi_color(img, 0, 0, 59, 39, (255, 200, 0), [(100, 0, (0, 0, 0))], tol=12) is None


def test_find_multi_color_returns_first_in_row_major():
    img = _blank(w=60, h=40, rgb=(0, 0, 0))
    img[20, 30] = _bgr((255, 0, 0))
    img[5, 40] = _bgr((255, 0, 0))
    assert co.find_multi_color(img, 0, 0, 59, 39, (255, 0, 0), [], tol=0) == (40, 5)


# ─── find_icons ───────────────────────────────────────────

def test_find_icons_filters_by_area_and_bbox_and_sorts_desc():
    img = _blank(w=200, h=100, rgb=(30, 30, 30))
    img[10:50, 10:50] = _bgr((40, 220, 60))      # 40×40 大块
    img[60:84, 100:124] = _bgr((40, 220, 60))    # 24×24 中块
    img[5:8, 150:153] = _bgr((40, 220, 60))      # 3×3 小点
    blobs = co.find_icons(img, 0, 0, 199, 99, co.CH_GREEN, 150, 35, 60, 190, min_area=60, min_bbox=0)
    assert [(b[3], b[4]) for b in blobs] == [(40, 40), (24, 24)]
    assert blobs[0][0] == pytest.approx(29.5) and blobs[0][1] == pytest.approx(29.5)
    # bbox ≥ 32 只剩大块
    blobs = co.find_icons(img, 0, 0, 199, 99, co.CH_GREEN, 150, 35, 60, 190, min_area=60, min_bbox=32)
    assert len(blobs) == 1 and blobs[0][2] == 1600


def test_find_icons_region_offset_and_diagonal_connectivity():
    img = _blank(w=100, h=100, rgb=(0, 0, 0))
    # 两个只在对角相接的 3×3 块：8 连通应合并为一个
    img[10:13, 10:13] = _bgr((0, 255, 0))
    img[13:16, 13:16] = _bgr((0, 255, 0))
    blobs = co.find_icons(img, 5, 5, 50, 50, co.CH_GREEN, 100, 50, min_area=1)
    assert len(blobs) == 1 and blobs[0][2] == 18
    assert blobs[0][0] == pytest.approx(12.5) and blobs[0][1] == pytest.approx(12.5)


def test_find_icons_o_max_rejects_whitish():
    img = _blank(w=50, h=50, rgb=(0, 0, 0))
    img[10:30, 10:30] = _bgr((200, 255, 200))  # 发白的绿：g 主导但 r/b 超 o_max
    assert co.find_icons(img, 0, 0, 49, 49, co.CH_GREEN, 150, 35, 35, o_max=190, min_area=1) == []
