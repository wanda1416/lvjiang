"""模板定位 —— 在画面里找「这张小图在哪」（图色三件套的"图"半边）

与 ``reference_matcher.ReferenceMatcher`` 的分工：
- ReferenceMatcher 是**分类器**：给一块已裁好的区域，回答"它最像图库里哪一条"
- 本模块是**定位器**：给整帧 + 一张模板，回答"模板出现在哪、多像"

分辨率自适应沿用 Airtest 的做法：Region 的布局模板绑定记录录制画布宽，
运行时按 ``当前画布宽 / record_w`` 先缩放模板，再在基准比例 ±10% 各试一次
兜底。声明分辨率自动缩放这套在业界没人
信（用户都在手写比例换算），所以这里不猜，只按录制尺寸换算。

模板文件只有 ``config/system/templates/<name>.png``，走 ConfigResolver 的
system/local 双层，用户可在 local 覆盖。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import cv2
import numpy as np
from loguru import logger

#: 模板目录（相对 config 层根）
TEMPLATES_REL_DIR = "templates"

#: 匹配分数默认门槛（TM_CCOEFF_NORMED，0–1）。与按键精灵系的 SAD 相似度（0–100）
#: 不是同一尺度，那类脚本里的 sim=85 不能直接搬；0.8 是 CCOEFF_NORMED 下的常用起点。
DEFAULT_MIN_SCORE = 0.8


def normalize_template_name(name: str) -> str:
    """规范模板逻辑名并拒绝越出 templates/ 的路径。"""
    value = str(name).strip()
    if value.endswith(".png"):
        value = value[:-4]
    path = PurePosixPath(value)
    if (not value or "\\" in value or path.is_absolute()
            or any(part in ("", ".", "..") for part in path.parts)):
        raise ValueError(f"非法模板名: {name!r}")
    return path.as_posix()


def validate_template_image(image: np.ndarray) -> None:
    """拒绝无法可靠用于相关匹配的模板图。"""
    if not isinstance(image, np.ndarray) or image.ndim not in (2, 3):
        raise ValueError("模板必须是灰度、BGR 或 BGRA 图像")
    h, w = image.shape[:2]
    if w < 4 or h < 4:
        raise ValueError("模板尺寸不能小于 4×4 像素")
    gray = _to_gray(image)
    if float(gray.std()) < 1.0:
        raise ValueError("模板近似纯色，无法可靠匹配；请框入更多图形边缘")


def template_from_image(name: str, image: np.ndarray,
                        record_w: int = 0, record_h: int = 0) -> Template:
    """从编辑器内存裁剪构造可立即测试的模板。"""
    base = normalize_template_name(name)
    validate_template_image(image)
    return Template(base, _to_gray(image), int(record_w), int(record_h))


def write_template(name: str, image: np.ndarray) -> Path:
    """按配置分层写 PNG；录制画布尺寸属于布局的 Region 绑定。"""
    tpl = template_from_image(name, image)
    base = tpl.name
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("模板 PNG 编码失败")
    from ..config.resolver import get_resolver
    resolver = get_resolver()
    return resolver.write_entity(
        f"{TEMPLATES_REL_DIR}/{base}.png", encoded.tobytes())


def delete_template(name: str) -> bool:
    """删除当前身份可写层的模板 PNG；不越权删除系统下发内容。"""
    base = normalize_template_name(name)
    rel_path = f"{TEMPLATES_REL_DIR}/{base}.png"
    from ..config.resolver import get_resolver
    resolver = get_resolver()
    if resolver.is_dev_mode():
        existed = (resolver.system_dir / rel_path).is_file()
        resolver.delete_entity(rel_path)
        return existed

    local = resolver.local_dir / rel_path
    if not local.is_file():
        return False
    if resolver.is_system_entity(rel_path):
        resolver.revert_entity_to_system(rel_path)
    else:
        resolver.delete_entity(rel_path)
    return True


@dataclass(frozen=True)
class Template:
    name: str
    gray: np.ndarray          # uint8，h×w
    record_w: int = 0         # 录制时画布宽；0 = 未知（不做基准缩放）
    record_h: int = 0

    @property
    def w(self) -> int:
        return int(self.gray.shape[1])

    @property
    def h(self) -> int:
        return int(self.gray.shape[0])


def with_record_size(
    template: Template, record_w: int, record_h: int,
) -> Template:
    """把布局绑定保存的录制画布尺寸应用到已加载 PNG。"""
    if record_w <= 0 or record_h <= 0:
        return template
    return Template(
        template.name, template.gray, int(record_w), int(record_h))


@dataclass(frozen=True)
class Located:
    """定位结果（像素，相对于传入的整帧）"""
    cx: float
    cy: float
    w: int            # 命中时的模板尺寸（已缩放）
    h: int
    score: float      # 0–1
    scale: float      # 命中时用的缩放比例


class TemplateStore:
    """按名字加载 + 缓存模板（灰度）

    ``base_dir`` 给定时直接从该目录读（测试/离线用）；否则走 ConfigResolver
    的 ``templates/`` 相对路径，local 覆盖 system。

    缓存按文件失效：每次 ``get`` 先 stat 一下 PNG，路径或 mtime 变了就重载，
    文件没了就丢缓存。脚本工作台里边调边截新模板、替换旧图，不用重启也不用手动
    ``invalidate``；代价是每次查找多两次 stat，相对模板匹配本身可以忽略。
    """

    def __init__(self, base_dir: Path | str | None = None):
        self._base_dir = Path(base_dir) if base_dir else None
        #: name → (文件签名, 模板)；签名 = (png 路径, png mtime)
        self._cache: dict[str, tuple[tuple[str, int], Template]] = {}

    def _resolve(self, rel: str) -> Path | None:
        if self._base_dir is not None:
            p = self._base_dir / rel
            return p if p.exists() else None
        from ..config.resolver import get_resolver
        return get_resolver().resolve_read(f"{TEMPLATES_REL_DIR}/{rel}")

    def _signature(self, base: str) -> tuple[str, int] | None:
        """当前磁盘上该模板的 (png, mtime)；PNG 不存在返回 None。"""
        png = self._resolve(f"{base}.png")
        if png is None:
            return None
        try:
            png_m = Path(png).stat().st_mtime_ns
        except OSError:
            return None
        return (str(png), png_m)

    def get(self, name: str) -> Template | None:
        base = normalize_template_name(name)
        sig = self._signature(base)
        if sig is None:
            self._cache.pop(base, None)
            logger.warning(f"模板不存在: {base}.png（目录 {TEMPLATES_REL_DIR}/）")
            return None
        cached = self._cache.get(base)
        if cached is not None and cached[0] == sig:
            return cached[1]
        tpl = self._load(base, Path(sig[0]))
        if tpl is None:
            self._cache.pop(base, None)
            return None
        self._cache[base] = (sig, tpl)
        return tpl

    def invalidate(self) -> None:
        self._cache.clear()

    def _load(self, base: str, png: Path) -> Template | None:
        data = np.fromfile(str(png), dtype=np.uint8)  # 路径含中文时 cv2.imread 在 Windows 上会失败
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if img is None:
            logger.warning(f"模板解码失败: {png}")
            return None
        try:
            validate_template_image(img)
        except ValueError as exc:
            logger.warning(f"模板无效 {png}: {exc}")
            return None
        return Template(name=base, gray=_to_gray(img))


def _to_gray(img: np.ndarray) -> np.ndarray:
    """BGR / BGRA / 灰度 → 灰度 uint8。带 alpha 的模板先合成到黑底，避免透明区当白色参与匹配。"""
    if img.ndim == 2:
        return img.astype(np.uint8)
    if img.shape[2] == 4:
        alpha = img[..., 3:4].astype(np.float32) / 255.0
        bgr = (img[..., :3].astype(np.float32) * alpha).astype(np.uint8)
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(img[..., :3], cv2.COLOR_BGR2GRAY)


def adaptive_scales(canvas_w: int, record_w: int) -> list[float]:
    """录制宽 → 当前宽的基准比例 ± 10%，再加 1.0 兜底"""
    base = canvas_w / record_w if record_w > 0 and canvas_w > 0 else 1.0
    if abs(base - 1.0) < 0.02:
        return [0.9, 1.0, 1.1]
    return [base * 0.9, base, base * 1.1, 1.0]


def locate(
    frame_bgr: np.ndarray,
    tpl: Template,
    x1: int, y1: int, x2: int, y2: int,
    scales: list[float] | tuple[float, ...] = (1.0,),
    min_score: float = DEFAULT_MIN_SCORE,
) -> Located | None:
    """在整帧的 [x1,x2]×[y1,y2]（像素闭区间）里做多尺度模板匹配，返回最佳命中或 None

    用 TM_CCOEFF_NORMED（去均值归一化相关），对整体亮度偏移不敏感；
    分数 <0 按 0 计。模板缩放后比搜索区域还大的尺度直接跳过。
    """
    h, w = frame_bgr.shape[:2]
    x1 = min(max(int(x1), 0), w - 1)
    x2 = min(max(int(x2), 0), w - 1)
    y1 = min(max(int(y1), 0), h - 1)
    y2 = min(max(int(y2), 0), h - 1)
    if x2 < x1 or y2 < y1:
        return None
    region = cv2.cvtColor(frame_bgr[y1:y2 + 1, x1:x2 + 1, :3], cv2.COLOR_BGR2GRAY)
    rh, rw = region.shape[:2]

    best: Located | None = None
    for s in scales:
        tw = int(round(tpl.w * s))
        th = int(round(tpl.h * s))
        if tw < 4 or th < 4 or tw > rw or th > rh:
            continue
        t = tpl.gray if (tw == tpl.w and th == tpl.h) else cv2.resize(
            tpl.gray, (tw, th), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
        res = cv2.matchTemplate(region, t, cv2.TM_CCOEFF_NORMED)
        _min_v, max_v, _min_loc, max_loc = cv2.minMaxLoc(res)
        score = max(float(max_v), 0.0)
        if best is None or score > best.score:
            best = Located(
                cx=x1 + max_loc[0] + (tw - 1) / 2.0,
                cy=y1 + max_loc[1] + (th - 1) / 2.0,
                w=tw, h=th, score=score, scale=float(s),
            )
    if best is None or best.score < min_score:
        if best is not None:
            logger.debug(f"模板 {tpl.name} 最佳分 {best.score:.3f} < {min_score}（scale {best.scale:.2f}）")
        return None
    if abs(best.scale - 1.0) > 0.03:
        logger.debug(f"模板 {tpl.name} 自适配 scale={best.scale:.2f} score={best.score:.3f}")
    return best


def locate_in_region(
    frame_bgr: np.ndarray,
    tpl: Template,
    canvas,
    region,
    min_score: float = DEFAULT_MIN_SCORE,
) -> Located | None:
    """在布局 Region 内定位模板；搜索范围不会越出 Region。"""
    h, w = frame_bgr.shape[:2]
    canvas_x = canvas.x_ratio * w
    canvas_y = canvas.y_ratio * h
    canvas_w = canvas.w_ratio * w
    canvas_h = canvas.h_ratio * h
    x1 = int(canvas_x + region.x_ratio * canvas_w)
    y1 = int(canvas_y + region.y_ratio * canvas_h)
    x2 = int(canvas_x + (region.x_ratio + region.w_ratio) * canvas_w) - 1
    y2 = int(canvas_y + (region.y_ratio + region.h_ratio) * canvas_h) - 1
    return locate(
        frame_bgr, tpl, x1, y1, x2, y2,
        scales=adaptive_scales(int(canvas_w), tpl.record_w),
        min_score=min_score,
    )


_STORE: TemplateStore | None = None


def get_template_store() -> TemplateStore:
    """进程级模板缓存（走 ConfigResolver 路径）"""
    global _STORE
    if _STORE is None:
        _STORE = TemplateStore()
    return _STORE
