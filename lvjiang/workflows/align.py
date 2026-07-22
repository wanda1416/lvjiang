"""Panel 图像自对齐 — 从 panel 截图中检测网格 slot 精确位置

核心思路（二值化 + 连续区间检测）：

  1. 截取 panel 区域图像
  2. 转灰度
  3. 对每行/每列计算平均亮度
  4. 二值化：亮度 < 阈值 → 0（span 黑边），> 阈值 → 1（slot 内容）
  5. 找连续 1 的区间 → 每个区间 = 一个 slot
  6. 区间边界 = slot 边界，区间中点 = slot 中心

前提：
  - grid 区域外围有黑色边框（span），所以图像边缘必然是黑色
  - slot 内部有图标/文字，平均亮度显著高于纯黑
  - span 是纯黑/深色，平均亮度接近 0
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from loguru import logger


@dataclass(frozen=True)
class SlotAxis:
    """一个轴（行或列）的对齐结果"""
    centers: list[float]          # 归一化中心坐标 ∈ (0,1)
    boundaries: list[float]       # 归一化边界坐标，长度 = len(centers) + 1
    slot_size: float = 0.0        # slot 实际尺寸（归一化）
    span_size: float = 0.0        # span 实际尺寸（归一化）


def _binary_axis(line_means: np.ndarray, black_threshold: float = 30.0) -> SlotAxis:
    """从一维亮度数组提取 slot 中心和边界

    算法：
      1. 二值化：< threshold → 0（黑边），≥ threshold → 1（内容）
      2. 找连续 1 的区间 → 每个 = 一个 slot
      3. 区间起止 = slot 边界，区间中点 = slot 中心

    Args:
        line_means: 1D 数组，每行/每列的平均亮度
        black_threshold: 黑边判定阈值（0-255）

    Returns:
        SlotAxis(centers, boundaries)
    """
    length = len(line_means)
    if length == 0:
        return SlotAxis(centers=[], boundaries=[])

    # 二值化
    binary = (line_means >= black_threshold).astype(np.int8)

    # 找连续 1 的区间（slot 区域）
    runs: list[tuple[int, int]] = []  # (start, end) 像素坐标
    in_run = False
    run_start = 0
    for i in range(length):
        if binary[i] == 1 and not in_run:
            run_start = i
            in_run = True
        elif binary[i] == 0 and in_run:
            runs.append((run_start, i))
            in_run = False
    if in_run:
        runs.append((run_start, length))

    if not runs:
        logger.warning("binary_axis: 未检测到任何内容区域")
        return SlotAxis(centers=[], boundaries=[])

    # 如果只有 1 个区间，无法区分 slot
    if len(runs) == 1:
        s, e = runs[0]
        center = (s + e) / 2.0 / length
        return SlotAxis(centers=[center], boundaries=[s / length, e / length])

    # 过滤边缘短区间：如果边缘区间长度 < 95% 主区间长度，视为半截 slot
    # 低于 95% 可见度的 slot 后续 OCR 不可靠，必须过滤
    run_lengths = [e - s for s, e in runs]
    median_length = float(np.median(run_lengths))
    min_valid = 0.95 * median_length

    # 检查首尾区间是否为短区间
    filtered_runs = list(runs)
    if len(filtered_runs) > 1 and (filtered_runs[0][1] - filtered_runs[0][0]) < min_valid:
        logger.debug(
            f"binary_axis: 合并首部短区间 "
            f"(长度={filtered_runs[0][1] - filtered_runs[0][0]}, "
            f"阈值={min_valid:.0f})"
        )
        filtered_runs.pop(0)
    if len(filtered_runs) > 1 and (filtered_runs[-1][1] - filtered_runs[-1][0]) < min_valid:
        logger.debug(
            f"binary_axis: 合并尾部短区间 "
            f"(长度={filtered_runs[-1][1] - filtered_runs[-1][0]}, "
            f"阈值={min_valid:.0f})"
        )
        filtered_runs.pop()

    runs = filtered_runs

    # 构建边界
    # 相邻 run 之间的 0 区域 = span，边界 = span 中点
    boundaries: list[float] = []

    # 首边界：第一个 run 之前的 span 中点
    # 如果首部被过滤，用第一个 run 起始位置往前推半个周期
    # 如果首部未被过滤（完整 slot），用 run 起始位置（span 中点）
    first_run_start = runs[0][0]
    if first_run_start > 0:
        # 有前置 span，取 span 中点作为边界
        # span 起点 ≈ 前一个 run 的结束位置，但我们不知道（已被过滤）
        # 用当前 run 起始位置 - 半个 span 宽度估算
        # 简化：直接用第一个 run 的起始位置作为边界（span 结束 = slot 开始）
        boundaries.append(first_run_start / length)
    else:
        boundaries.append(0.0)

    # 内部边界：相邻 run 之间的 span 中点
    for i in range(len(runs) - 1):
        gap_start = runs[i][1]      # 当前 slot 结束位置
        gap_end = runs[i + 1][0]    # 下一个 slot 开始位置
        boundary = (gap_start + gap_end) / 2.0 / length
        boundaries.append(boundary)

    # 尾边界：最后一个 run 之后的 span 中点
    last_run_end = runs[-1][1]
    if last_run_end < length:
        boundaries.append(last_run_end / length)
    else:
        boundaries.append(1.0)

    # 中心 = 相邻边界的中点
    centers = [(boundaries[i] + boundaries[i + 1]) / 2.0
               for i in range(len(boundaries) - 1)]

    # slot / span 实际尺寸（归一化）
    # slot = 有效 run 长度的中位数；span = 相邻 run 间隙的中位数
    slot_sizes = [e - s for s, e in runs]
    gap_sizes = [runs[i + 1][0] - runs[i][1] for i in range(len(runs) - 1)]
    slot_size = float(np.median(slot_sizes)) / length
    span_size = float(np.median(gap_sizes)) / length if gap_sizes else 0.0

    # 等距补全：用周期推导被漏掉的 slot（首尾行/列装备少时会被亮度过滤漏掉）
    # 约束：推导出的 slot 边缘（center ± period/2）不能超出 [0, 1]，否则边界不清晰
    if len(centers) >= 2:
        period = centers[1] - centers[0]
        half_period = period / 2.0

        # 向前推导（补全首部被漏掉的 slot）
        prev_center = centers[0] - period
        while prev_center - half_period >= 0:
            centers.insert(0, prev_center)
            prev_center -= period

        # 向后推导（补全尾部被漏掉的 slot）
        next_center = centers[-1] + period
        while next_center + half_period <= 1.0:
            centers.append(next_center)
            next_center += period

        # 重新计算 boundaries（保持和 centers 的关系一致）
        boundaries = [centers[0] - half_period]
        for i in range(len(centers) - 1):
            boundaries.append((centers[i] + centers[i + 1]) / 2.0)
        boundaries.append(centers[-1] + half_period)

    return SlotAxis(centers=centers, boundaries=boundaries,
                    slot_size=slot_size, span_size=span_size)


@dataclass(frozen=True)
class GridAlignment:
    """grid 对齐结果"""
    row_centers: list[float]       # 行中心（归一化）
    col_centers: list[float]       # 列中心（归一化）
    row_bounds: list[float]        # 行边界（归一化），长度 = len(row_centers) + 1
    col_bounds: list[float]        # 列边界（归一化），长度 = len(col_centers) + 1
    row_slot: float = 0.0          # 行 slot 高度（归一化）
    row_span: float = 0.0          # 行 span 高度（归一化）
    col_slot: float = 0.0          # 列 slot 宽度（归一化）
    col_span: float = 0.0          # 列 span 宽度（归一化）

    @property
    def n_rows(self) -> int:
        return len(self.row_centers)

    @property
    def n_cols(self) -> int:
        return len(self.col_centers)

    @property
    def total_slots(self) -> int:
        return self.n_rows * self.n_cols

    def slot_center(self, row_idx: int, col_idx: int) -> tuple[float, float]:
        """获取 slot 中心 (cx, cy) 归一化坐标"""
        return self.col_centers[col_idx], self.row_centers[row_idx]

    def slot_bounds(self, row_idx: int, col_idx: int) -> tuple[float, float, float, float]:
        """获取 slot 边界 (x1, y1, x2, y2) 归一化坐标"""
        x1 = self.col_bounds[col_idx]
        x2 = self.col_bounds[col_idx + 1]
        y1 = self.row_bounds[row_idx]
        y2 = self.row_bounds[row_idx + 1]
        return x1, y1, x2, y2


def detect_grid(
    image: np.ndarray,
    expected_rows: int = 3,
    expected_cols: int = 6,
    black_threshold: float = 30.0,
) -> GridAlignment | None:
    """从 panel 截图中检测网格 slot 精确位置

    通过二值化每行/每列的平均亮度，找连续内容区间来确定 slot 位置。

    Args:
        image: panel 区域的 BGR 图像（H×W×3 或 H×W）
        expected_rows: 期望的行数（用于日志）
        expected_cols: 期望的列数（用于日志）
        black_threshold: 黑边判定阈值（0-255），低于此值视为黑边

    Returns:
        GridAlignment 或 None（检测失败时）
    """
    if image is None or image.size == 0:
        logger.error("detect_grid: 空图像")
        return None

    # 转灰度
    if image.ndim == 3:
        gray = np.mean(image, axis=2)
    else:
        gray = image.astype(np.float64)

    # 每行/每列平均亮度
    row_means = np.mean(gray, axis=1)  # 每行所有像素的平均亮度
    col_means = np.mean(gray, axis=0)  # 每列所有像素的平均亮度

    row_axis = _binary_axis(row_means, black_threshold)
    col_axis = _binary_axis(col_means, black_threshold)

    if not row_axis.centers or not col_axis.centers:
        logger.error(
            f"detect_grid: 无法检测到网格"
            f"（行={len(row_axis.centers)}, 列={len(col_axis.centers)}）"
        )
        return None

    result = GridAlignment(
        row_centers=row_axis.centers,
        col_centers=col_axis.centers,
        row_bounds=row_axis.boundaries,
        col_bounds=col_axis.boundaries,
        row_slot=row_axis.slot_size,
        row_span=row_axis.span_size,
        col_slot=col_axis.slot_size,
        col_span=col_axis.span_size,
    )

    if result.n_rows > expected_rows:
        logger.error(
            f"detect_grid: 检测到行数 {result.n_rows} 超过预期 {expected_rows}，"
            f"可能是滚动过头或亮度剖面异常。"
            f"行 centers={[f'{c:.4f}' for c in result.row_centers]}, "
            f"bounds={[f'{b:.4f}' for b in result.row_bounds]}, "
            f"slot={result.row_slot:.4f}, span={result.row_span:.4f}"
        )
    if result.n_cols > expected_cols:
        logger.error(
            f"detect_grid: 检测到列数 {result.n_cols} 超过预期 {expected_cols}，"
            f"可能是亮度剖面异常。"
            f"列 centers={[f'{c:.4f}' for c in result.col_centers]}, "
            f"bounds={[f'{b:.4f}' for b in result.col_bounds]}, "
            f"slot={result.col_slot:.4f}, span={result.col_span:.4f}"
        )

    logger.info(
        f"detect_grid: 检测到 {result.n_rows}/{expected_rows} 行 × "
        f"{result.n_cols}/{expected_cols} 列 = {result.total_slots} 个有效 slot"
    )
    return result
