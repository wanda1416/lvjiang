"""屏幕映射标定（core/android/calib.py）PC 侧测试

设备端 ScreenMap / CalibOverlay 在 Kotlin 里，这里用假代理模拟一台"输入空间相对截图空间
有偏移 + 缩放"的手机：calib_mark(x, y) 把准星画在 f(x, y) 处，截图按此合成。覆盖：
- 合成截图里找准星（含标签文字、圆环、缩放后的短臂）
- 逐轴仿射求逆
- probe：恒等设备 → 判恒等不写文件；偏移设备 → 解出参数、--apply 写回后验证点误差归零
- AgentClient.calib_* 与 describe() 的"已标定"标记
"""
from __future__ import annotations

import numpy as np
import pytest

from lvjiang.core.android import calib as calib_mod
from lvjiang.core.android.agent import AgentClient
from lvjiang.core.android.calib import (
    MARK_BGR,
    CalibError,
    find_crosshair,
    probe,
    solve_axis,
)

from .test_device_agent import _status, fake  # noqa: F401, F811  (fixture 复用)


def _draw_crosshair(img: np.ndarray, cx: int, cy: int, arm: int = 60, ring: int = 40) -> None:
    """按 CalibOverlay 的画法：3px 十字 + 圆环（洋红），右侧再放一块白字标签"""
    color = np.array(MARK_BGR, dtype=np.uint8)
    h, w = img.shape[:2]
    for d in (-1, 0, 1):
        y = cy + d
        if 0 <= y < h:
            img[y, max(0, cx - arm):min(w, cx + arm + 1)] = color
        x = cx + d
        if 0 <= x < w:
            img[max(0, cy - arm):min(h, cy + arm + 1), x] = color
    ang = np.linspace(0, 2 * np.pi, 720)
    for r in (ring - 1, ring, ring + 1):
        xs = (cx + r * np.cos(ang)).astype(int)
        ys = (cy + r * np.sin(ang)).astype(int)
        ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
        img[ys[ok], xs[ok]] = color
    # 白色标签（不是准星色，不该影响定位）
    img[max(0, cy - 40):cy + 8, cx + 44:min(w, cx + 160)] = 255


def test_find_crosshair_exact_center_with_label_and_noise():
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, size=(720, 1280, 3), dtype=np.uint8)
    _draw_crosshair(img, 400, 300)
    found = find_crosshair(img, expected=(380, 320))
    assert found is not None
    assert abs(found[0] - 400) < 0.6 and abs(found[1] - 300) < 0.6


def test_find_crosshair_missing_returns_none():
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    assert find_crosshair(img, (400, 300)) is None
    # 只有零星准星色像素（比如 UI 里一个洋红图标）不算准星
    img[300:310, 400:410] = MARK_BGR
    assert find_crosshair(img, (400, 300)) is None


def test_find_crosshair_scaled_short_arms():
    """截图被缩放一半：臂长 30px 仍能找到"""
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    _draw_crosshair(img, 200, 150, arm=30, ring=20)
    found = find_crosshair(img, (205, 145))
    assert found is not None and abs(found[0] - 200) < 0.6 and abs(found[1] - 150) < 0.6


def test_solve_axis_inverts_affine():
    # 设备：shot% = 0.9 * input% + 0.05  → input% = shot% / 0.9 - 0.05/0.9
    samples = [(0.2, 0.9 * 0.2 + 0.05), (0.8, 0.9 * 0.8 + 0.05)]
    s, o = solve_axis(samples)
    assert s == pytest.approx(1 / 0.9) and o == pytest.approx(-0.05 / 0.9)
    with pytest.raises(CalibError):
        solve_axis([(0.2, 0.3)])


# ─── 假设备：输入空间 = 截图空间经仿射 ───────────────────────

class _OffsetPhone:
    """模拟一台手机：输入像素 (x, y) 显示在截图的 (ax*x + bx, ay*y + by)；可存映射参数"""

    def __init__(self, w=1080, h=1920, ax=1.0, bx=0.0, ay=1.0, by=0.0):
        self.w, self.h = w, h
        self.ax, self.bx, self.ay, self.by = ax, bx, ay, by
        self.calib = None  # 设备端 ScreenMap 文件：None=恒等
        self.mark = None
        self.hidden = 0

    def _map(self, x, y):
        if self.calib is None:
            return x, y
        sx, ox, sy, oy = (self.calib[k] for k in ("sx", "ox", "sy", "oy"))
        return int((x / self.w * sx + ox) * self.w), int((y / self.h * sy + oy) * self.h)

    def info(self):
        c = self.calib or {"sx": 1.0, "ox": 0.0, "sy": 1.0, "oy": 0.0}
        return {"ok": True, "key": "fake_%dx%d" % (self.w, self.h),
                "screen": {"w": self.w, "h": self.h, "rotation": 0},
                "calib": c, "identity": self.calib is None, "stored": self.calib is not None}

    def handle(self, req):
        op = req["op"]
        if op in ("ping", "status"):
            return _status(calib_identity=self.calib is None), b""
        if op == "calib_get":
            return self.info(), b""
        if op == "calib_clear":
            self.calib = None
            return self.info(), b""
        if op == "calib_set":
            self.calib = {k: float(req.get(k, d)) for k, d in (("sx", 1), ("ox", 0), ("sy", 1), ("oy", 0))}
            return self.info(), b""
        if op == "calib_mark":
            px, py = self._map(req["x"], req["y"])
            self.mark = (px, py)
            return dict(self.info(), px={"x": px, "y": py}), b""
        if op == "calib_hide":
            self.hidden += 1
            self.mark = None
            return {"ok": True}, b""
        if op == "screenshot":
            img = np.zeros((self.h, self.w, 4), dtype=np.uint8)
            if self.mark is not None:
                bgr = np.zeros((self.h, self.w, 3), dtype=np.uint8)
                sx = int(self.ax * self.mark[0] + self.bx)
                sy = int(self.ay * self.mark[1] + self.by)
                _draw_crosshair(bgr, sx, sy)
                img[..., 0], img[..., 1], img[..., 2] = bgr[..., 2], bgr[..., 1], bgr[..., 0]
            return {"ok": True, "via": "a11y", "fmt": "rgba", "w": self.w, "h": self.h}, img.tobytes()
        return {"ok": False, "error": "unknown " + op}, b""


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch):
    monkeypatch.setattr(calib_mod, "_SETTLE_S", 0)


def test_probe_identity_device_keeps_identity(fake):  # noqa: F811
    phone = _OffsetPhone()
    _, dev = fake(phone.handle)
    client = AgentClient(dev)
    assert client.connect()
    result = probe(client, apply=True)
    assert result.identity and result.applied and phone.calib is None
    assert result.verify_err_px is not None and result.verify_err_px < 1
    assert phone.hidden == 1  # 结束撤覆盖层
    client.close()


def test_probe_offset_device_solves_and_verifies(fake):  # noqa: F811
    # 输入点显示在截图里偏了 (+40, -60) 且 y 轴缩到 0.95
    phone = _OffsetPhone(ax=1.0, bx=40, ay=0.95, by=-60)
    _, dev = fake(phone.handle)
    client = AgentClient(dev)
    assert client.connect()

    dry = probe(client, apply=False)
    assert not dry.identity and not dry.applied and phone.calib is None
    sx, ox, sy, oy = dry.params
    # input% = (shot% - b%) / a
    assert sx == pytest.approx(1.0, abs=2e-3)
    assert ox == pytest.approx(-40 / 1080, abs=2e-3)
    assert sy == pytest.approx(1 / 0.95, abs=2e-3)
    assert oy == pytest.approx(60 / 1920 / 0.95, abs=2e-3)

    applied = probe(client, apply=True)
    assert applied.applied and phone.calib is not None
    assert applied.verify_err_px is not None and applied.verify_err_px <= 2
    # 写回后 status 标记非恒等，描述里带"已标定"
    client.refresh_status()
    assert not client.calib_identity and "已标定" in client.describe()
    assert client.calib_get()["identity"] is False
    client.calib_clear()
    assert client.calib_get()["identity"] is True
    client.close()


def test_probe_without_crosshair_raises(fake):  # noqa: F811
    phone = _OffsetPhone()
    orig = phone.handle

    def handle(req):
        if req["op"] == "screenshot":
            img = np.zeros((phone.h, phone.w, 4), dtype=np.uint8)
            return {"ok": True, "via": "a11y", "fmt": "rgba", "w": phone.w, "h": phone.h}, img.tobytes()
        return orig(req)

    _, dev = fake(handle)
    client = AgentClient(dev)
    assert client.connect()
    with pytest.raises(CalibError, match="准星"):
        probe(client)
    assert phone.hidden == 1
    client.close()
