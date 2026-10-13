"""地图平移拼接必须保留实际地形坐标，拒绝无重叠的拼接。"""
import numpy as np
import pytest

from lvjiang.core.map_stitching import stitch_map_frames
from lvjiang.core.maps import MapError


def test_stitch_translation_excludes_fixed_overlay():
    import cv2

    rng = np.random.default_rng(7)
    world = cv2.GaussianBlur(rng.integers(0, 256, (300, 600, 3), dtype=np.uint8), (3, 3), 0)
    frames = [world[:, :400].copy(), world[:, 200:].copy()]
    mask = np.full((300, 400), 255, dtype=np.uint8)
    mask[:70, :100] = 0
    for frame in frames:
        frame[:70, :100] = 255
    result = stitch_map_frames(frames, [mask, mask])
    assert result.offsets == [(0, 0), (200, 0)]
    assert result.image.shape == world.shape
    assert np.array_equal(result.image[100:], world[100:])
    assert not result.coverage[:70, :100].any()


def test_stitch_rejects_disconnected_views():
    rng = np.random.default_rng(11)
    frames = [rng.integers(0, 256, (250, 350, 3), dtype=np.uint8) for _ in range(2)]
    masks = [np.full((250, 350), 255, dtype=np.uint8)] * 2
    with pytest.raises(MapError, match="重叠"):
        stitch_map_frames(frames, masks)
