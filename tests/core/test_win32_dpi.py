"""PostMessage DPI 坐标换算 + 子窗口投递测试

1. DPI 换算：本进程 Per-Monitor V2，目标 unaware 窗口位于缩放副屏时，
   需按 96/屏DPI 换算坐标。
2. 子窗口投递：投屏/游戏窗口（vivo 互传、Qt 渲染）用子窗口接收鼠标，
   PostMessage 投给顶层窗口无效，须命中点解析实际子窗口。

验证 win32_util.message_coord_scale / screen_to_client_logical /
resolve_message_target。
"""

import ctypes

import pytest

from lvjiang.core.desktop import win32_util

# awareness 常量（与 win32_util 内部一致）
UNAWARE, SYSTEM, PER_MONITOR = 0, 1, 2


# ─── message_coord_scale（纯函数）──────────────────────────────

class TestMessageCoordScale:
    def test_per_monitor_no_scale(self):
        assert win32_util.message_coord_scale(PER_MONITOR, 120, 96) == 1.0

    def test_unaware_scales_by_96(self):
        # 125% 副屏（120 DPI）：unaware 窗口逻辑坐标 = 物理 × 96/120
        assert win32_util.message_coord_scale(UNAWARE, 120, 96) == pytest.approx(96 / 120)

    def test_system_scales_by_system_dpi(self):
        # system aware 窗口按系统 DPI（96）虚拟化，同样 ×96/120
        assert win32_util.message_coord_scale(SYSTEM, 120, 96) == pytest.approx(96 / 120)

    def test_system_aware_on_scaled_system_dpi(self):
        # 系统 DPI 本身为 120（主屏 125% 启动），副屏 150%（144 DPI）
        assert win32_util.message_coord_scale(SYSTEM, 144, 120) == pytest.approx(120 / 144)

    def test_100_percent_screen_no_scale(self):
        assert win32_util.message_coord_scale(UNAWARE, 96, 96) == 1.0

    def test_zero_dpi_fallback(self):
        assert win32_util.message_coord_scale(UNAWARE, 0, 96) == 1.0


# ─── screen_to_client_logical（mock Win32 探测函数）─────────────

class TestScreenToClientLogical:
    def _patch(self, monkeypatch, awareness, screen_dpi, system_dpi=96, client=(100, 200)):
        monkeypatch.setattr(win32_util, "screen_to_client", lambda h, x, y: client)
        monkeypatch.setattr(win32_util, "_get_window_dpi_awareness", lambda h: awareness)
        monkeypatch.setattr(win32_util, "_get_window_screen_dpi", lambda h: screen_dpi)
        monkeypatch.setattr(win32_util, "_get_system_dpi", lambda: system_dpi)

    def test_per_monitor_passthrough(self, monkeypatch):
        self._patch(monkeypatch, awareness=PER_MONITOR, screen_dpi=120)
        assert win32_util.screen_to_client_logical(1, 0, 0) == (100, 200)

    def test_unaware_125_percent_scaled(self, monkeypatch):
        # 核心回归场景：unaware 窗口 + 120 DPI 副屏 → ×0.8
        self._patch(monkeypatch, awareness=UNAWARE, screen_dpi=120)
        assert win32_util.screen_to_client_logical(1, 0, 0) == (80, 160)

    def test_system_aware_125_percent_scaled(self, monkeypatch):
        self._patch(monkeypatch, awareness=SYSTEM, screen_dpi=120, system_dpi=96)
        assert win32_util.screen_to_client_logical(1, 0, 0) == (80, 160)

    def test_unaware_100_percent_unchanged(self, monkeypatch):
        self._patch(monkeypatch, awareness=UNAWARE, screen_dpi=96)
        assert win32_util.screen_to_client_logical(1, 0, 0) == (100, 200)


# ─── resolve_message_target（子窗口投递）───────────────────────

class _MockUser32:
    """替换 win32_util._user32 的假对象（跨平台可测）"""

    def __init__(self, child_result):
        self._child_result = child_result
        self._child_impl = _MockChildWindowFromPointEx(child_result)

    def __getattr__(self, name):
        if name == "ChildWindowFromPointEx":
            return self._child_impl
        raise AttributeError(name)


class _MockChildWindowFromPointEx:
    def __init__(self, result):
        self._result = result
        self.restype = ctypes.c_void_p
        self.argtypes = []

    def __call__(self, hwnd, pt, flags):
        return self._result


class TestResolveMessageTarget:
    def _patch_user32(self, monkeypatch, child_result):
        monkeypatch.setattr(win32_util, "_user32", _MockUser32(child_result))

    def test_no_child_returns_parent(self, monkeypatch):
        # 无子窗口 → 返回原顶层 hwnd
        self._patch_user32(monkeypatch, 0)
        assert win32_util.resolve_message_target(0x100, 50, 50) == 0x100

    def test_same_window_returns_parent(self, monkeypatch):
        # ChildWindowFromPointEx 返回与父相同 → 无子窗口
        self._patch_user32(monkeypatch, 0x100)
        assert win32_util.resolve_message_target(0x100, 50, 50) == 0x100

    def test_child_returned(self, monkeypatch):
        # 命中子窗口 → 返回子窗口句柄（vivo 投屏/Qt 渲染窗口场景）
        self._patch_user32(monkeypatch, 0x2A0)
        assert win32_util.resolve_message_target(0x100, 50, 50) == 0x2A0

    def test_api_error_falls_back_to_parent(self, monkeypatch):
        # API 抛异常 → 回退到原顶层窗口（不崩溃、保持旧行为）
        class _Boom:
            def __call__(self, *a, **k):
                raise OSError("no API")
        monkeypatch.setattr(win32_util, "_user32", _MockUser32(None))
        monkeypatch.setattr(win32_util._user32, "_child_impl", _Boom())
        assert win32_util.resolve_message_target(0x100, 50, 50) == 0x100
