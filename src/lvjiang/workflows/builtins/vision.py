"""内置函数 - 图色（取色 / 色占比 / 亮段 / 色心方位 / 多点找色 / 同色图标）

把 ``core.recognizers.color_ops`` 的像素级原语接到 DSL：
入参用 CoordRef（``$r = [scene].[region]`` 的求值结果）或 find 产出的 FoundRegion，
内部按当前布局画布换算成像素，每次调用截一帧。

为什么是内置函数而不是新指令：这些是标量/列表求值，进 ``eval`` 和条件表达式
最自然（``if color_ratio($btn, "#2ecc71", 40) > 0.3``）；``scan/recognize/find``
三条指令的语义是「区域 → 结构化结果存变量」，不合适。

坐标/尺寸参数一律归一化：
- 区域/点：CoordRef / FoundRegion（画布归一化）
- 距离类参数（color_vec 的 min_r/max_r、find_icons 的 min_bbox）：**画布高**的比例
- find_multi_color 的偏移 dx/dy：**画布宽**的比例
这样同一套阈值跨分辨率可用；按像素调出来的阈值换算：px / 画布高 或 px / 画布宽。
"""
from __future__ import annotations

from typing import Any

import numpy as np
from loguru import logger

from ...core.coord_types import CircleCoordRef, CoordRef, RectCoordRef
from ...core.layout_models import FoundRegion
from ...core.recognizers import color_ops
from ...i18n import tr
from ._coerce import to_number
from ._registry import builtin_func

# ─── 画布与坐标换算 ─────────────────────────────────────

def _canvas_frame(engine) -> np.ndarray:
    """截一帧并裁到布局画布；拿不到帧直接抛错（坐标全错，不能静默）"""
    if engine is None:
        raise ValueError(tr("图色函数需要在工作流引擎中调用"))
    img = engine._capture.capture()
    if img is None:
        raise ValueError(tr("图色函数：截图失败"))
    canvas = engine._layout.get_canvas()
    h, w = img.shape[:2]
    x1 = int(canvas.x_ratio * w)
    y1 = int(canvas.y_ratio * h)
    x2 = int((canvas.x_ratio + canvas.w_ratio) * w)
    y2 = int((canvas.y_ratio + canvas.h_ratio) * h)
    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError(tr("图色函数：画布裁剪为空（检查布局 canvas 配置）"))
    return crop


def _as_rect(ref: Any, what: str) -> tuple[float, float, float, float]:
    """CoordRef / FoundRegion → 归一化 (x1, y1, x2, y2)

    点（无宽高）退化为零面积矩形；CircleCoordRef 取外接正方形。
    """
    if isinstance(ref, FoundRegion):
        return ref.x_ratio, ref.y_ratio, ref.x_ratio + ref.w_ratio, ref.y_ratio + ref.h_ratio
    if isinstance(ref, RectCoordRef):
        return ref.cx - ref.w / 2, ref.cy - ref.h / 2, ref.cx + ref.w / 2, ref.cy + ref.h / 2
    if isinstance(ref, CircleCoordRef):
        return ref.cx - ref.r, ref.cy - ref.r, ref.cx + ref.r, ref.cy + ref.r
    if isinstance(ref, CoordRef):
        return ref.cx, ref.cy, ref.cx, ref.cy
    # 画布上框/点出来的字面量：(x, y, w, h) 矩形、(x, y) 点
    if isinstance(ref, (tuple, list)) and len(ref) == 4 and all(isinstance(v, (int, float)) for v in ref):
        x, y, w, h = (float(v) for v in ref)
        return x, y, x + w, y + h
    if isinstance(ref, (tuple, list)) and len(ref) == 2 and all(isinstance(v, (int, float)) for v in ref):
        x, y = float(ref[0]), float(ref[1])
        return x, y, x, y
    raise ValueError(tr("{what}: 需要坐标引用（$var = [scene].[region]、find 结果或 (x, y[, w, h]) 字面量），实际: {t}").format(
        what=what, t=type(ref).__name__))


def _as_center(ref: Any, what: str) -> tuple[float, float]:
    if isinstance(ref, FoundRegion):
        return ref.center_ratios()
    if isinstance(ref, CoordRef):
        return ref.cx, ref.cy
    if isinstance(ref, (tuple, list)) and len(ref) in (2, 4) and all(isinstance(v, (int, float)) for v in ref):
        x1, y1, x2, y2 = _as_rect(ref, what)
        return (x1 + x2) / 2, (y1 + y2) / 2
    raise ValueError(tr("{what}: 需要坐标引用，实际: {t}").format(what=what, t=type(ref).__name__))


def _rect_px(frame: np.ndarray, ref: Any, what: str) -> tuple[int, int, int, int]:
    """归一化矩形 → 画布像素闭区间。x2/y2 用 ceil-1 语义避免零宽区域丢像素。"""
    h, w = frame.shape[:2]
    rx1, ry1, rx2, ry2 = _as_rect(ref, what)
    x1 = int(rx1 * w)
    y1 = int(ry1 * h)
    x2 = max(int(rx2 * w) - 1, x1)
    y2 = max(int(ry2 * h) - 1, y1)
    return x1, y1, x2, y2


def _int(val, name: str, default: int | None = None) -> int:
    if val is None and default is not None:
        return default
    n = to_number(val)
    if n is None:
        raise ValueError(tr("{name}: 需要数值，实际: {v!r}").format(name=name, v=val))
    return int(n)


def _float(val, name: str, default: float | None = None) -> float:
    if val is None and default is not None:
        return default
    n = to_number(val)
    if n is None:
        raise ValueError(tr("{name}: 需要数值，实际: {v!r}").format(name=name, v=val))
    return float(n)


def _found(frame: np.ndarray, cx_px: float, cy_px: float, w_px: float, h_px: float, text: str = "") -> FoundRegion:
    """像素 bbox 中心/宽高 → 画布归一化 FoundRegion（可直接 click）"""
    h, w = frame.shape[:2]
    return FoundRegion(
        x_ratio=(cx_px - w_px / 2) / w,
        y_ratio=(cy_px - h_px / 2) / h,
        w_ratio=w_px / w,
        h_ratio=h_px / h,
        text=text,
    )


# ─── 取点 ───────────────────────────────────────────────

@builtin_func("pixel")
def _pixel(_engine=None, ref=None) -> list[int]:
    """取坐标中心点的颜色 → [r, g, b]

    .wf 用法:
        $p = [game].[slot_1]
        $rgb = pixel($p)
        if $rgb[0] > 200
    """
    frame = _canvas_frame(_engine)
    cx, cy = _as_center(ref, "pixel")
    h, w = frame.shape[:2]
    r, g, b = color_ops.pixel_rgb(frame, int(cx * w), int(cy * h))
    return [r, g, b]


@builtin_func("bright")
def _bright(_engine=None, ref=None) -> int:
    """取坐标中心点亮度 = r + g + b（0–765）

    .wf 用法:
        $x = [map].[close_btn]
        if bright($x) > 300
    """
    frame = _canvas_frame(_engine)
    cx, cy = _as_center(ref, "bright")
    h, w = frame.shape[:2]
    return color_ops.brightness(frame, int(cx * w), int(cy * h))


# ─── 色占比 ─────────────────────────────────────────────

@builtin_func("color_ratio")
def _color_ratio(_engine=None, ref=None, lo=None, hi_or_tol=None, step=1) -> float:
    """区域内目标色像素占比（0.0–1.0）

    两种写法：
    - color_ratio($rect, "#2ecc71", 40)          目标色 ± 40 容差（三通道对称）
    - color_ratio($rect, "#005a28", "#ffffa0")   三通道各自的 [下界, 上界]（非对称范围）
    第 4 参 step 采样步长（默认 1 全采）。

    .wf 用法:
        $btn = [lobby].[depart]
        if color_ratio($btn, "#005a28", "#5affa0") >= 0.05
            log "大厅就绪"
        end
    """
    frame = _canvas_frame(_engine)
    x1, y1, x2, y2 = _rect_px(frame, ref, "color_ratio")
    if lo is None:
        raise ValueError(tr("color_ratio: 缺少颜色参数"))
    st = _int(step, "color_ratio.step", 1)
    if isinstance(hi_or_tol, str):
        return color_ops.color_ratio(
            frame, x1, y1, x2, y2,
            color_ops.parse_hex(lo), color_ops.parse_hex(hi_or_tol), st,
        )
    tol = _int(hi_or_tol, "color_ratio.tol", 0)
    return color_ops.color_ratio_tol(frame, x1, y1, x2, y2, color_ops.parse_hex(lo), tol, st)


# ─── 亮段计数 ───────────────────────────────────────────

@builtin_func("bright_segs")
def _bright_segs(_engine=None, ref=None, on_min=None, off_max=None) -> int:
    """沿区域中线水平扫描，数「亮→暗」跳变次数（底部页签栏有几段字）

    ref 取其垂直中心作为扫描行、左右边界作为扫描范围。
    on_min：亮度 > 此值进入亮段；off_max：亮度 < 此值退出并计 1。
    注意：段数随分辨率略变（缩放会合并细缝，1080p→540p 实测 21→18），
    阈值要留余量，别卡在精确值上。

    .wf 用法:
        $bar = [lobby].[tab_bar]
        if bright_segs($bar, 300, 150) >= 4
    """
    frame = _canvas_frame(_engine)
    x1, y1, x2, y2 = _rect_px(frame, ref, "bright_segs")
    y = (y1 + y2) // 2
    return color_ops.bright_segments(
        frame, y, x1, x2,
        _int(on_min, "bright_segs.on_min"), _int(off_max, "bright_segs.off_max"),
    )


# ─── 色心方位角 ─────────────────────────────────────────

@builtin_func("color_vec")
def _color_vec(
    _engine=None, ref=None, center=None,
    c_lo=None, c_hi=None, margin=None,
    channel=1, min_r=0, max_r=None, step=1,
) -> dict | None:
    """区域内「某通道主导」像素相对 center 的合成方位角

    返回 {"deg": 0–360（上方 0°，顺时针）, "count": 像素数}；无命中返回 null。
    channel: 0=red 1=green 2=blue；c_lo/c_hi 主导通道取值范围；margin 主导通道须
    比另外两通道各高出多少。min_r/max_r 是到 center 的距离环带，单位**画布高比例**。

    .wf 用法（小地图路线方向）:
        $mm = [hud].[minimap]
        $v = color_vec($mm, $mm, 120, 255, 40, 1, 0.02, 0.09)
        if $v
            log concat("route=", $v.deg)
        end
    """
    frame = _canvas_frame(_engine)
    h, _w = frame.shape[:2]
    x1, y1, x2, y2 = _rect_px(frame, ref, "color_vec")
    ccx, ccy = _as_center(center if center is not None else ref, "color_vec.center")
    max_px = float("inf") if max_r is None else _float(max_r, "color_vec.max_r") * h
    deg, n = color_ops.color_vec(
        frame, x1, y1, x2, y2,
        ccx * frame.shape[1], ccy * h,
        _int(c_lo, "color_vec.c_lo"), _int(c_hi, "color_vec.c_hi"), _int(margin, "color_vec.margin"),
        channel=_int(channel, "color_vec.channel", 1),
        step=_int(step, "color_vec.step", 1),
        min_r=_float(min_r, "color_vec.min_r", 0.0) * h,
        max_r=max_px,
    )
    if n == 0:
        return None
    return {"deg": deg, "count": n}


# ─── 同色图标连通分量 ───────────────────────────────────

@builtin_func("find_icons")
def _find_icons(
    _engine=None, ref=None,
    channel=1, c_min=None, margin1=None, margin2=None,
    o_max=255, min_area=0.0072, min_bbox=0.0, c_max=255,
) -> list[FoundRegion]:
    """区域内某通道主导色的连通色块 → [FoundRegion, ...]（按面积降序，可直接 click）

    用途：每局随机位置的离散图标（地图上的目标标记），静态坐标失效，只能实时找。
    min_area / min_bbox 都按**画布高比例**给：面积阈值 = (min_area × 画布高)² 像素，
    bbox 阈值 = min_bbox × 画布高 像素。按 1080p 像素调出的阈值换算示例：
    minArea=120 → sqrt(120)/1080 ≈ 0.0101，minBbox=32 → 32/1080 ≈ 0.0296。

    .wf 用法:
        $map = [fullmap].[canvas]
        $icons = find_icons($map, 1, 150, 35, 60, 190, 0.0101, 0.0296)
        if $icons is_empty
            log warn "未检测到目标图标"
        end
        $first = $icons[0]
        click $first
    """
    frame = _canvas_frame(_engine)
    h, _w = frame.shape[:2]
    x1, y1, x2, y2 = _rect_px(frame, ref, "find_icons")
    side = _float(min_area, "find_icons.min_area", 0.0072) * h
    blobs = color_ops.find_icons(
        frame, x1, y1, x2, y2,
        channel=_int(channel, "find_icons.channel", 1),
        c_min=_int(c_min, "find_icons.c_min"),
        margin1=_int(margin1, "find_icons.margin1"),
        margin2=None if margin2 is None else _int(margin2, "find_icons.margin2"),
        o_max=_int(o_max, "find_icons.o_max", 255),
        min_area=int(round(side * side)),
        min_bbox=int(round(_float(min_bbox, "find_icons.min_bbox", 0.0) * h)),
        c_max=_int(c_max, "find_icons.c_max", 255),
    )
    out = [_found(frame, cx, cy, bw, bh, text=f"icon#{i}") for i, (cx, cy, _a, bw, bh) in enumerate(blobs)]
    logger.debug(f"find_icons: {len(out)} 个色块")
    return out


# ─── 多点找色 ───────────────────────────────────────────

@builtin_func("find_multi_color")
def _find_multi_color(_engine=None, ref=None, anchor=None, points=None, tol=12):
    """多点找色：锚点色 + 偏移点全部命中 → FoundRegion（锚点位置，零尺寸）；未命中返回 ""

    points 是 [[dx, dy, "#rrggbb"], ...]，dx/dy 为**画布宽比例**（录制时 px / 录制宽）。

    .wf 用法:
        $area = [bag].[grid]
        $hit = find_multi_color($area, "#ffcc00", [[0.004, 0, "#ffffff"], [0, 0.006, "#000000"]], 16)
        if $hit
            click $hit
        end
    """
    frame = _canvas_frame(_engine)
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = _rect_px(frame, ref, "find_multi_color")
    if anchor is None:
        raise ValueError(tr("find_multi_color: 缺少锚点颜色"))
    pts: list[tuple[int, int, tuple[int, int, int]]] = []
    for item in (points or []):
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            raise ValueError(tr("find_multi_color: 偏移点应为 [dx, dy, \"#rrggbb\"]，实际: {v!r}").format(v=item))
        dx = int(round(_float(item[0], "find_multi_color.dx") * w))
        dy = int(round(_float(item[1], "find_multi_color.dy") * w))
        pts.append((dx, dy, color_ops.parse_hex(item[2])))
    hit = color_ops.find_multi_color(
        frame, x1, y1, x2, y2, color_ops.parse_hex(anchor), pts, _int(tol, "find_multi_color.tol", 12),
    )
    if hit is None:
        return ""
    return FoundRegion(x_ratio=hit[0] / w, y_ratio=hit[1] / h, w_ratio=0.0, h_ratio=0.0, text="multi_color")
