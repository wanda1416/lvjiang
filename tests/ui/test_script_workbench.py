"""脚本工作台：取点画布 + 调试面板

- PickCanvas：点击发 picked（含 RGB），拖拽发 region_changed，归一化坐标正确
- DebugPanel 纯逻辑：片段格式、画布裁剪、值格式化、环境检查
- DebugPanel 运行：假主窗口（mock 后端）跑一段脚本 → 变量表/状态；单步：停在第 1 行、
  单步到第 2 行、继续到结束、当前行高亮随之变化；停止能结束阻塞中的脚本
"""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
from PyQt6.QtCore import QPointF, Qt

from lvjiang.core.config import resolver as cr
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.ui.ocr.pick_canvas import PickCanvas
from lvjiang.ui.scripts import workbench as wb


@pytest.fixture
def dev_resolver(tmp_path, monkeypatch):
    system = tmp_path / "system"
    local = tmp_path / "local"
    (system / "workflows").mkdir(parents=True)
    (local / "workflows").mkdir(parents=True)
    (system / "workflows" / "demo.wf").write_text('log "demo"\n', encoding="utf-8")
    r = ConfigResolver(system_dir=system, local_dir=local, dev_mode=True)
    monkeypatch.setattr(cr, "_resolver", r)
    return r


def _frame(w=200, h=100) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :100] = (0, 0, 255)      # 左半红（BGR）
    img[:, 100:] = (255, 0, 0)      # 右半蓝
    return img


# ─── 纯逻辑 ─────────────────────────────────────────────

def test_snippets():
    assert wb.snippet_point(0.5, 0.25) == "(0.5000, 0.2500)"
    assert wb.snippet_color(46, 204, 113) == '"#2ecc71"'
    assert wb.snippet_rect(0.1, 0.2, 0.3, 0.4) == "(0.1000, 0.2000, 0.3000, 0.4000)"


def test_crop_to_canvas():
    img = _frame()
    canvas = MagicMock(x_ratio=0.5, y_ratio=0.0, w_ratio=0.5, h_ratio=1.0)
    crop = wb.crop_to_canvas(img, canvas)
    assert crop.shape == (100, 100, 3) and tuple(crop[0, 0]) == (255, 0, 0)
    assert wb.crop_to_canvas(img, None) is img
    assert wb.crop_to_canvas(img, MagicMock(x_ratio=1.0, y_ratio=0, w_ratio=0, h_ratio=1)) is img


def test_format_value():
    assert wb.format_value(None) == "null"
    assert wb.format_value(True) == "true"
    assert wb.format_value(3) == "3"
    assert wb.format_value("s") == "'s'"
    assert wb.format_value({"a": [1, 2]}) == '{"a": [1, 2]}'
    assert wb.format_value(object()).startswith("<object>")


# ─── PickCanvas ─────────────────────────────────────────

class TestPickCanvas:
    def _canvas(self, qtbot):
        c = PickCanvas()
        qtbot.addWidget(c)
        c.resize(400, 200)
        c.show()
        c.set_image(_frame())
        c._fit_to_widget()
        return c

    def _widget_pos(self, c: PickCanvas, nx: float, ny: float):
        p = c._img_to_widget(nx * c._img_w, ny * c._img_h)
        return p

    def test_click_emits_pick_with_rgb(self, qtbot):
        c = self._canvas(qtbot)
        pos = self._widget_pos(c, 0.25, 0.5)
        with qtbot.waitSignal(c.picked, timeout=1000) as blocker:
            qtbot.mousePress(c, Qt.MouseButton.LeftButton, pos=pos.toPoint())
            qtbot.mouseRelease(c, Qt.MouseButton.LeftButton, pos=pos.toPoint())
        x, y, r, g, b = blocker.args
        assert abs(x - 0.25) < 0.02 and abs(y - 0.5) < 0.02
        assert (r, g, b) == (255, 0, 0)

    def test_drag_emits_region(self, qtbot):
        c = self._canvas(qtbot)
        p1 = self._widget_pos(c, 0.1, 0.1).toPoint()
        p2 = self._widget_pos(c, 0.6, 0.8).toPoint()
        with qtbot.waitSignal(c.region_changed, timeout=1000) as blocker:
            qtbot.mousePress(c, Qt.MouseButton.LeftButton, pos=p1)
            qtbot.mouseMove(c, pos=QPointF((p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2).toPoint())
            qtbot.mouseMove(c, pos=p2)
            qtbot.mouseRelease(c, Qt.MouseButton.LeftButton, pos=p2)
        x, y, w, h = blocker.args
        assert abs(x - 0.1) < 0.02 and abs(y - 0.1) < 0.03
        assert abs(w - 0.5) < 0.03 and abs(h - 0.7) < 0.05
        assert c.selection_norm() is not None

    def test_rgb_at_clamps(self, qtbot):
        c = self._canvas(qtbot)
        assert c.rgb_at(1.5, -1) == (0, 0, 255)  # 夹到右上角 → 蓝


# ─── DebugPanel ─────────────────────────────────────────

class _Editor:
    def __init__(self, text=""):
        self.text = text
        self.inserted: list[str] = []
        self.lines: list[int | None] = []
        self.locked: list[bool] = []

    def insert_text(self, t):
        self.inserted.append(t)

    def script_text(self):
        return self.text

    def highlight_line(self, n):
        self.lines.append(n)

    def set_locked(self, v):
        self.locked.append(v)


def _fake_main(frame=None, backend="windows"):
    main = MagicMock()
    main._backend = backend
    main._running = False
    main._device_ready = True
    main._target_window = {"left": 0, "top": 0, "width": 200, "height": 100, "hwnd": 1}
    main._refresh_capture = lambda: (frame if frame is not None else _frame(), None)
    layout = MagicMock()
    layout.get_canvas.return_value = MagicMock(x_ratio=0, y_ratio=0, w_ratio=1, h_ratio=1)
    layout.get_scene_regions.return_value = []
    layout.get_scene_points.return_value = []
    layout.get_scene_panels.return_value = []
    layout.get_scene_arrows.return_value = []
    main._layout_manager.load_layout.return_value = layout
    main._layout_manager.get_active_layout_name.return_value = "默认布局"
    main._capture.capture.return_value = frame if frame is not None else _frame()
    main._capture.get_capture_size.return_value = (200, 100)
    main._input.background_mode = False
    main._user_config.input_sim = None
    main._user_config.delay_params = {}
    main._user_manager.get_active_user_name.return_value = "u"
    main._session_manager.load.return_value = {}
    main._session_manager.save_fn.return_value = lambda: None
    del main._create_ui_callback   # MagicMock 默认什么属性都有；没有 UI 回调更接近独立运行
    return main


class TestDebugPanel:
    def _panel(self, qtbot, main, text=""):
        ed = _Editor(text)
        panel = wb.DebugPanel(main, ed)
        qtbot.addWidget(panel)
        return panel, ed

    def test_refresh_and_insert(self, qtbot):
        panel, ed = self._panel(qtbot, _fake_main())
        assert panel.refresh_screenshot()
        assert not panel.btn_ins_point.isEnabled()
        panel._on_pick(0.25, 0.5, 255, 0, 0)
        panel._on_region(0.1, 0.2, 0.3, 0.4)
        panel._insert_point()
        panel._insert_color()
        panel._insert_rect()
        assert ed.inserted == ["(0.2500, 0.5000)", '"#ff0000"', "(0.1000, 0.2000, 0.3000, 0.4000)"]

    def test_refresh_failure_message(self, qtbot):
        main = _fake_main()
        main._refresh_capture = lambda: (None, "请先在主窗口定位窗口")
        panel, _ = self._panel(qtbot, main)
        assert not panel.refresh_screenshot()
        assert "定位窗口" in panel.lbl_readout.text()

    def test_check_env(self, qtbot):
        main = _fake_main()
        panel, _ = self._panel(qtbot, main)
        assert panel._check_env() is None
        main._running = True
        assert "运行" in panel._check_env()
        main._running = False
        main._target_window = None
        assert "定位" in panel._check_env()
        main._backend = "adb"
        main._device_ready = False
        assert "连接设备" in panel._check_env()

    def test_run_to_completion_fills_vars(self, qtbot, dev_resolver):
        main = _fake_main()
        panel, ed = self._panel(qtbot, main, text='$a = 1\n$b = $a + 1\nlog "done"\n')
        with qtbot.waitSignal(panel._bridge.finished, timeout=5000):
            assert panel._start(step=False)
        assert not panel.running
        names = {panel.vars.item(r, 0).text(): panel.vars.item(r, 1).text() for r in range(panel.vars.rowCount())}
        assert names == {"$a": "1", "$b": "2"}
        assert ed.locked == [True, False]
        assert ed.lines[-1] is None
        assert "完成" in panel.lbl_run.text()
        assert (dev_resolver.system_dir / "workflows" / "_editor_run.wf").exists()
        assert "done" in panel.log.toPlainText()

    def test_step_then_continue(self, qtbot, dev_resolver):
        main = _fake_main()
        panel, ed = self._panel(qtbot, main, text="$a = 1\n$b = 2\n$c = 3\n")
        with qtbot.waitSignal(panel._bridge.stepped, timeout=5000):
            assert panel._start(step=True)
        worker = panel._worker
        assert worker is not None
        assert ed.lines[-1] == 1 and panel.running
        assert "第 1 行" in panel.lbl_run.text()
        with qtbot.waitSignal(panel._bridge.stepped, timeout=5000):
            panel._on_step()
        assert ed.lines[-1] == 2
        assert panel.vars.rowCount() == 1 and panel.vars.item(0, 0).text() == "$a"
        with qtbot.waitSignal(panel._bridge.finished, timeout=5000):
            panel._on_continue()
        assert worker.isFinished() and not worker.isRunning()
        assert not panel.running and ed.lines[-1] is None

    def test_stop_while_paused(self, qtbot, dev_resolver):
        main = _fake_main()
        panel, _ = self._panel(qtbot, main, text="$a = 1\n$b = 2\n")
        with qtbot.waitSignal(panel._bridge.stepped, timeout=5000):
            panel._start(step=True)
        with qtbot.waitSignal(panel._bridge.finished, timeout=5000):
            panel._on_stop()
        assert not panel.running
        assert "停止" in panel.lbl_run.text()

    def test_refuses_when_env_not_ready(self, qtbot, dev_resolver):
        main = _fake_main()
        main._target_window = None
        panel, _ = self._panel(qtbot, main, text="$a = 1\n")
        assert not panel._start(step=False)
        assert not panel.running
