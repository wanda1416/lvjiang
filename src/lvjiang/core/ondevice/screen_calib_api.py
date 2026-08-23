"""设备端屏幕标定 — 原生标定页（CalibActivity）的 Python 侧入口

Kotlin 侧只管画图和收点：参照图 / 实时截图的路径从这里拿，用户在两张图上点的
地标对发回来求解，解出的画布再由这里写进 layouts.yaml 的本地覆盖。算法全部在
core/screen_calib.py（PC 与设备共用），本模块只做跨语言适配。

与 task_runner 相同的约定：对外函数返回 JSON 文本、自己吞异常（{ok:false, error}）。
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from ...constants import PROJECT_ROOT
from ...i18n import tr
from .. import screen_calib as sc
from ..layout_models import CanvasConfig

#: 实时截图落盘位置（filesDir/lvjiang/calib/live.png），Kotlin 按路径读显示
_CALIB_DIR = PROJECT_ROOT / "calib"
_LIVE_PNG = _CALIB_DIR / "live.png"


def _layout_name() -> str:
    from .workflow_runner import _default_layout_name

    return _default_layout_name()


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _fail(e: Exception) -> str:
    return _json({"ok": False, "error": f"{type(e).__name__}: {e}"})


def _read_png(path: Path) -> np.ndarray | None:
    if not path.exists():
        return None
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def _size(img: np.ndarray | None) -> list[int] | None:
    return None if img is None else [int(img.shape[1]), int(img.shape[0])]


def calib_info() -> str:
    """当前布局、参照图与实时截图的路径尺寸、画布（合并值 + 系统值）"""
    try:
        name = _layout_name()
        ref_path = sc.reference_image_path(name)
        ref = sc.load_reference_image(name) if ref_path else None
        live = _read_png(_LIVE_PNG)
        return _json({
            "ok": True,
            "layout": name,
            "ref_path": str(ref_path) if ref_path and ref is not None else None,
            "ref_size": _size(ref),
            "live_path": str(_LIVE_PNG) if live is not None else None,
            "live_size": _size(live),
            "canvas": sc.load_canvas(name).to_dict(),
            "system_canvas": sc.system_canvas(name).to_dict(),
            "ref_canvas": sc.reference_canvas(name).to_dict(),
        })
    except Exception as e:
        return _fail(e)


def calib_capture() -> str:
    """经无障碍截一张当前画面存成 live.png（调用前游戏应在前台）"""
    try:
        from .capture import A11yCapture

        frame = A11yCapture().capture(timeout=8.0)
        if frame is None:
            return _json({"ok": False, "error": tr("截图失败：无障碍服务未连接或截图被节流")})
        _CALIB_DIR.mkdir(parents=True, exist_ok=True)
        ok, buf = cv2.imencode(".png", frame)
        if not ok:
            return _json({"ok": False, "error": tr("截图编码失败")})
        _LIVE_PNG.write_bytes(buf.tobytes())
        return _json({"ok": True, "path": str(_LIVE_PNG), "w": int(frame.shape[1]), "h": int(frame.shape[0])})
    except Exception as e:
        return _fail(e)


def calib_use_live_as_reference() -> str:
    """把当前 live.png 设为本布局的参照图（作者在自己的机器上提供大厅截图就走这里）"""
    try:
        live = _read_png(_LIVE_PNG)
        if live is None:
            return _json({"ok": False, "error": tr("还没有实时截图")})
        name = _layout_name()
        path = sc.save_reference_image(name, live)
        return _json({"ok": True, "path": str(path), "layout": name})
    except Exception as e:
        return _fail(e)


def calib_locate(ref_x: float, ref_y: float) -> str:
    """在实时截图里自动找参照图 (ref_x, ref_y) 处的地标"""
    try:
        name = _layout_name()
        ref = sc.load_reference_image(name)
        live = _read_png(_LIVE_PNG)
        if ref is None or live is None:
            return _json({"ok": False, "error": tr("缺参照图或实时截图")})
        found = sc.locate_landmark(ref, (float(ref_x), float(ref_y)), live)
        if found is None:
            return _json({"ok": True, "found": False})
        return _json({"ok": True, "found": True, "x": found[0], "y": found[1], "score": found[2]})
    except Exception as e:
        return _fail(e)


def calib_solve(pairs_json: str) -> str:
    """由对应点解画布；pairs_json = [{"ref_x","ref_y","live_x","live_y"}, ...]"""
    try:
        name = _layout_name()
        ref = sc.load_reference_image(name)
        live = _read_png(_LIVE_PNG)
        if ref is None or live is None:
            return _json({"ok": False, "error": tr("缺参照图或实时截图")})
        pairs = [sc.Correspondence(float(p["ref_x"]), float(p["ref_y"]),
                                   float(p["live_x"]), float(p["live_y"]))
                 for p in json.loads(pairs_json)]
        ref_size = (ref.shape[1], ref.shape[0])
        live_size = (live.shape[1], live.shape[0])
        sol = sc.solve_canvas(pairs, sc.reference_canvas(name), ref_size, live_size)
        return _json({"ok": True, **sol.to_dict(),
                      "rect_live": list(sc.canvas_rect_px(sol.canvas, live_size))})
    except Exception as e:
        return _fail(e)


def calib_save(canvas_json: str) -> str:
    try:
        name = _layout_name()
        canvas = CanvasConfig.from_dict(json.loads(canvas_json))
        if canvas.w_ratio <= 0 or canvas.h_ratio <= 0:
            return _json({"ok": False, "error": tr("画布尺寸非法")})
        sc.save_canvas(name, canvas)
        return _json({"ok": True, "layout": name, "canvas": canvas.to_dict()})
    except Exception as e:
        return _fail(e)


def calib_reset() -> str:
    try:
        name = _layout_name()
        return _json({"ok": True, "layout": name, "canvas": sc.reset_canvas(name).to_dict()})
    except Exception as e:
        return _fail(e)
