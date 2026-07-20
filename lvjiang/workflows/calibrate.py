"""Panel 图像自校准 — 从 panel 截图中检测网格 slot 精确位置

核心算法（valley-based，利用 span 比 slot 更稳定的特性）：

  1. 截取 panel 区域图像
  2. 转灰度
  3. 计算每行/每列像素方差 → 方差剖面
  4. 平滑剖面
  5. 找 N+1 个「低方差谷点」作为 slot 分隔器（含图像两端）
     - slot 内部（含黑边）= 高方差
     - span（纯色间隔）= 低方差
     - 谷点 = 局部最小值，优先选 span 中心
  6. 相邻谷点中点 = slot 中心

为什么用谷点而不是峰点：
  - slot 内部的黑边 + 图标都会产生高方差，峰点可能有多条（边框+内部）
  - span 是整段低方差，谷点单一且稳定
  - N 个 slot 必有 N+1 个分隔器（含两端），数量关系确定
"""

from __future__ import annotations

import numpy as np
from loguru import logger


def _smooth(variance: np.ndarray, kernel: int = 5) -> np.ndarray:
    """轻度平滑，抑制像素级噪声"""
    if variance.size >= kernel:
        return np.convolve(variance, np.ones(kernel) / kernel, mode="same")
    return variance.copy()


def _find_valleys(smoothed: np.ndarray, min_distance: int = 3) -> list[int]:
    """找出所有局部最小值位置（严格小于两侧邻居）"""
    valleys: list[int] = []
    for i in range(1, len(smoothed) - 1):
        if smoothed[i] < smoothed[i - 1] and smoothed[i] <= smoothed[i + 1]:
            if not valleys or i - valleys[-1] >= min_distance:
                valleys.append(i)
            elif smoothed[i] < smoothed[valleys[-1]]:
                valleys[-1] = i
    return valleys


def _fill_valleys(valleys: list[int], needed: int, length: int) -> list[int]:
    """若谷点不足，递归细分最大间隔补齐"""
    result = sorted(valleys)
    while len(result) < needed and len(result) >= 2:
        # 找最大间隔
        max_gap = 0
        max_idx = 0
        for i in range(len(result) - 1):
            gap = result[i + 1] - result[i]
            if gap > max_gap:
                max_gap = gap
                max_idx = i
        if max_gap < 2:
            break
        # 在最大间隔中点插入
        result.insert(max_idx + 1, (result[max_idx] + result[max_idx + 1]) // 2)
    # 极端回退：均匀分布
    if len(result) < needed:
        result = list(range(0, length, max(1, length // needed)))
    return result[:needed]


def find_slot_centers(
    variance: np.ndarray,
    expected_count: int,
) -> list[float]:
    """从方差剖面中提取 N 个 slot 中心（归一化到 [0,1]）

    算法：
      1. 平滑剖面
      2. 找所有局部最小值（谷点）
      3. 取前 expected_count+1 个最低谷点作为分隔器
      4. 按位置排序
      5. 若谷点不足，递归细分最大间隔补齐
      6. 加上两端（0, length-1）作为虚拟分隔器
      7. 相邻分隔器中点 = slot 中心

    Args:
        variance: 1D 数组，每行/每列的像素方差
        expected_count: 期望的 slot 数量（行数或列数）

    Returns:
        list[float]，长度 = expected_count，每个值为归一化中心坐标 ∈ (0,1)
    """
    length = variance.size
    if length == 0 or expected_count <= 0:
        return []
    if expected_count == 1:
        return [0.5]

    smoothed = _smooth(variance)

    # 找所有局部最小值
    valleys = _find_valleys(smoothed, min_distance=max(1, length // (expected_count * 3)))

    # 取最低的 N+1 个（span 是最低方差区域）
    n_separators = expected_count + 1
    if len(valleys) >= n_separators:
        valleys.sort(key=lambda i: smoothed[i])
        separators = sorted(valleys[:n_separators])
    else:
        separators = _fill_valleys(valleys, n_separators, length)

    # 加上两端作为虚拟分隔器
    full_seps = [0] + separators + [length - 1]
    # 去重 + 排序
    full_seps = sorted(set(full_seps))
    # 确保至少有 2 个分隔器
    if len(full_seps) < 2:
        full_seps = [0, length - 1]

    # 相邻分隔器中点 = slot 中心
    centers: list[float] = []
    for i in range(len(full_seps) - 1):
        center = (full_seps[i] + full_seps[i + 1]) / 2.0 / length
        centers.append(center)

    # 若中心数多于期望，按方差峰值保留前 N 个
    if len(centers) > expected_count:
        # 评估每个中心附近方差总和，保留最高的 N 个
        scored: list[tuple[float, int]] = []
        for idx, c in enumerate(centers):
            c_pixel = int(c * length)
            window = max(1, length // (expected_count * 3))
            lo = max(0, c_pixel - window)
            hi = min(length, c_pixel + window)
            score = float(np.sum(variance[lo:hi]))
            scored.append((score, idx))
        scored.sort(reverse=True)
        keep_indices = sorted([idx for _, idx in scored[:expected_count]])
        centers = [centers[i] for i in keep_indices]

    return centers


def detect_grid(
    image: np.ndarray,
    expected_rows: int = 3,
    expected_cols: int = 6,
) -> list[tuple[float, float]]:
    """从 panel 截图中检测网格 slot 精确位置

    Args:
        image: panel 区域的 BGR 图像（H×W×3 或 H×W）
        expected_rows: 期望行数
        expected_cols: 期望列数

    Returns:
        slot_centers: list[(cx_ratio, cy_ratio)]，相对于 panel 区域，
        按「先行后列」顺序（row 0 col 0, row 0 col 1, ..., row 1 col 0, ...）
    """
    if image is None or image.size == 0:
        logger.error("detect_grid: 空图像")
        return []

    # 转灰度
    if image.ndim == 3:
        gray = np.mean(image, axis=2)
    else:
        gray = image.astype(np.float64)

    # 每行/每列方差
    row_var = np.var(gray, axis=1)
    col_var = np.var(gray, axis=0)

    row_centers = find_slot_centers(row_var, expected_rows)
    col_centers = find_slot_centers(col_var, expected_cols)

    if not row_centers or not col_centers:
        logger.error(
            f"detect_grid: 无法检测到网格（行中心={len(row_centers)}, 列中心={len(col_centers)}）"
        )
        return []

    # 笛卡尔积：先行后列
    centers: list[tuple[float, float]] = []
    for cy in row_centers:
        for cx in col_centers:
            centers.append((cx, cy))

    logger.debug(
        f"detect_grid: 检测到 {len(row_centers)} 行 × {len(col_centers)} 列 = {len(centers)} 个 slot"
    )
    return centers
