"""装备锁定图标的纯图像分类。"""

from __future__ import annotations

import numpy as np

LOCKED = "locked"
UNLOCK = "unlock"
LOCK_STATES = frozenset((LOCKED, UNLOCK))

# 两个图标轮廓相同，状态差异是红色/灰色。下列阈值与亮度绝对值
# 解耦，留出抗缩放和压缩的余量；模板样本的红色主导占比约 0.328。
_FOREGROUND_MIN = 50
_FOREGROUND_RATIO_MIN = 0.10
_RED_MIN = 50
_RED_MARGIN = 10
_LOCKED_RED_RATIO_MIN = 0.10
_UNLOCK_RED_RATIO_MAX = 0.02


def normalize_lock_status(value) -> str | None:
    """只保留公开的两种状态；缺失和非法值均为未知。"""
    text = str(value or "").strip().lower()
    return text if text in LOCK_STATES else None


def classify_lock_status(crop: np.ndarray | None) -> str | None:
    """根据 BGR 裁剪中的前景和红色主导占比识别锁定状态。

    None 表示无图像、无明确图标或处在两个阈值之间；调用方不得
    把这种情况当成未锁定。
    """
    if crop is None or not isinstance(crop, np.ndarray):
        return None
    if crop.ndim != 3 or crop.shape[2] < 3 or crop.size == 0:
        return None

    b = crop[..., 0].astype(np.int16)
    g = crop[..., 1].astype(np.int16)
    r = crop[..., 2].astype(np.int16)
    foreground = np.maximum(np.maximum(r, g), b) >= _FOREGROUND_MIN
    foreground_ratio = float(foreground.mean())
    if foreground_ratio < _FOREGROUND_RATIO_MIN:
        return None

    red_dominant = (
        (r >= _RED_MIN)
        & ((r - g) >= _RED_MARGIN)
        & ((r - b) >= _RED_MARGIN)
    )
    red_ratio = float(red_dominant.mean())
    if red_ratio >= _LOCKED_RED_RATIO_MIN:
        return LOCKED
    if red_ratio <= _UNLOCK_RED_RATIO_MAX:
        return UNLOCK
    return None
