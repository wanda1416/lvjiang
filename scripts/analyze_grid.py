"""分析 data/screenshots 中图片的网格特征

输出：
- 图片尺寸、通道数
- 灰度直方图分布
- 边缘检测后的线条信息
- 网格线检测结果（水平/垂直线位置）
- 轮廓检测结果（矩形轮廓面积分布）
- 模板自相关检测（格子周期性）
"""

import cv2
import numpy as np
from pathlib import Path
from collections import Counter

SCREENSHOTS_DIR = Path(__file__).parent.parent / "data" / "screenshots"


def analyze_image(path: Path):
    """分析单张图片的特征"""
    # 读取（支持中文路径）
    data = path.read_bytes()
    buf = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
    if img is None:
        print(f"  [ERROR] 无法读取: {path.name}")
        return

    print(f"\n{'='*60}")
    print(f"文件: {path.name}")
    print(f"尺寸: {img.shape[1]}x{img.shape[0]}, 通道: {img.shape[2] if len(img.shape) > 2 else 1}")

    # 转灰度
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img

    print(f"灰度范围: [{gray.min()}, {gray.max()}], 均值: {gray.mean():.1f}, 标准差: {gray.std():.1f}")

    # ── 1. 边缘检测 ──
    edges = cv2.Canny(gray, 50, 150)
    edge_ratio = np.count_nonzero(edges) / edges.size
    print(f"\n边缘检测: 边缘像素占比 {edge_ratio:.4f}")

    # ── 2. 网格线检测（形态学） ──
    # 自适应二值化
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY_INV, 11, 2)

    # 水平线
    h_len = max(gray.shape[1] // 30, 15)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_len, 1))
    h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
    h_coords = np.where(h_lines.sum(axis=1) > gray.shape[1] * 0.3)[0]
    h_groups = _group_consecutive(h_coords)
    print(f"\n水平网格线: 检测到 {len(h_groups)} 条")
    for i, (start, end) in enumerate(h_groups[:20]):
        mid = (start + end) // 2
        print(f"  线 {i}: y={mid} (范围 {start}-{end}, 厚度 {end-start+1})")

    # 垂直线
    v_len = max(gray.shape[0] // 30, 15)
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_len))
    v_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel)
    v_coords = np.where(v_lines.sum(axis=0) > gray.shape[0] * 0.3)[0]
    v_groups = _group_consecutive(v_coords)
    print(f"\n垂直网格线: 检测到 {len(v_groups)} 条")
    for i, (start, end) in enumerate(v_groups[:20]):
        mid = (start + end) // 2
        print(f"  线 {i}: x={mid} (范围 {start}-{end}, 厚度 {end-start+1})")

    # ── 3. 网格周期性分析 ──
    if len(h_groups) >= 2 and len(v_groups) >= 2:
        h_mids = [(s + e) // 2 for s, e in h_groups]
        v_mids = [(s + e) // 2 for s, e in v_groups]
        h_gaps = np.diff(h_mids)
        v_gaps = np.diff(v_mids)
        if len(h_gaps) > 0:
            print(f"\n水平间距: min={h_gaps.min()}, max={h_gaps.max()}, "
                  f"mean={h_gaps.mean():.1f}, std={h_gaps.std():.1f}")
        if len(v_gaps) > 0:
            print(f"垂直间距: min={v_gaps.min()}, max={v_gaps.max()}, "
                  f"mean={v_gaps.mean():.1f}, std={v_gaps.std():.1f}")

    # ── 4. 轮廓检测 ──
    contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    rect_contours = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < 100:  # 过滤太小的
            continue
        peri = cv2.arcLength(c, True)
        if peri == 0:
            continue
        approx = cv2.approxPolyDP(c, 0.04 * peri, True)
        if len(approx) == 4:  # 矩形
            x, y, w, h = cv2.boundingRect(approx)
            aspect = w / h if h > 0 else 0
            rect_contours.append((x, y, w, h, area, aspect))

    print(f"\n矩形轮廓: 共 {len(rect_contours)} 个")
    if rect_contours:
        # 按面积排序，取前 30
        rect_contours.sort(key=lambda r: r[4], reverse=True)
        areas = [r[4] for r in rect_contours[:50]]
        aspects = [r[5] for r in rect_contours[:50]]
        print(f"  面积范围(top50): [{min(areas)}, {max(areas)}], 均值: {np.mean(areas):.0f}")
        print(f"  宽高比范围(top50): [{min(aspects):.2f}, {max(aspects):.2f}]")

        # 聚类：面积最集中的区间
        area_counter = Counter()
        for r in rect_contours:
            # 按 100 为桶
            bucket = (r[4] // 100) * 100
            area_counter[bucket] += 1
        top_buckets = area_counter.most_common(5)
        print(f"  面积分布 top5 桶: {top_buckets}")

        # 打印前 10 个最大矩形
        print(f"  最大 10 个矩形:")
        for i, (x, y, w, h, area, aspect) in enumerate(rect_contours[:10]):
            print(f"    {i}: pos=({x},{y}) size={w}x{h} area={area} aspect={aspect:.2f}")

    # ── 5. 颜色特征（如果有通道） ──
    if len(img.shape) == 3 and img.shape[2] >= 3:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        h_ch, s_ch, v_ch = hsv[:,:,0], hsv[:,:,1], hsv[:,:,2]
        print(f"\nHSV 分析:")
        print(f"  H: mean={h_ch.mean():.1f} std={h_ch.std():.1f}")
        print(f"  S: mean={s_ch.mean():.1f} std={s_ch.std():.1f}")
        print(f"  V: mean={v_ch.mean():.1f} std={v_ch.std():.1f}")

        # 主色调
        h_flat = h_ch.flatten()
        s_flat = s_ch.flatten()
        # 只看饱和度高的像素（有颜色的）
        colored = h_flat[s_flat > 50]
        if len(colored) > 0:
            h_hist, _ = np.histogram(colored, bins=18, range=(0, 180))
            dominant_h = np.argmax(h_hist) * 10 + 5
            print(f"  主色调: H≈{dominant_h}° (占比 {h_hist.max()/len(colored)*100:.1f}%)")

    # ── 6. 行/列像素方差分析（找格子边界） ──
    row_var = np.array([np.var(gray[y, :].astype(float)) for y in range(gray.shape[0])])
    col_var = np.array([np.var(gray[:, x].astype(float)) for x in range(gray.shape[1])])

    # 方差突变点 = 可能的格子边界
    row_edges = _find_variance_edges(row_var)
    col_edges = _find_variance_edges(col_var)
    print(f"\n行方差突变点(格子水平边界): {len(row_edges)} 个")
    if row_edges:
        gaps = np.diff(row_edges)
        print(f"  位置: {row_edges[:30]}")
        if len(gaps) > 0:
            print(f"  间距: min={gaps.min()}, max={gaps.max()}, mean={gaps.mean():.1f}, std={gaps.std():.1f}")

    print(f"\n列方差突变点(格子垂直边界): {len(col_edges)} 个")
    if col_edges:
        gaps = np.diff(col_edges)
        print(f"  位置: {col_edges[:30]}")
        if len(gaps) > 0:
            print(f"  间距: min={gaps.min()}, max={gaps.max()}, mean={gaps.mean():.1f}, std={gaps.std():.1f}")


def _group_consecutive(coords, gap=3):
    """将连续坐标分组为线段"""
    if len(coords) == 0:
        return []
    groups = []
    start = coords[0]
    prev = coords[0]
    for c in coords[1:]:
        if c - prev > gap:
            groups.append((start, prev))
            start = c
        prev = c
    groups.append((start, prev))
    return groups


def _find_variance_edges(variances, threshold_ratio=0.5, min_prominence=20):
    """找方差序列中的突变点（格子边界）

    策略：找方差值突然升高或降低的位置
    """
    if len(variances) < 5:
        return []

    # 计算方差的梯度
    grad = np.diff(variances)
    abs_grad = np.abs(grad)

    # 阈值：取梯度的中位数 + ratio * (max - median)
    median_grad = np.median(abs_grad)
    max_grad = np.max(abs_grad)
    threshold = median_grad + threshold_ratio * (max_grad - median_grad)

    # 找超过阈值的点
    edge_indices = np.where(abs_grad > threshold)[0]

    if len(edge_indices) == 0:
        return []

    # 合并相邻的突变点
    groups = _group_consecutive(edge_indices, gap=5)
    # 取每组的中间位置
    result = [(s + e) // 2 for s, e in groups if (e - s) < 10]

    # 过滤：只保留梯度变化足够大的（真正的边界）
    filtered = []
    for idx in result:
        if idx < len(abs_grad) and abs_grad[idx] > min_prominence:
            filtered.append(idx)

    return filtered


def main():
    print("分析 data/screenshots 中的图片特征")
    print(f"目录: {SCREENSHOTS_DIR}")

    if not SCREENSHOTS_DIR.exists():
        print(f"目录不存在: {SCREENSHOTS_DIR}")
        return

    files = sorted(SCREENSHOTS_DIR.glob("*"))
    image_files = [f for f in files if f.suffix.lower() in ('.png', '.jpg', '.bmp', '.jpeg')]

    print(f"找到 {len(image_files)} 个图片文件")

    for f in image_files:
        analyze_image(f)


if __name__ == "__main__":
    main()
