"""脚本录制数据链路测试 — MacroRecorder on_line 实时回调

不依赖 pynput 真实监听/Qt：直接构造 recorder 并调用内部方法，
验证每生成一行 DSL 都会回调 on_line，且 stop() 全文与回调行一致。
（ScriptRecordDialog 仅把 on_line 桥接到 UI 线程追加文本，核心在此链路）
"""

import time

from src.macros.recorder import MacroRecorder


class _Capture:
    def get_capture_size(self):
        return (1000, 800)


class _Canvas:
    x_ratio = 0.0
    y_ratio = 0.0
    w_ratio = 1.0
    h_ratio = 1.0


class _Layout:
    def get_canvas(self):
        return _Canvas()


def _make_recorder(lines: list[str]) -> MacroRecorder:
    win = {"left": 0, "top": 0, "width": 1000, "height": 800}
    return MacroRecorder(
        target_window=win, capture=_Capture(), layout=_Layout(),
        win_left=0, win_top=0, on_line=lines.append,
    )


class TestOnLineCallback:
    def test_click_line_emitted(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True

        rec._press_pos = (100, 80)
        rec._press_time = time.monotonic()
        rec._handle_release(100, 80)

        assert lines == ["click (0.1, 0.1)"]

    def test_wait_and_drag_lines_emitted(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True

        # 距上次操作 1s → 先补 wait，再拖拽
        now = time.monotonic()
        rec._last_action_time = now - 1.0
        rec._press_pos = (100, 80)
        rec._press_time = now - 0.5
        rec._handle_release(500, 400)

        assert len(lines) == 2
        assert lines[0].startswith("wait ")
        assert lines[1].startswith("drag (0.1, 0.1) (0.5, 0.5) ")

    def test_stop_text_matches_callback_lines(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True

        rec._press_pos = (100, 80)
        rec._press_time = time.monotonic()
        rec._handle_release(100, 80)
        rec._press_pos = (200, 160)
        rec._press_time = time.monotonic()
        rec._handle_release(200, 160)

        rec._recording = False  # 无真实 listener，直接置停
        assert rec.stop() == "\n".join(lines)

    def test_callback_exception_does_not_break_recording(self):
        def boom(_line: str):
            raise RuntimeError("callback boom")

        win = {"left": 0, "top": 0, "width": 1000, "height": 800}
        rec = MacroRecorder(
            target_window=win, capture=_Capture(), layout=_Layout(),
            win_left=0, win_top=0, on_line=boom,
        )
        rec._recording = True
        rec._press_pos = (100, 80)
        rec._press_time = time.monotonic()
        rec._handle_release(100, 80)   # 回调抛异常不应向外传播

        assert rec._lines == ["click (0.1, 0.1)"]

    def test_no_callback_still_records(self):
        win = {"left": 0, "top": 0, "width": 1000, "height": 800}
        rec = MacroRecorder(
            target_window=win, capture=_Capture(), layout=_Layout(),
            win_left=0, win_top=0,
        )
        rec._recording = True
        rec._press_pos = (100, 80)
        rec._press_time = time.monotonic()
        rec._handle_release(100, 80)

        assert rec._lines == ["click (0.1, 0.1)"]
