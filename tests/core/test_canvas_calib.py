"""屏幕标定 — 画布求解（core/screen_calib.py）

用一张合成"大厅"当参照图；目标设备的实时截图 = 把参照图内容缩放后贴进另一尺寸画面的
某个矩形（模拟挖孔安全区 / 黑边），期望解出的画布就是那个矩形。覆盖：
- 两点对角 → 逐轴缩放 + 平移全部解出，残差 ≈ 0
- 单点 / 同轴没拉开 → 该轴只平移、缩放按两图尺寸比等比
- 参照画布本身不是整屏（继承布局那种）时的归一化口径
- 模板匹配自动定位地标（含缩放）
- layouts.yaml 本地覆盖的读写 / 复位、参照图 + sidecar 落盘
"""
from __future__ import annotations

import numpy as np
import pytest

from lvjiang.core import screen_calib as sc
from lvjiang.core.layout_models import CanvasConfig
from lvjiang.core.screen_calib import (
    CalibError,
    Correspondence,
    locate_landmark,
    solve_canvas,
)


def _synthetic_lobby(w: int, h: int, seed: int = 1) -> np.ndarray:
    """铺满不规则色块的假界面：任何一块 patch 都有纹理，模板匹配才有意义"""
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 40, dtype=np.uint8)
    for _ in range(w * h // 600):
        x, y = int(rng.integers(0, w - 30)), int(rng.integers(0, h - 30))
        color = rng.integers(30, 255, size=3).tolist()
        img[y:y + int(rng.integers(6, 30)), x:x + int(rng.integers(6, 30))] = color
    return img


def _embed(ref: np.ndarray, live_size: tuple[int, int], rect: tuple[int, int, int, int]) -> np.ndarray:
    """把参照图缩放后贴到实时画面的 rect=(x,y,w,h)，其余留黑"""
    import cv2

    lw, lh = live_size
    x, y, w, h = rect
    live = np.zeros((lh, lw, 3), dtype=np.uint8)
    live[y:y + h, x:x + w] = cv2.resize(ref, (w, h), interpolation=cv2.INTER_AREA)
    return live


FULL = CanvasConfig()


def test_two_diagonal_points_solve_scale_and_offset():
    ref_size, live_size = (2400, 1080), (2400, 1080)
    rect = (84, 0, 2186, 1080)  # 挖孔机的 app bounds
    # 参照图上任意两点 → 它们在实时图里的位置（按 rect 线性映射）
    def to_live(rx, ry):
        return rect[0] + rx / ref_size[0] * rect[2], rect[1] + ry / ref_size[1] * rect[3]
    pairs = [Correspondence(200, 150, *to_live(200, 150)), Correspondence(2200, 990, *to_live(2200, 990))]
    sol = solve_canvas(pairs, FULL, ref_size, live_size)
    assert sol.scaled_x and sol.scaled_y
    assert sol.residual_px < 0.01
    assert sol.canvas.x_ratio == pytest.approx(84 / 2400, abs=1e-5)
    assert sol.canvas.y_ratio == pytest.approx(0.0, abs=1e-5)
    assert sol.canvas.w_ratio == pytest.approx(2186 / 2400, abs=1e-5)
    assert sol.canvas.h_ratio == pytest.approx(1.0, abs=1e-5)


def test_single_point_translates_only():
    ref_size, live_size = (1080, 1920), (1080, 2400)
    # 同宽不同高：内容等比贴在 y=240 起（上下留黑）
    pairs = [Correspondence(540, 960, 540, 240 + 960)]
    sol = solve_canvas(pairs, FULL, ref_size, live_size)
    assert not sol.scaled_x and not sol.scaled_y
    assert sol.canvas.w_ratio == pytest.approx(1.0)
    assert sol.canvas.h_ratio == pytest.approx(1920 / 2400)
    assert sol.canvas.y_ratio == pytest.approx(240 / 2400)


def test_points_not_spread_on_one_axis_fall_back_to_translation():
    ref_size = live_size = (2400, 1080)
    # 两点只在 x 上拉开：y 轴按平移
    pairs = [Correspondence(100, 500, 184, 520), Correspondence(2300, 500, 2384, 520)]
    sol = solve_canvas(pairs, FULL, ref_size, live_size)
    assert sol.scaled_x and not sol.scaled_y
    assert sol.canvas.x_ratio == pytest.approx(84 / 2400, abs=1e-5)
    assert sol.canvas.y_ratio == pytest.approx(20 / 1080, abs=1e-5)


def test_reference_canvas_not_full_screen():
    """参照图本身带边框（画布只占中间 90%）：地标按参照画布归一化后再映射"""
    ref_size = live_size = (1000, 1000)
    ref_canvas = CanvasConfig(0.05, 0.05, 0.9, 0.9)
    # 目标机内容区 = (100, 50, 800, 900)
    def to_live(rx, ry):
        u, v = (rx - 50) / 900, (ry - 50) / 900
        return 100 + u * 800, 50 + v * 900
    pairs = [Correspondence(100, 100, *to_live(100, 100)), Correspondence(900, 900, *to_live(900, 900))]
    sol = solve_canvas(pairs, ref_canvas, ref_size, live_size)
    assert sol.canvas.to_dict() == pytest.approx({"x_ratio": 0.1, "y_ratio": 0.05, "w_ratio": 0.8, "h_ratio": 0.9}, abs=1e-5)


def test_solve_rejects_empty_and_reversed():
    with pytest.raises(CalibError):
        solve_canvas([], FULL, (10, 10), (10, 10))
    pairs = [Correspondence(100, 100, 900, 900), Correspondence(900, 900, 100, 100)]
    with pytest.raises(CalibError, match="点反"):
        solve_canvas(pairs, FULL, (1000, 1000), (1000, 1000))


def test_locate_landmark_finds_scaled_patch():
    ref = _synthetic_lobby(1600, 720)
    rect = (120, 60, 1400, 630)
    live = _embed(ref, (1600, 720), rect)
    for rx, ry in ((300, 200), (1200, 500)):
        found = locate_landmark(ref, (rx, ry), live, scales=(0.85, 0.875, 0.9))
        assert found is not None and found[2] > 0.6
        ex = rect[0] + rx / 1600 * rect[2]
        ey = rect[1] + ry / 720 * rect[3]
        assert abs(found[0] - ex) <= 2 and abs(found[1] - ey) <= 2


def test_locate_landmark_missing_returns_none():
    ref = _synthetic_lobby(800, 600)
    live = np.zeros((600, 800, 3), dtype=np.uint8)
    assert locate_landmark(ref, (400, 300), live) is None


# ─── 落盘：layouts.yaml 本地覆盖 + 参照图 ────────────────────

@pytest.fixture
def resolver(tmp_path, monkeypatch):
    from lvjiang.core.config import resolver as rmod

    system = tmp_path / "system"
    local = tmp_path / "local"
    (system / "layouts" / "默认布局").mkdir(parents=True)
    (system / "layouts.yaml").write_text(
        "layouts:\n  默认布局:\n    desc: d\n    canvas:\n      x_ratio: 0.0\n      y_ratio: 0.0\n"
        "      w_ratio: 1.0\n      h_ratio: 1.0\n", encoding="utf-8")
    r = rmod.ConfigResolver(system_dir=system, local_dir=local, dev_mode=False)
    monkeypatch.setattr(rmod, "_resolver", r)
    return r


def test_canvas_save_reset_and_reference_sidecar(resolver):
    assert sc.load_canvas("默认布局") == CanvasConfig()
    sc.save_canvas("默认布局", CanvasConfig(0.035, 0.0, 0.91, 1.0))
    assert (resolver.local_dir / "layouts.yaml").exists()
    assert sc.load_canvas("默认布局").w_ratio == 0.91
    assert sc.system_canvas("默认布局") == CanvasConfig()

    img = _synthetic_lobby(640, 360)
    path = sc.save_reference_image("默认布局", img, canvas=CanvasConfig())
    assert path.exists() and sc.reference_image_path("默认布局") == path
    loaded = sc.load_reference_image("默认布局")
    assert loaded is not None and loaded.shape == img.shape
    assert sc.reference_canvas("默认布局") == CanvasConfig()

    assert sc.reset_canvas("默认布局") == CanvasConfig()
    assert not (resolver.local_dir / "layouts.yaml").exists()  # 与系统值一致 → 覆盖文件删掉
