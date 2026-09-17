"""小地图朝向箭头解析 —— 居中的内凹型（燕尾形）箭头指向即视角/人物朝向

算法只依赖形状，不依赖模板，对分辨率与缩放不敏感：

1. 在小地图中心附近的搜索窗口内按 HSV 阈值取色（默认黄色）；
2. 取离窗口中心最近、面积足够的连通块作为箭头；
3. 求轮廓凸包与**凸性缺陷**：内凹箭头的缺口是最深的缺陷，缺口最深点在箭头
   尾部，"缺口点 → 离它最远的凸包点（尖端）"就是朝向；
4. 缺陷不明显（箭头被压住、颜色撞色）时退化为"质心 → 离质心最远的凸包点"，
   即尖端方向；两条路径给出的置信度不同。

角度约定：**罗盘方位角**，0° = 屏幕正上（北），顺时针为正，范围 [0, 360)。
入参图像是全仓统一的 BGR 数组；本模块只依赖 numpy / cv2，可离线回归。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

#: 默认黄色阈值（OpenCV HSV：H 0–180）
DEFAULT_HSV_LOWER = (18, 90, 120)
DEFAULT_HSV_UPPER = (42, 255, 255)


@dataclass(frozen=True)
class HeadingResult:
    """朝向解析结果。"""

    heading_deg: float          # 罗盘方位角，0=上，顺时针
    confidence: float           # 0–1；凸缺陷路径 ≥ 0.6，尖端退化路径 ≤ 0.5
    method: str                 # "notch" | "tip"
    centroid: tuple[float, float]   # 箭头质心（输入图像像素坐标）
    anchor: tuple[float, float]     # 缺口点或尖端点（像素坐标）
    area: int                   # 箭头像素面积


def bearing_from_vector(dx: float, dy: float) -> float:
    """图像坐标向量 (dx, dy)（y 向下）→ 罗盘方位角。"""
    return math.degrees(math.atan2(dx, -dy)) % 360.0


def _color_mask(img: np.ndarray, hsv_lower, hsv_upper) -> np.ndarray:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    lower = np.array(hsv_lower, dtype=np.uint8)
    upper = np.array(hsv_upper, dtype=np.uint8)
    if lower[0] <= upper[0]:
        return cv2.inRange(hsv, lower, upper)
    # 色相跨 0（红色）时拆两段
    a = cv2.inRange(hsv, np.array([0, lower[1], lower[2]], np.uint8), upper)
    b = cv2.inRange(hsv, lower, np.array([180, upper[1], upper[2]], np.uint8))
    return cv2.bitwise_or(a, b)


def detect_arrow_heading(
    img: np.ndarray,
    *,
    center: tuple[float, float] | None = None,
    window_ratio: float = 0.5,
    hsv_lower=DEFAULT_HSV_LOWER,
    hsv_upper=DEFAULT_HSV_UPPER,
    min_area: int = 12,
) -> HeadingResult | None:
    """解析 ``img``（小地图裁剪图，BGR）中心附近的箭头朝向。

    Args:
        center: 箭头应在的位置（像素）；None 取图像中心。
        window_ratio: 搜索窗口边长 = 图像短边 × window_ratio。
        hsv_lower / hsv_upper: 箭头颜色的 HSV 阈值。
        min_area: 小于该面积的色块视为噪声。

    Returns:
        找不到箭头返回 None；调用方必须按 None 停止移动，不得猜方向。
    """
    if img is None or img.ndim != 3 or img.shape[0] < 4 or img.shape[1] < 4:
        return None
    h, w = img.shape[:2]
    cx, cy = center if center is not None else ((w - 1) / 2.0, (h - 1) / 2.0)
    half = max(4, int(min(w, h) * max(0.05, window_ratio) / 2))
    x0, y0 = max(0, int(cx) - half), max(0, int(cy) - half)
    x1, y1 = min(w, int(cx) + half + 1), min(h, int(cy) + half + 1)
    window = img[y0:y1, x0:x1]
    if window.size == 0:
        return None

    mask = _color_mask(window, hsv_lower, hsv_upper)
    if not mask.any():
        return None
    # 一次闭运算把箭头描边/抗锯齿造成的细缝合上，不然缺口检测会被假缺陷干扰
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    local_center = np.array([cx - x0, cy - y0])
    best_label, best_score = -1, float("inf")
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        dist = float(np.linalg.norm(centroids[label] - local_center))
        # 箭头中心由布局显式标定（未标定才退化为裁剪中心），因此距离是最可靠
        # 的候选依据。不能用面积奖励：小地图上的大块黄色任务标记会反过来压过
        # 真正位于中心的玩家箭头。
        score = dist
        if score < best_score:
            best_label, best_score = label, score
    if best_label < 0:
        return None

    blob = (labels == best_label).astype(np.uint8)
    contours, _ = cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    area = int(stats[best_label, cv2.CC_STAT_AREA])
    moments = cv2.moments(blob, binaryImage=True)
    if moments["m00"] <= 0:
        return None
    gx, gy = moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]

    hull = cv2.convexHull(contour).reshape(-1, 2).astype(np.float64)
    notch = _deepest_notch(contour, area)
    if notch is not None:
        (nx, ny), (tx_, ty_), depth_ratio = notch
        # 尾部两翼中点 → 离它最远的凸包点（尖端）：基线是整个箭头长度，
        # 两翼中点又是对称量，比缺口像素本身稳得多
        tip_d = np.hypot(hull[:, 0] - tx_, hull[:, 1] - ty_)
        tip = hull[int(np.argmax(tip_d))]
        heading = bearing_from_vector(tip[0] - tx_, tip[1] - ty_)
        confidence = min(1.0, 0.6 + depth_ratio)
        return HeadingResult(
            heading_deg=heading, confidence=confidence, method="notch",
            centroid=(gx + x0, gy + y0), anchor=(nx + x0, ny + y0), area=area,
        )

    dists = np.hypot(hull[:, 0] - gx, hull[:, 1] - gy)
    tip = hull[int(np.argmax(dists))]
    heading = bearing_from_vector(tip[0] - gx, tip[1] - gy)
    # 尖端路径：最远点与次远点越接近，方向越不可信
    sorted_d = np.sort(dists)[::-1]
    margin = (sorted_d[0] - sorted_d[1]) / sorted_d[0] if len(sorted_d) > 1 and sorted_d[0] > 0 else 0.0
    return HeadingResult(
        heading_deg=heading, confidence=min(0.5, 0.2 + margin), method="tip",
        centroid=(gx + x0, gy + y0), anchor=(float(tip[0]) + x0, float(tip[1]) + y0),
        area=area,
    )


def _deepest_notch(
    contour: np.ndarray, area: int,
) -> tuple[tuple[float, float], tuple[float, float], float] | None:
    """内凹箭头尾部缺口：凸包最深的凸性缺陷。

    返回 ``((缺口最深点), (两翼中点), 深度/等效半径)``；两翼即该缺陷两端的
    凸包顶点。
    """
    if len(contour) < 5:
        return None
    hull_idx = cv2.convexHull(contour, returnPoints=False)
    if hull_idx is None or len(hull_idx) < 3:
        return None
    try:
        defects = cv2.convexityDefects(contour, hull_idx)
    except cv2.error:
        return None
    if defects is None or len(defects) == 0:
        return None
    # defects 形状随 OpenCV 版本为 (n,1,4) 或 (n,4)；depth 以 1/256 像素为单位
    rows = defects.reshape(-1, 4)
    deepest = rows[int(np.argmax(rows[:, 3]))]
    depth = float(deepest[3]) / 256.0
    radius = math.sqrt(max(area, 1) / math.pi)
    depth_ratio = depth / radius
    # 缺口至少要有等效半径的 1/4 深，才当成真正的燕尾缺口而不是描边毛刺
    if depth_ratio < 0.25:
        return None
    far = contour[int(deepest[2])][0]
    start = contour[int(deepest[0])][0]
    end = contour[int(deepest[1])][0]
    tail_mid = ((float(start[0]) + float(end[0])) / 2.0,
                (float(start[1]) + float(end[1])) / 2.0)
    return (float(far[0]), float(far[1])), tail_mid, depth_ratio


def draw_heading_overlay(img: np.ndarray, result: HeadingResult, *,
                         length: int | None = None) -> np.ndarray:
    """在副本上画出质心、锚点与朝向射线，供诊断/UI 叠加显示。"""
    out = img.copy()
    gx, gy = result.centroid
    length = length or max(20, min(img.shape[:2]) // 3)
    rad = math.radians(result.heading_deg)
    ex, ey = gx + length * math.sin(rad), gy - length * math.cos(rad)
    color = (0, 200, 0) if result.method == "notch" else (0, 165, 255)
    cv2.circle(out, (int(round(gx)), int(round(gy))), 3, (255, 255, 255), -1)
    cv2.circle(out, (int(round(result.anchor[0])), int(round(result.anchor[1]))), 3, (255, 0, 255), -1)
    cv2.arrowedLine(out, (int(round(gx)), int(round(gy))), (int(round(ex)), int(round(ey))),
                    color, 2, tipLength=0.25)
    return out
