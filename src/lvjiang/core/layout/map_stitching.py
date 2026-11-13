"""同一缩放的地图截图按平移拼接；有效区域由调用方排除固定 UI。"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .maps import MapError


@dataclass
class MapMosaic:
    image: np.ndarray
    coverage: np.ndarray
    offsets: list[tuple[int, int]]


def stitch_map_frames(images: list[np.ndarray], masks: list[np.ndarray]) -> MapMosaic:
    """拼接有足够重叠纹理的地图截图，不推测未探查区域。

    masks 为与图片同尺寸的 uint8 有效像素掩码。只接受平移；缩放或旋转不一致、
    缺乏重叠的截图会拒绝合并，调用方可按地域分别保存。
    """
    if not images or len(images) != len(masks):
        raise MapError("需要图片及对应的有效区域")
    detector = cv2.SIFT_create(nfeatures=4000)
    features = []
    for image, mask in zip(images, masks, strict=True):
        if image.ndim != 3 or image.shape[2] != 3 or mask.shape != image.shape[:2]:
            raise MapError("地图图片和有效区域尺寸不匹配")
        if mask.dtype != np.uint8 or not np.any(mask):
            raise MapError("地图有效区域不能为空，且必须为 uint8 掩码")
        features.append(detector.detectAndCompute(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), mask))
    offsets: dict[int, np.ndarray] = {0: np.zeros(2)}
    remaining = set(range(1, len(images)))
    matcher = cv2.BFMatcher()
    while remaining:
        found = False
        for target in sorted(remaining):
            kp_b, desc_b = features[target]
            if desc_b is None:
                continue
            for source in list(offsets):
                kp_a, desc_a = features[source]
                if desc_a is None:
                    continue
                pairs = matcher.knnMatch(desc_b, desc_a, k=2)
                good = [pair[0] for pair in pairs
                        if len(pair) == 2 and pair[0].distance < 0.7 * pair[1].distance]
                if len(good) < 12:
                    continue
                points_b = np.asarray([kp_b[m.queryIdx].pt for m in good], dtype=np.float32)
                points_a = np.asarray([kp_a[m.trainIdx].pt for m in good], dtype=np.float32)
                matrix, inliers = cv2.estimateAffinePartial2D(
                    points_b, points_a, method=cv2.RANSAC, ransacReprojThreshold=2)
                if matrix is None or inliers is None:
                    continue
                accepted = inliers.ravel().astype(bool)
                if accepted.sum() < 12 or accepted.mean() < 0.5:
                    continue
                if not np.allclose(matrix[:, :2], np.eye(2), atol=0.01):
                    continue
                shift = np.median(points_a[accepted] - points_b[accepted], axis=0)
                error = np.linalg.norm(points_a[accepted] - points_b[accepted] - shift, axis=1)
                if np.median(error) > 1.5 or np.min(np.ptp(points_b[accepted], axis=0)) < 40:
                    continue
                offsets[target] = offsets[source] + shift
                remaining.remove(target)
                found = True
                break
            if found:
                break
        if not found:
            raise MapError("截图缺少同一缩放下的重叠地图纹理，请补拍相邻视口或按地域分别保存")
    positions = np.rint(np.array([offsets[i] for i in range(len(images))])).astype(int)
    positions -= positions.min(axis=0)
    width = max(x + image.shape[1] for (x, _), image in zip(positions, images, strict=True))
    height = max(y + image.shape[0] for (_, y), image in zip(positions, images, strict=True))
    canvas = np.full((height, width, 3), 40, dtype=np.uint8)
    quality = np.zeros((height, width), dtype=np.float32)
    for (x, y), image, mask in zip(positions, images, masks, strict=True):
        h, w = image.shape[:2]
        weight = cv2.distanceTransform((mask > 0).astype(np.uint8), cv2.DIST_L2, 3)
        area = quality[y:y+h, x:x+w]
        use = weight > area
        canvas[y:y+h, x:x+w][use] = image[use]
        area[use] = weight[use]
    covered = quality > 0
    ys, xs = np.where(covered)
    left, top, right, bottom = xs.min(), ys.min(), xs.max()+1, ys.max()+1
    return MapMosaic(canvas[top:bottom, left:right], covered[top:bottom, left:right],
                     [(int(x-left), int(y-top)) for x, y in positions])
