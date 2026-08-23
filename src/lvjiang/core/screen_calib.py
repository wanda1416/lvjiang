"""屏幕标定 — 用参照大厅截图 + 几组地标对应点，推出本设备的布局画布

布局（场景区域、点、箭头）的坐标都相对于**画布**（布局级别的游戏内容区）归一化。
脚本作者在自己的机器上适配好布局（比如 1080p 挖孔屏，画布 = 整屏），换一台机器，
游戏内容区常常不在同一位置：挖孔 / 刘海的安全区不同、宽高比不同导致留黑边、系统
缩放……于是所有区域一起偏。要适配的不是每个区域，而是**画布这一个矩形**——
这正是 layouts.yaml 里「别名布局 = extends + 不同 canvas」表达的东西。

本模块把求这个矩形的过程做成可计算的：

1. 布局目录下放一张参照截图 ``layouts/<布局>/_reference.png``（作者机器上的大厅画面，
   画布即该布局当时的 canvas）；
2. 在目标设备上停在同一画面截一张图；
3. 用户在参照图上点一个地标、在实时图上点同一个地标（或由模板匹配自动定位），
   至少两组、横纵坐标都拉开距离；
4. 每轴最小二乘拟合 ``live = c + u * size``（u 为地标在参照画布内的归一化坐标），
   得到实时图上的画布矩形，归一化后写回 layouts.yaml 的本地覆盖。

与 ScreenMap（设备端"截图像素 → 输入像素"）是两层：那层解决截图与触摸网格不重合，
这层解决游戏内容区在截图里的位置。绝大多数情况只需要这层。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from .config.resolver import get_resolver
from .layout_manager import _LAYOUTS_YAML_REL, _safe_name
from .layout_models import CanvasConfig

#: 布局目录下的参照截图文件名（下划线开头：布局加载只枚举场景 json，不会误读）
REFERENCE_IMAGE = "_reference.png"
#: 参照截图的 sidecar：拍它时的画布（没有 sidecar 时按系统分发的画布算）
REFERENCE_META = "_reference.json"
#: 自动定位地标时从参照图裁的方块边长（像素），够含一个按钮/图标
DEFAULT_PATCH_PX = 120
#: 模板匹配分数门槛（TM_CCOEFF_NORMED）
DEFAULT_MIN_SCORE = 0.6
#: 两点在某轴上的归一化间距低于此值视为"没拉开"，该轴无法拟合缩放
MIN_AXIS_SPREAD = 0.15


class CalibError(ValueError):
    pass


@dataclass(frozen=True)
class Correspondence:
    """一组地标对应：参照图像素坐标 ↔ 实时截图像素坐标"""

    ref_x: float
    ref_y: float
    live_x: float
    live_y: float


@dataclass
class CanvasSolution:
    canvas: CanvasConfig
    #: 拟合残差：各对应点按解出的画布回推到实时图，与用户给的点的最大偏差（像素）
    residual_px: float
    #: 每轴是否真的拟合了缩放（两点拉开了）；False 表示该轴只平移、缩放沿用参照
    scaled_x: bool
    scaled_y: bool

    def to_dict(self) -> dict:
        return {"canvas": self.canvas.to_dict(), "residual_px": self.residual_px,
                "scaled_x": self.scaled_x, "scaled_y": self.scaled_y}


# ─── 参照图 ────────────────────────────────────────────────

def reference_image_rel(layout_name: str) -> str:
    return f"layouts/{_safe_name(layout_name)}/{REFERENCE_IMAGE}"


def reference_image_path(layout_name: str) -> Path | None:
    """参照截图路径（local 覆盖优先于 system），没有返回 None"""
    return get_resolver().resolve_read(reference_image_rel(layout_name))


def load_reference_image(layout_name: str) -> np.ndarray | None:
    path = reference_image_path(layout_name)
    if path is None:
        return None
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        logger.warning(f"[Calib] 参照图读不出来: {path}")
    return img


def save_reference_image(layout_name: str, img_bgr: np.ndarray,
                         canvas: CanvasConfig | None = None) -> Path:
    """把一张截图存为布局的参照图（开发态写 system，用户态写 local），连同拍它时的画布"""
    import json

    ok, buf = cv2.imencode(".png", img_bgr)
    if not ok:
        raise CalibError("参照图编码失败")
    resolver = get_resolver()
    path = resolver.write_entity(reference_image_rel(layout_name), buf.tobytes())
    meta = {"canvas": (canvas or load_canvas(layout_name)).to_dict(),
            "size": [int(img_bgr.shape[1]), int(img_bgr.shape[0])]}
    resolver.write_entity(
        f"layouts/{_safe_name(layout_name)}/{REFERENCE_META}",
        json.dumps(meta, ensure_ascii=False, indent=2))
    return path


def reference_canvas(layout_name: str) -> CanvasConfig:
    """参照图对应的画布：sidecar 里记了就用它，否则按系统分发的画布"""
    import json

    path = get_resolver().resolve_read(f"layouts/{_safe_name(layout_name)}/{REFERENCE_META}")
    if path is not None:
        try:
            return CanvasConfig.from_dict(json.loads(path.read_text(encoding="utf-8")).get("canvas") or {})
        except (OSError, ValueError) as e:
            logger.warning(f"[Calib] 参照图 sidecar 读不出来，按系统画布: {e}")
    return system_canvas(layout_name)


# ─── 画布读写 ──────────────────────────────────────────────

def load_canvas(layout_name: str) -> CanvasConfig:
    """layouts.yaml（system ← local 合并）里该布局当前的画布；缺省整屏"""
    doc = get_resolver().load_merged(_LAYOUTS_YAML_REL)
    entry = doc.get("layouts", {}).get(layout_name) or {}
    return CanvasConfig.from_dict(entry.get("canvas") or {})


def system_canvas(layout_name: str) -> CanvasConfig:
    """随版本分发的画布（不含本地覆盖）——参照图就是按它拍的"""
    resolver = get_resolver()
    doc = resolver._load_yaml(resolver.system_dir / _LAYOUTS_YAML_REL)  # noqa: SLF001
    entry = doc.get("layouts", {}).get(layout_name) or {}
    return CanvasConfig.from_dict(entry.get("canvas") or {})


def save_canvas(layout_name: str, canvas: CanvasConfig) -> None:
    """把画布写成该布局的本地覆盖（用户态：只落 diff；与系统值一致则删覆盖）"""
    resolver = get_resolver()
    doc = resolver.load_merged(_LAYOUTS_YAML_REL)
    layouts = doc.setdefault("layouts", {})
    entry = layouts.setdefault(layout_name, {})
    entry["canvas"] = canvas.to_dict()
    resolver.save_merged(_LAYOUTS_YAML_REL, doc)
    logger.info(f"[Calib] 布局「{layout_name}」画布已保存: {canvas.to_dict()}")


def reset_canvas(layout_name: str) -> CanvasConfig:
    """撤掉本地覆盖，回到系统分发值"""
    canvas = system_canvas(layout_name)
    save_canvas(layout_name, canvas)
    return canvas


# ─── 地标自动定位 ──────────────────────────────────────────

def _gray(img: np.ndarray) -> np.ndarray:
    return img if img.ndim == 2 else cv2.cvtColor(img[..., :3], cv2.COLOR_BGR2GRAY)


def locate_landmark(
    ref_img: np.ndarray, ref_xy: tuple[float, float], live_img: np.ndarray,
    patch_px: int = DEFAULT_PATCH_PX, min_score: float = DEFAULT_MIN_SCORE,
    scales: tuple[float, ...] | None = None,
) -> tuple[float, float, float] | None:
    """在实时截图里找参照图 (ref_xy) 周围那块图案，返回其中心 (x, y, score)；找不到 None

    多尺度 TM_CCOEFF_NORMED：基准比例取两图宽度比，再 ±10% 和 1.0 兜底（同
    template_locator.adaptive_scales 的思路）。返回的是**参照点**在实时图里的位置——
    参照点不一定在 patch 正中（靠边时被裁），按裁剪偏移折算。
    """
    rh, rw = ref_img.shape[:2]
    lh, lw = live_img.shape[:2]
    half = patch_px // 2
    rx, ry = int(round(ref_xy[0])), int(round(ref_xy[1]))
    x0, y0 = max(0, rx - half), max(0, ry - half)
    x1, y1 = min(rw, rx + half), min(rh, ry + half)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    patch = _gray(ref_img)[y0:y1, x0:x1]
    live = _gray(live_img)
    # 参照点相对 patch 左上角的偏移（归一化，缩放后仍成立）
    fx = (rx - x0) / (x1 - x0)
    fy = (ry - y0) / (y1 - y0)

    if scales is None:
        base = lw / rw if rw > 0 else 1.0
        scales = (base * 0.9, base, base * 1.1) if abs(base - 1.0) > 0.02 else (0.9, 1.0, 1.1)
        if 1.0 not in scales:
            scales = (*scales, 1.0)

    best: tuple[float, float, float] | None = None
    for s in scales:
        tw, th = int(round(patch.shape[1] * s)), int(round(patch.shape[0] * s))
        if tw < 8 or th < 8 or tw > lw or th > lh:
            continue
        t = patch if (tw, th) == (patch.shape[1], patch.shape[0]) else cv2.resize(
            patch, (tw, th), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
        res = cv2.matchTemplate(live, t, cv2.TM_CCOEFF_NORMED)
        _mn, mx, _mnl, mxl = cv2.minMaxLoc(res)
        score = max(float(mx), 0.0)
        if best is None or score > best[2]:
            best = (mxl[0] + fx * tw, mxl[1] + fy * th, score)
    if best is None or best[2] < min_score:
        return None
    return best


# ─── 求解画布 ──────────────────────────────────────────────

def _fit_axis(us: list[float], lives: list[float], ref_size_px: float) -> tuple[float, float, bool]:
    """拟合 live = c + u * size；u 没拉开时只拟合平移、size 沿用参照画布尺寸。返回 (c, size, scaled)"""
    if len(us) >= 2 and (max(us) - min(us)) >= MIN_AXIS_SPREAD:
        a, b = np.polyfit(np.array(us), np.array(lives), 1)  # live = a*u + b
        return float(b), float(a), True
    c = float(np.mean([lv - u * ref_size_px for u, lv in zip(us, lives, strict=True)]))
    return c, ref_size_px, False


def solve_canvas(
    pairs: list[Correspondence],
    ref_canvas: CanvasConfig,
    ref_size: tuple[int, int],
    live_size: tuple[int, int],
) -> CanvasSolution:
    """由对应点解出实时截图上的画布

    每个参照点先换算成参照画布内的归一化坐标 (u, v)；实时图上的画布满足
    ``live_x = cx + u * cw``、``live_y = cy + v * ch``，逐轴最小二乘。
    只有一组点时按平移处理（缩放沿用参照画布在实时图上的等比尺寸）。
    """
    if not pairs:
        raise CalibError("至少要一组对应点")
    rw, rh = ref_size
    lw, lh = live_size
    if min(rw, rh, lw, lh) <= 0:
        raise CalibError("图像尺寸非法")
    # 参照画布在参照图中的像素矩形
    rcx, rcy = ref_canvas.x_ratio * rw, ref_canvas.y_ratio * rh
    rcw, rch = ref_canvas.w_ratio * rw, ref_canvas.h_ratio * rh
    if rcw <= 0 or rch <= 0:
        raise CalibError("参照画布尺寸为 0")
    us = [(p.ref_x - rcx) / rcw for p in pairs]
    vs = [(p.ref_y - rcy) / rch for p in pairs]
    # 单点 / 没拉开时的缩放兜底：参照画布按两图尺寸比**等比**缩放（取小的那个比例，
    # 即 letterbox 贴合——宽高比不同的机器上游戏通常就是这么渲染的）
    uniform = min(lw / rw, lh / rh)
    cx, cw, sx = _fit_axis(us, [p.live_x for p in pairs], rcw * uniform)
    cy, ch, sy = _fit_axis(vs, [p.live_y for p in pairs], rch * uniform)
    if cw <= 0 or ch <= 0:
        raise CalibError("解出的画布尺寸为负：对应点可能点反了")
    residual = max(
        max(abs(cx + u * cw - p.live_x), abs(cy + v * ch - p.live_y))
        for u, v, p in zip(us, vs, pairs, strict=True)
    )
    canvas = CanvasConfig(
        x_ratio=round(cx / lw, 6), y_ratio=round(cy / lh, 6),
        w_ratio=round(cw / lw, 6), h_ratio=round(ch / lh, 6),
    )
    return CanvasSolution(canvas=canvas, residual_px=float(residual), scaled_x=sx, scaled_y=sy)


def canvas_rect_px(canvas: CanvasConfig, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """画布在给定尺寸截图中的像素矩形 (x, y, w, h)"""
    w, h = size
    return (int(round(canvas.x_ratio * w)), int(round(canvas.y_ratio * h)),
            int(round(canvas.w_ratio * w)), int(round(canvas.h_ratio * h)))
