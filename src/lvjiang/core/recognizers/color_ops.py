"""图色原语 —— 取色 / 色占比 / 亮段计数 / 色心方位角 / 多点找色 / 同色图标连通分量

游戏 UI 不在无障碍树里，状态判定只能靠画面本身。这一组原语是按键精灵 /
AutoJS 系"图色"三件套里的"色"半边，参数顺序沿用这一系工具的惯例
（region-first，颜色按 RGB），便于把别处调好的阈值直接搬过来。

约定：
- 入参图像是 lvjiang 全仓统一的 **BGR** numpy 数组（CaptureBackend 产出）；
  颜色参数一律按 **RGB** 给（`#rrggbb` 或 (r, g, b)），与截图工具取色一致。
- 坐标全部是**像素**整数，闭区间 [x1, x2] × [y1, y2]；归一化 ↔ 像素的换算
  由上层（DSL 内置函数）按画布做，这里不感知布局。
- 本模块只依赖 numpy / cv2，不 import 引擎，保证能在无 Qt、无设备的
  环境下用截图文件离线回归。
"""
from __future__ import annotations

import math
from typing import Sequence

import cv2
import numpy as np

RGB = tuple[int, int, int]

#: channel 参数约定：0=red 1=green 2=blue
CH_RED, CH_GREEN, CH_BLUE = 0, 1, 2

# BGR 数组里 R/G/B 各自所在的通道下标
_BGR_INDEX = {CH_RED: 2, CH_GREEN: 1, CH_BLUE: 0}


def parse_hex(color: str | Sequence[int]) -> RGB:
    """`#rrggbb` / `rrggbb` / (r, g, b) → (r, g, b)"""
    if isinstance(color, str):
        s = color.strip().lstrip("#")
        if len(s) != 6:
            raise ValueError(f"颜色格式应为 #rrggbb，实际: {color!r}")
        v = int(s, 16)
        return (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF
    if len(color) != 3:
        raise ValueError(f"颜色应为 3 元组 (r, g, b)，实际: {color!r}")
    return int(color[0]), int(color[1]), int(color[2])


def _clip_rect(img: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> tuple[int, int, int, int]:
    """把闭区间矩形裁到图像范围内；越界坐标夹到边缘而不是抛错（与 Kotlin px()/py() 一致）"""
    h, w = img.shape[:2]
    x1 = min(max(int(x1), 0), w - 1)
    x2 = min(max(int(x2), 0), w - 1)
    y1 = min(max(int(y1), 0), h - 1)
    y2 = min(max(int(y2), 0), h - 1)
    return x1, y1, x2, y2


def _rgb_planes(img: np.ndarray, x1: int, y1: int, x2: int, y2: int, step: int = 1):
    """取子区域并按 step 采样，返回 (r, g, b) 三个 int16 平面"""
    step = max(int(step), 1)
    sub = img[y1:y2 + 1:step, x1:x2 + 1:step]
    b = sub[..., 0].astype(np.int16)
    g = sub[..., 1].astype(np.int16)
    r = sub[..., 2].astype(np.int16)
    return r, g, b


# ─── 取点 ─────────────────────────────────────────────────

def pixel_rgb(img: np.ndarray, x: int, y: int) -> RGB:
    """单点取色 → (r, g, b)"""
    x, y, _, _ = _clip_rect(img, x, y, x, y)
    b, g, r = img[y, x][:3]
    return int(r), int(g), int(b)


def brightness(img: np.ndarray, x: int, y: int) -> int:
    """单点亮度 = r + g + b（0–765）。Kotlin 版同名语义，阈值可直接复用。"""
    r, g, b = pixel_rgb(img, x, y)
    return r + g + b


# ─── 色占比 ───────────────────────────────────────────────

def color_ratio(
    img: np.ndarray,
    x1: int, y1: int, x2: int, y2: int,
    lo: RGB, hi: RGB,
    step: int = 1,
) -> float:
    """区域内 RGB 三通道各落在 [lo, hi] 闭区间的像素占比（0.0–1.0）

    这是状态判定的主力原语：「右下角绿按钮在不在」「顶部罗盘白字占比」都是它。
    lo/hi 分别给三通道下界/上界，可表达非对称范围（如 r∈[0,90] g∈[130,255]）。
    step 是采样步长，>1 时只数网格点，结果是统计近似；空区域返回 0。
    """
    x1, y1, x2, y2 = _clip_rect(img, x1, y1, x2, y2)
    if x2 < x1 or y2 < y1:
        return 0.0
    r, g, b = _rgb_planes(img, x1, y1, x2, y2, step)
    if r.size == 0:
        return 0.0
    mask = (
        (r >= lo[0]) & (r <= hi[0])
        & (g >= lo[1]) & (g <= hi[1])
        & (b >= lo[2]) & (b <= hi[2])
    )
    return float(mask.sum()) / float(mask.size)


def color_ratio_tol(
    img: np.ndarray,
    x1: int, y1: int, x2: int, y2: int,
    color: RGB, tol: int,
    step: int = 1,
) -> float:
    """color_ratio 的对称便捷形式：目标色 ± tol"""
    lo = tuple(max(c - tol, 0) for c in color)
    hi = tuple(min(c + tol, 255) for c in color)
    return color_ratio(img, x1, y1, x2, y2, lo, hi, step)  # type: ignore[arg-type]


# ─── 亮段计数 ─────────────────────────────────────────────

def bright_segments(
    img: np.ndarray,
    y: int, x1: int, x2: int,
    on_min: int, off_max: int,
) -> int:
    """沿水平线 y 从 x1 扫到 x2，数「亮→暗」跳变次数

    用途：数底部页签栏上有几段文字（大厅 ≥4 段、结算页 <3 段）。
    亮度 > on_min 进入亮段，< off_max 退出并计 1；两阈值之间是迟滞带。
    注意一条到行尾仍未退出的亮段不计数（与 Kotlin 一致）。
    """
    x1, y, x2, _ = _clip_rect(img, x1, y, x2, y)
    if x2 < x1:
        return 0
    row = img[y, x1:x2 + 1, :3].astype(np.int32)
    bright = row.sum(axis=1)
    segs = 0
    in_bright = False
    for v in bright:
        if v > on_min and not in_bright:
            in_bright = True
        elif v < off_max and in_bright:
            in_bright = False
            segs += 1
    return segs


# ─── 主导通道掩膜（colorVec / findIcons 共用）────────────────

def _dominant_mask(
    r: np.ndarray, g: np.ndarray, b: np.ndarray,
    channel: int, c_lo: int, c_hi: int,
    margin1: int, margin2: int, o_max: int,
) -> np.ndarray:
    """「某一通道主导」像素掩膜

    c ∈ [c_lo, c_hi] 且 c − other1 ≥ margin1 且 c − other2 ≥ margin2 且 other ≤ o_max。
    other1/other2 的配对与 Kotlin 一致：green → (r, b)，red → (g, b)，blue → (r, g)。
    对半透明叠加层（小地图路线、标记）比绝对 RGB 范围稳——底图混色时
    绝对值漂，但「绿远大于红蓝」这个关系不变。
    """
    if channel == CH_RED:
        c, o1, o2 = r, g, b
    elif channel == CH_BLUE:
        c, o1, o2 = b, r, g
    else:
        c, o1, o2 = g, r, b
    return (
        (c >= c_lo) & (c <= c_hi)
        & ((c - o1) >= margin1) & ((c - o2) >= margin2)
        & (o1 <= o_max) & (o2 <= o_max)
    )


# ─── 色心方位角 ───────────────────────────────────────────

def color_vec(
    img: np.ndarray,
    x1: int, y1: int, x2: int, y2: int,
    cx: float, cy: float,
    c_lo: int, c_hi: int, margin: int,
    channel: int = CH_GREEN,
    step: int = 1,
    min_r: float = 0.0,
    max_r: float = math.inf,
) -> tuple[float, int]:
    """主导通道像素相对中心 (cx, cy) 的单位向量合成方位角

    返回 (deg, count)：deg 以屏幕上方为 0°、顺时针 0–360；count 是参与投票的
    像素数，为 0 时 deg = -1。每个命中像素贡献一个单位向量，所以环形/均匀
    噪声会自相抵消，只剩不对称的那一撮（路线方向、朝向楔）。
    min_r/max_r 是到中心的像素距离环带，用来把「朝向楔」和外圈「路线」分开。
    """
    x1, y1, x2, y2 = _clip_rect(img, x1, y1, x2, y2)
    if x2 < x1 or y2 < y1:
        return -1.0, 0
    step = max(int(step), 1)
    r, g, b = _rgb_planes(img, x1, y1, x2, y2, step)
    mask = _dominant_mask(r, g, b, channel, c_lo, c_hi, margin, margin, 255)
    if not mask.any():
        return -1.0, 0
    ys, xs = np.nonzero(mask)
    px = x1 + xs * step
    py = y1 + ys * step
    dx = px.astype(np.float64) - float(cx)
    dy = py.astype(np.float64) - float(cy)
    d2 = dx * dx + dy * dy
    band = (d2 > 0) & (d2 >= min_r * min_r) & (d2 <= max_r * max_r)
    if not band.any():
        return -1.0, 0
    d = np.sqrt(d2[band])
    sum_x = float((dx[band] / d).sum())
    sum_y = float((dy[band] / d).sum())
    n = int(band.sum())
    deg = (math.degrees(math.atan2(sum_x, -sum_y)) + 360.0) % 360.0
    return deg, n


# ─── 多点找色 ─────────────────────────────────────────────

def find_multi_color(
    img: np.ndarray,
    x1: int, y1: int, x2: int, y2: int,
    anchor: RGB,
    points: Sequence[tuple[int, int, RGB]],
    tol: int = 12,
) -> tuple[int, int] | None:
    """按键精灵式多点找色：锚点色 + 若干 (dx, dy, color) 偏移点全部命中即返回锚点像素坐标

    扫描顺序行优先、左上角优先，返回第一个命中；无命中返回 None。
    dx/dy 是**当前帧像素**偏移，录制分辨率不同时调用方先按比例缩放。
    实现：先对锚点色做一次整区域掩膜，再只在候选点上逐个验证偏移点，
    避免 Python 级双重循环扫全图。
    """
    x1, y1, x2, y2 = _clip_rect(img, x1, y1, x2, y2)
    if x2 < x1 or y2 < y1:
        return None
    h, w = img.shape[:2]
    r, g, b = _rgb_planes(img, x1, y1, x2, y2, 1)

    def near_mask(rp, gp, bp, color: RGB) -> np.ndarray:
        return (
            (np.abs(rp - color[0]) <= tol)
            & (np.abs(gp - color[1]) <= tol)
            & (np.abs(bp - color[2]) <= tol)
        )

    cand = near_mask(r, g, b, anchor)
    if not cand.any():
        return None
    ys, xs = np.nonzero(cand)
    full = img[..., :3].astype(np.int16)
    for yy, xx in zip(ys, xs, strict=True):
        ax, ay = x1 + int(xx), y1 + int(yy)
        ok = True
        for dx, dy, color in points:
            px_, py_ = ax + int(dx), ay + int(dy)
            if px_ < 0 or px_ >= w or py_ < 0 or py_ >= h:
                ok = False
                break
            bb, gg, rr = full[py_, px_]
            if abs(rr - color[0]) > tol or abs(gg - color[1]) > tol or abs(bb - color[2]) > tol:
                ok = False
                break
        if ok:
            return ax, ay
    return None


# ─── 同色图标连通分量 ─────────────────────────────────────

def find_icons(
    img: np.ndarray,
    x1: int, y1: int, x2: int, y2: int,
    channel: int, c_min: int, margin1: int,
    margin2: int | None = None,
    o_max: int = 255,
    min_area: int = 60,
    min_bbox: int = 0,
    c_max: int = 255,
) -> list[tuple[float, float, int, int, int]]:
    """区域内「某通道主导色」的 8 连通色块 → [(cx, cy, area, w, h), ...]，按面积降序

    用途：每局随机位置的离散图标（地图上的目标标记），静态坐标失效，只能实时找。
    min_area 滤掉细小的"!"标记；min_bbox（宽高同时 ≥）把 ~40px 的图标和同色
    ~24px 队友①②③标记分开。cx/cy 是 bbox 中心的**像素**坐标（float）。
    """
    x1, y1, x2, y2 = _clip_rect(img, x1, y1, x2, y2)
    if x2 < x1 or y2 < y1:
        return []
    if margin2 is None:
        margin2 = margin1
    r, g, b = _rgb_planes(img, x1, y1, x2, y2, 1)
    mask = _dominant_mask(r, g, b, channel, c_min, c_max, margin1, margin2, o_max)
    if not mask.any():
        return []
    n, _labels, stats, _cent = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    out: list[tuple[float, float, int, int, int]] = []
    # label 0 是背景
    for i in range(1, n):
        left, top, w, h, area = (int(v) for v in stats[i])
        if area >= min_area and w >= min_bbox and h >= min_bbox:
            cx = x1 + left + (w - 1) / 2.0
            cy = y1 + top + (h - 1) / 2.0
            out.append((cx, cy, area, w, h))
    out.sort(key=lambda t: t[2], reverse=True)
    return out
