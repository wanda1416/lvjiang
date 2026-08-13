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

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from loguru import logger


@dataclass(frozen=True)
class SlotAxis:
    """一个轴（行或列）的对齐结果"""
    centers: list[float]          # 归一化中心坐标 ∈ (0,1)
    boundaries: list[float]       # 归一化边界坐标，长度 = len(centers) + 1
    slot_size: float = 0.0        # slot 实际尺寸（归一化）
    span_size: float = 0.0        # span 实际尺寸（归一化）


def _find_runs(binary: np.ndarray) -> list[tuple[int, int]]:
    """找连续 1 的区间 [start, end)（像素坐标）"""
    runs: list[tuple[int, int]] = []
    in_run = False
    start = 0
    for i, v in enumerate(binary):
        if v == 1 and not in_run:
            start, in_run = i, True
        elif v == 0 and in_run:
            runs.append((start, i))
            in_run = False
    if in_run:
        runs.append((start, len(binary)))
    return runs


def _binary_axis(
    line_means: np.ndarray,
    expected_count: int,
    black_threshold: float = 30.0,
    min_visible: float = 0.95,
) -> SlotAxis:
    """从一维亮度剖面拟合「规则周期网格」的 slot 中心与边界。

    不再假设「每个亮区 = 一个 slot」（稀疏行、半截行、杂散亮区都会破坏
    该假设），而是充分利用已知的 expected_count：

      1. 二值化找亮区 run，取足够长的 run 作为「可靠 slot」锚点（滤掉半截/噪声）；
      2. 由可靠 slot 间距的中位数估算网格周期 p（个别稀疏行漏检只会产生 2p
         的间距，被中位数抹掉），并用全部可靠中心最小二乘修正相位；
      3. 以该相位 + 周期在 [0, L] 上枚举所有 slot 候选——即使某行只有一
         列有装备（整行偏暗、无 run），也会被周期点阵补上；而落在点阵外的
         杂散亮区（如半周期处的偷看行）自然被排除；
      4. 只保留「可见比例 ≥ min_visible」的候选（默认 0.95，即 ≥95% 落在 panel 内）；
      5. 数量以 expected_count 封顶，超出时保留邻域平均亮度最高的若干个。

    Args:
        line_means: 1D 数组，每行/每列的平均亮度
        expected_count: 该轴期望的 slot 数（行数或列数），用作数量上限
        black_threshold: 黑边判定阈值（0-255）
        min_visible: slot 计入有效所需的最小可见比例（0.5-1.0）

    Returns:
        SlotAxis(centers, boundaries, slot_size, span_size)
    """
    length = len(line_means)
    if length == 0 or expected_count <= 0:
        return SlotAxis(centers=[], boundaries=[])

    binary = (line_means >= black_threshold).astype(np.int8)
    runs = _find_runs(binary)
    if not runs:
        logger.warning("binary_axis: 未检测到任何内容区域")
        return SlotAxis(centers=[], boundaries=[])

    run_centers = np.array([(s + e) / 2.0 for s, e in runs])
    run_lengths = np.array([e - s for s, e in runs], dtype=float)
    median_len = float(np.median(run_lengths))

    # 可靠 slot：长度 ≥ 60% 中位数（滤掉半截行/杂散噪声，仅用于定周期与相位）
    reliable_mask = run_lengths >= 0.6 * median_len
    reliable_centers = run_centers[reliable_mask]
    reliable_lengths = run_lengths[reliable_mask]
    if reliable_centers.size == 0:
        reliable_centers, reliable_lengths = run_centers, run_lengths

    # 估算周期 p（像素）
    if reliable_centers.size >= 2:
        gaps = np.diff(np.sort(reliable_centers))
        period = float(np.median(gaps))
    else:
        # 只有单个可靠 slot：退化为「满格时铺满 panel」假设
        period = length / expected_count
    if period <= 1e-6:
        period = length / expected_count

    slot_len = min(float(np.median(reliable_lengths)), period)
    half_slot = slot_len / 2.0

    # 用全部可靠中心最小二乘拟合相位（对齐到同一点阵，抗单点偏差）
    a0 = float(reliable_centers[0])
    ks = np.round((reliable_centers - a0) / period)
    anchor = float(np.mean(reliable_centers - ks * period))

    # 以 anchor 对齐相位，向前后枚举覆盖 [0, L] 的候选中心
    k_lo = int(np.floor(-anchor / period)) - 1
    k_hi = int(np.ceil((length - anchor) / period)) + 1
    candidates = sorted(anchor + k * period for k in range(k_lo, k_hi + 1))

    # 可见性：slot 可见比例需 ≥ min_visible（容差 = (1 - min_visible) * slot）
    eps = (1.0 - min_visible) * slot_len
    visible = [c for c in candidates
               if c - half_slot >= -eps and c + half_slot <= length + eps]
    if not visible:
        logger.warning("binary_axis: 无完整可见 slot")
        return SlotAxis(centers=[], boundaries=[])

    # 数量封顶：超出 expected_count 时保留邻域平均亮度最高的若干个
    if len(visible) > expected_count:
        def _brightness(c: float) -> float:
            lo, hi = max(0, int(c - half_slot)), min(length, int(c + half_slot))
            return float(np.mean(line_means[lo:hi])) if hi > lo else 0.0
        visible = sorted(sorted(visible, key=_brightness,
                                reverse=True)[:expected_count])

    centers = [c / length for c in visible]
    half_p = (period / length) / 2.0
    boundaries = [max(0.0, centers[0] - half_p)]
    for i in range(len(centers) - 1):
        boundaries.append((centers[i] + centers[i + 1]) / 2.0)
    boundaries.append(min(1.0, centers[-1] + half_p))

    return SlotAxis(
        centers=centers,
        boundaries=boundaries,
        slot_size=slot_len / length,
        span_size=max(0.0, period - slot_len) / length,
    )


def _even_axis(count: int) -> SlotAxis:
    """将 [0,1] 等分为 count 个格子（无间隔）

    用于 panel 无法通过图像检测识别 slot 边界时的降级方案：
    直接按声明的 rows/cols 均匀分割 panel 区域。

    Args:
        count: 该轴的格子数（行数或列数）

    Returns:
        SlotAxis(centers, boundaries, slot_size=1/count, span_size=0)
    """
    if count <= 0:
        return SlotAxis(centers=[], boundaries=[])
    step = 1.0 / count
    centers = [step * (i + 0.5) for i in range(count)]
    boundaries = [step * i for i in range(count + 1)]
    return SlotAxis(
        centers=centers,
        boundaries=boundaries,
        slot_size=step,
        span_size=0.0,
    )


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


def _make_even_alignment(expected_rows: int, expected_cols: int) -> GridAlignment:
    """构造等分模式的 GridAlignment（无图像检测）"""
    row_axis = _even_axis(expected_rows)
    col_axis = _even_axis(expected_cols)
    return GridAlignment(
        row_centers=row_axis.centers,
        col_centers=col_axis.centers,
        row_bounds=row_axis.boundaries,
        col_bounds=col_axis.boundaries,
        row_slot=row_axis.slot_size,
        row_span=0.0,
        col_slot=col_axis.slot_size,
        col_span=0.0,
    )


def detect_grid(
    image: np.ndarray,
    expected_rows: int = 3,
    expected_cols: int = 6,
    black_threshold: float = 30.0,
    min_visible: float = 0.95,
    fallback: bool = False,
    scroll_direction: str = "vertical",
) -> GridAlignment | None:
    """从 panel 截图中检测网格 slot 精确位置

    通过二值化每行/每列的平均亮度，找连续内容区间来确定 slot 位置。

    Args:
        image: panel 区域的 BGR 图像（H×W×3 或 H×W）
        expected_rows: 期望的行数（用于日志）
        expected_cols: 期望的列数（用于日志）
        black_threshold: 黑边判定阈值（0-255），低于此值视为黑边
        min_visible: 行计入有效所需的最小可见比例，钳位到 [0.5, 1.0]。
            仅作用于行轴（垂直滚动才会产生半截行）；必须 > 0.5，
            否则半截行的中心可能落在 panel 外导致点击脱靶。
        fallback: 检测失败时是否降级为等分模式。True 时图像检测失败
            返回按 expected_rows/expected_cols 等分的 GridAlignment，
            而非 None。
        scroll_direction: 滚动方向，决定 rows/cols 的容差判断：
            - "vertical"：rows 允许 expected-1，cols 必须精确
            - "horizontal"：cols 允许 expected-1，rows 必须精确
            - "both"：rows/cols 都允许 expected-1
            - "none"：rows/cols 都必须精确

    Returns:
        GridAlignment 或 None（检测失败且 fallback=False 时）
    """
    if image is None or image.size == 0:
        logger.error("detect_grid: 空图像")
        if fallback:
            logger.warning(f"detect_grid: 降级为等分模式（{expected_rows}×{expected_cols}）")
            return _make_even_alignment(expected_rows, expected_cols)
        return None

    min_visible = min(1.0, max(0.5, float(min_visible)))

    # 转灰度
    if image.ndim == 3:
        gray = np.mean(image, axis=2)
    else:
        gray = image.astype(np.float64)

    # 每行/每列平均亮度
    row_means = np.mean(gray, axis=1)  # 每行所有像素的平均亮度
    col_means = np.mean(gray, axis=0)  # 每列所有像素的平均亮度

    row_axis = _binary_axis(row_means, expected_rows, black_threshold,
                            min_visible=min_visible)
    col_axis = _binary_axis(col_means, expected_cols, black_threshold)

    if not row_axis.centers or not col_axis.centers:
        logger.error(
            f"detect_grid: 无法检测到网格"
            f"（行={len(row_axis.centers)}, 列={len(col_axis.centers)}）"
        )
        if fallback:
            logger.warning(f"detect_grid: 降级为等分模式（{expected_rows}×{expected_cols}）")
            return _make_even_alignment(expected_rows, expected_cols)
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

    # 根据 scroll_direction 判断 rows/cols 是否可接受
    if scroll_direction in ("vertical", "both"):
        rows_ok = result.n_rows in (expected_rows, expected_rows - 1)
    else:
        rows_ok = result.n_rows == expected_rows

    if scroll_direction in ("horizontal", "both"):
        cols_ok = result.n_cols in (expected_cols, expected_cols - 1)
    else:
        cols_ok = result.n_cols == expected_cols

    # 异常结果保存调试图片，便于后续分析算法
    if not (rows_ok and cols_ok):
        _save_debug_image(
            image,
            f"rows{result.n_rows}_of_{expected_rows}_cols{result.n_cols}_of_{expected_cols}",
        )
        # fallback 模式：检测结果不符合预期时降级为等分
        if fallback:
            logger.warning(
                f"detect_grid: 检测结果不符合预期 "
                f"(rows={result.n_rows}/{expected_rows}, cols={result.n_cols}/{expected_cols}, "
                f"scroll={scroll_direction})，降级为等分模式"
            )
            return _make_even_alignment(expected_rows, expected_cols)

    return result


def _save_debug_image(image: np.ndarray, tag: str) -> Path | None:
    """保存 detect_grid 异常结果的原图到 logs/image/，便于离线分析算法。

    文件名格式：{YYYYMMDD_HHMMSS}_{tag}.png
    返回保存路径；保存失败返回 None（仅 log，不抛异常）。
    """
    try:
        out_dir = Path("logs/image")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"{ts}_{tag}.png"
        if not cv2.imwrite(str(out_path), image):
            logger.warning(f"detect_grid: 调试图片保存失败 {out_path}")
            return None
        logger.info(f"detect_grid: 异常结果已保存调试图 → {out_path}")
        return out_path
    except Exception as e:
        logger.warning(f"detect_grid: 保存调试图片异常: {e}")
        return None
