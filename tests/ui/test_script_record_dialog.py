"""脚本录制数据链路测试 — MacroRecorder on_line 实时回调

不依赖 pynput 真实监听/Qt：直接构造 recorder 并调用内部方法，
验证每生成一行 DSL 都会回调 on_line，且 stop() 全文与回调行一致。
（ScriptRecordDialog 仅把 on_line 桥接到 UI 线程追加文本，核心在此链路）
"""

import time

from lvjiang.core.macro_recorder import PRECISION_HIGH, MacroRecorder


class _MockKey:
    """模拟 pynput 按键对象（KeyCode/Key 枚举都会被当作 dict key 存进
    _key_press_times，需要保持默认的按身份哈希/相等，SimpleNamespace 会
    因为自带 __eq__ 而变成不可哈希，这里用普通类避开这个坑）"""

    def __init__(self, char=None, name=None):
        self.char = char
        self.name = name


def _char_key(char: str) -> _MockKey:
    """模拟 pynput KeyCode（普通字符键，无 .name 属性）"""
    return _MockKey(char=char)


def _special_key(name: str) -> _MockKey:
    """模拟 pynput Key 枚举（特殊键，无 .char 属性）"""
    return _MockKey(char=None, name=name)


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


def _make_recorder(lines: list[str], precision: str = "low") -> MacroRecorder:
    win = {"left": 0, "top": 0, "width": 1000, "height": 800}
    return MacroRecorder(
        target_window=win, capture=_Capture(), layout=_Layout(),
        win_left=0, win_top=0, on_line=lines.append,
        precision=precision,
    )


class TestOnLineCallback:
    def test_mouse_down_up_lines_emitted(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True

        rec._record_mouse_button("left", True, 100, 80, 10.0)
        rec._record_mouse_button("left", False, 100, 80, 10.2)

        assert lines == [
            "place (0.1, 0.1)",
            "mouse left down",
            "wait 0.2",
            "place (0.1, 0.1)",
            "mouse left up",
        ]

    def test_short_wait_is_preserved(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._last_action_time = 10.0

        rec._maybe_emit_wait(10.099)

        assert lines == ["wait 0.099"]

    def test_stop_text_matches_callback_lines(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True

        rec._record_mouse_button("left", True, 100, 80, 10.0)
        rec._record_mouse_button("left", False, 100, 80, 10.1)

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
        rec._record_mouse_button("left", True, 100, 80, 10.0)

        assert rec._lines == ["place (0.1, 0.1)", "mouse left down"]

    def test_no_callback_still_records(self):
        win = {"left": 0, "top": 0, "width": 1000, "height": 800}
        rec = MacroRecorder(
            target_window=win, capture=_Capture(), layout=_Layout(),
            win_left=0, win_top=0,
        )
        rec._recording = True
        rec._record_mouse_button("left", True, 100, 80, 10.0)

        assert rec._lines == ["place (0.1, 0.1)", "mouse left down"]


class _FakeButton:
    """模拟 pynput mouse.Button 具名枚举成员：_on_click 只依赖 .name。"""

    def __init__(self, name):
        self.name = name


class TestHighPrecisionClick:
    """precision=high 下 _on_click 对各鼠标键（含侧键）的录制。

    本测试环境没有真实 pynput 后端（无 DISPLAY），recorder 模块顶层的
    pynput_mouse 恒为 None，_on_click 开头 `if pynput_mouse is None:
    return` 会直接短路——monkeypatch 成非 None 的哨兵值绕过这层守卫，
    走真实的按键名判定逻辑。
    """

    def _make_high_precision_recorder(self, monkeypatch):
        from lvjiang.core import macro_recorder as recorder_module
        monkeypatch.setattr(recorder_module, "pynput_mouse", object())
        win = {"left": 0, "top": 0, "width": 1000, "height": 800}
        rec = MacroRecorder(
            target_window=win, capture=_Capture(), layout=_Layout(),
            win_left=0, win_top=0, on_line=lambda _line: None,
            precision=PRECISION_HIGH,
        )
        rec._recording = True
        monkeypatch.setattr(rec, "_target_is_foreground", lambda: True)
        return rec

    def test_side_buttons_recorded_as_trace_events(self, monkeypatch):
        rec = self._make_high_precision_recorder(monkeypatch)

        rec._on_click(10, 10, _FakeButton("x1"), True)
        rec._on_click(10, 10, _FakeButton("x1"), False)
        rec._on_click(10, 10, _FakeButton("x2"), True)
        rec._on_click(10, 10, _FakeButton("x2"), False)

        assert [(e.kind, e.values) for e in rec._trace_events] == [
            ("button", ("x1", True)),
            ("button", ("x1", False)),
            ("button", ("x2", True)),
            ("button", ("x2", False)),
        ]

    def test_left_right_middle_still_recorded(self, monkeypatch):
        rec = self._make_high_precision_recorder(monkeypatch)

        for name in ("left", "right", "middle"):
            rec._on_click(10, 10, _FakeButton(name), True)

        assert [e.values[0] for e in rec._trace_events] == [
            "left", "right", "middle"]

    def test_unrecognized_button_name_ignored(self, monkeypatch):
        rec = self._make_high_precision_recorder(monkeypatch)

        rec._on_click(10, 10, _FakeButton("mouse6"), True)

        assert rec._trace_events == []


class TestLowPrecisionMouseButtons:
    def _make_recorder(self, monkeypatch):
        from lvjiang.core import macro_recorder as recorder_module
        monkeypatch.setattr(recorder_module, "pynput_mouse", object())
        rec = _make_recorder([])
        rec._recording = True
        return rec

    def test_side_buttons_preserve_raw_down_up(self, monkeypatch):
        rec = self._make_recorder(monkeypatch)

        rec._on_click(100, 80, _FakeButton("x1"), True)
        rec._on_click(100, 80, _FakeButton("x1"), False)
        rec._on_click(200, 160, _FakeButton("x2"), True)
        rec._on_click(200, 160, _FakeButton("x2"), False)

        assert [line for line in rec._lines if not line.startswith("wait ")] == [
            "place (0.1, 0.1)", "mouse x1 down",
            "place (0.1, 0.1)", "mouse x1 up",
            "place (0.2, 0.2)", "mouse x2 down",
            "place (0.2, 0.2)", "mouse x2 up",
        ]

    def test_right_and_middle_buttons_are_not_dropped(self, monkeypatch):
        rec = self._make_recorder(monkeypatch)

        for button in ("right", "middle"):
            rec._on_click(100, 80, _FakeButton(button), True)
            rec._on_click(100, 80, _FakeButton(button), False)

        states = [line for line in rec._lines if line.startswith("mouse ")]
        assert states == [
            "mouse right down", "mouse right up",
            "mouse middle down", "mouse middle up",
        ]

    def test_raw_side_button_emits_click_when_movement_is_disabled(self):
        rec = MacroRecorder(
            target_window={"left": 0, "top": 0, "width": 1000, "height": 800},
            capture=_Capture(), layout=_Layout(), win_left=0, win_top=0,
            record_mouse_movement=False,
        )
        rec._recording = True
        rec._on_raw_button("x1", True, 100, 80, 1_000_000_000)
        rec._on_raw_button("x1", False, 100, 80, 1_100_000_000)

        assert rec._lines == [
            "place (0.1, 0.1)", "mouse x1 down", "wait 0.1",
            "place (0.1, 0.1)", "mouse x1 up",
        ]

    def test_raw_side_button_preserves_high_precision_down_up(self):
        rec = _make_recorder([], precision=PRECISION_HIGH)
        rec._recording = True
        rec._on_raw_button("x2", True, 100, 80, 1_000_000_000)
        rec._on_raw_button("x2", False, 100, 80, 1_100_000_000)

        assert [(event.kind, event.values) for event in rec._trace_events] == [
            ("button", ("x2", True)),
            ("button", ("x2", False)),
        ]


class TestScroll:
    def test_scroll_down_emitted_with_coord_target(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True

        rec._on_scroll(100, 80, 0, -1)  # dy<0 → down

        assert lines == ["scroll (0.1, 0.1) down 1"]

    def test_scroll_up_emitted(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True

        rec._on_scroll(100, 80, 0, 1)  # dy>0 → up

        assert lines == ["scroll (0.1, 0.1) up 1"]

    def test_scroll_outside_window_ignored(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True

        rec._on_scroll(9999, 9999, 0, 1)  # 落在窗口矩形外

        assert lines == []

    def test_scroll_zero_delta_ignored(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True

        rec._on_scroll(100, 80, 0, 0)

        assert lines == []

    def test_wait_inserted_before_scroll(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        rec._last_action_time = time.monotonic() - 1.0  # 间隔 1s > 0.1s 阈值

        rec._on_scroll(100, 80, 0, -1)

        assert len(lines) == 2
        assert lines[0].startswith("wait ")
        assert lines[1] == "scroll (0.1, 0.1) down 1"

    def test_driver_burst_is_recorded_as_one_scroll(self, monkeypatch):
        """一次物理滚动被驱动拆成短脉冲时，只生成一次武器切换。"""
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        ticks = iter((10.0, 10.031789, 10.072051))
        monkeypatch.setattr(
            "lvjiang.core.macro_recorder.time.monotonic", lambda: next(ticks))

        rec._on_scroll(500, 397, 0, -1)
        rec._on_scroll(500, 397, 0, -1)
        rec._on_scroll(500, 397, 0, -1)

        assert lines == ["scroll (0.5, 0.4963) down 1"]
        assert rec._last_action_time == 10.072051

    def test_separate_scroll_after_burst_window_is_preserved(self, monkeypatch):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        ticks = iter((10.0, 10.2))
        monkeypatch.setattr(
            "lvjiang.core.macro_recorder.time.monotonic", lambda: next(ticks))

        rec._on_scroll(500, 397, 0, -1)
        rec._on_scroll(500, 397, 0, -1)

        assert lines == [
            "scroll (0.5, 0.4963) down 1",
            "wait 0.2",
            "scroll (0.5, 0.4963) down 1",
        ]


class TestPress:
    def test_quick_tap_preserves_down_wait_up(self, monkeypatch):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        key = _char_key("a")
        ticks = iter((10_000_000_000, 10_020_000_000))
        monkeypatch.setattr(
            "lvjiang.core.macro_recorder.time.monotonic_ns", lambda: next(ticks))

        rec._on_key_press(key)
        rec._on_key_release(key)

        assert lines == ['press "A" down', "wait 0.02", 'press "A" up']

    def test_special_key_name_resolved(self, monkeypatch):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        key = _special_key("esc")
        ticks = iter((10_000_000_000, 10_100_000_000))
        monkeypatch.setattr(
            "lvjiang.core.macro_recorder.time.monotonic_ns", lambda: next(ticks))

        rec._on_key_press(key)
        rec._on_key_release(key)

        assert lines == ['press "ESC" down', "wait 0.1", 'press "ESC" up']

    def test_long_press_is_not_collapsed_to_hold(self, monkeypatch):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        key = _char_key("w")
        ticks = iter((10_000_000_000, 11_000_000_000))
        monkeypatch.setattr(
            "lvjiang.core.macro_recorder.time.monotonic_ns", lambda: next(ticks))

        rec._on_key_press(key)
        rec._on_key_release(key)

        assert lines == ['press "W" down', "wait 1", 'press "W" up']

    def test_interleaved_keyboard_and_mouse_keep_global_order(self, monkeypatch):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        key = _char_key("r")
        ticks = iter((10_000_000_000, 10_300_000_000))
        monkeypatch.setattr(
            "lvjiang.core.macro_recorder.time.monotonic_ns", lambda: next(ticks))

        rec._on_key_press(key)
        rec._record_mouse_button("left", True, 100, 80, 10.1)
        rec._record_mouse_button("left", False, 100, 80, 10.2)
        rec._on_key_release(key)

        assert lines == [
            'press "R" down',
            "wait 0.1", "place (0.1, 0.1)", "mouse left down",
            "wait 0.1", "place (0.1, 0.1)", "mouse left up",
            "wait 0.1", 'press "R" up',
        ]

    def test_reserved_hotkeys_never_recorded(self):
        """F8/F9/F10/F12 是录制器自身的全局热键，不应被误录成 press 语句"""
        for name in ("f8", "f9", "f10", "f12"):
            lines: list[str] = []
            rec = _make_recorder(lines)
            rec._recording = True
            key = _special_key(name)

            rec._on_key_press(key)
            rec._on_key_release(key)

            assert lines == [], f"{name} 不应被录制"

    def test_reserved_hotkey_does_not_update_last_action_time(self):
        """按 F12 停止录制前的等待时间应累积到下一条真正被记录的动作上"""
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        rec._last_action_time = time.monotonic() - 1.0

        f12 = _special_key("f12")
        rec._key_press_times[f12] = time.monotonic()
        rec._on_key_release(f12)  # 不记录，也不刷新 _last_action_time

        rec._record_mouse_button("left", True, 100, 80, time.monotonic())

        # 跨越了被过滤掉的 F12 事件，等待时间仍然完整体现在下一条动作前
        assert lines[0].startswith("wait ")
        assert lines[1:] == ["place (0.1, 0.1)", "mouse left down"]

    def test_unrecognized_key_ignored(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        key = _special_key("media_play_pause")  # 不在标准键名表里

        rec._key_press_times[key] = time.monotonic()
        rec._on_key_release(key)

        assert lines == []

    def test_wait_inserted_before_key_down(self, monkeypatch):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        rec._last_action_time = time.monotonic() - 1.0
        key = _char_key("a")
        now_ns = time.monotonic_ns()
        monkeypatch.setattr(
            "lvjiang.core.macro_recorder.time.monotonic_ns", lambda: now_ns)

        rec._on_key_press(key)

        assert len(lines) == 2
        assert lines[0].startswith("wait ")
        assert lines[1] == 'press "A" down'


class TestTrailingWaitOnStop:
    def test_stop_emits_trailing_wait_after_idle_gap(self):
        """收尾：按 F12 停止录制前有明显空闲，应在脚本末尾补一条 wait，
        否则脚本读不出录制时实际经过的等待"""
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True

        rec._emit_line('press "A" down')

        rec._last_action_time = time.monotonic() - 1.0  # 模拟停止前空等了 1s
        text = rec.stop()

        assert lines[-1].startswith("wait ")
        assert text == "\n".join(lines)

    def test_stop_preserves_even_short_trailing_wait(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True

        rec._emit_line('press "A" down')
        rec._last_action_time = time.monotonic() - 0.01

        rec.stop()

        assert lines[0] == 'press "A" down'
        assert lines[1].startswith("wait 0.01")

    def test_stop_when_not_recording_has_no_side_effect(self):
        """已经不在录制状态时调用 stop() 只是返回现有文本，不应额外插入 wait"""
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True

        rec._emit_line('press "A" down')

        rec._recording = False  # 无真实 listener，模拟已经停止过一次
        assert rec.stop() == "\n".join(lines)
        assert lines == ['press "A" down']


class TestRawInputMove:
    def test_mouse_movement_can_be_disabled(self):
        lines: list[str] = []
        rec = MacroRecorder(
            target_window={"left": 0, "top": 0, "width": 1000, "height": 800},
            capture=_Capture(), layout=_Layout(), win_left=0, win_top=0,
            on_line=lines.append, record_mouse_movement=False,
        )
        rec._recording = True

        rec._on_raw_move(20, 20, 1_000_000_000)
        with rec._lock:
            rec._flush_raw_frame()

        assert lines == []

    def test_low_precision_merges_continuous_raw_packets(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        start_ns = 1_000_000_000

        rec._on_raw_move(10, -8, start_ns)
        rec._on_raw_move(5, 4, start_ns + 2_000_000)
        rec._on_raw_move(-20, 8, start_ns + 6_000_000)
        with rec._lock:
            rec._flush_raw_frame()

        assert lines == [
            "move by (-0.005, 0.005) duration 0.006",
        ]

    def test_low_precision_merges_raw_packets_within_100ms(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        start_ns = 2_000_000_000

        rec._on_raw_move(10, 0, start_ns)
        rec._on_raw_move(10, 0, start_ns + 99_000_000)
        with rec._lock:
            rec._flush_raw_frame()

        assert lines == [
            "move by (0.02, 0) duration 0.099",
        ]

    def test_low_precision_splits_after_100ms_idle_gap(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        start_ns = 2_000_000_000

        rec._on_raw_move(10, 0, start_ns)
        rec._on_raw_move(10, 0, start_ns + 120_000_000)
        with rec._lock:
            rec._flush_raw_frame()

        assert lines == [
            "move by (0.01, 0) duration 0.001",
            "wait 0.119",
            "move by (0.01, 0) duration 0.001",
        ]

    def test_low_precision_allows_opposite_packets_to_cancel(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        start_ns = 3_000_000_000

        rec._on_raw_move(10, 0, start_ns)
        rec._on_raw_move(-10, 0, start_ns + 2_000_000)
        with rec._lock:
            rec._flush_raw_frame()

        assert lines == []

    def test_high_precision_preserves_every_raw_packet(self):
        rec = _make_recorder([], precision=PRECISION_HIGH)
        rec._recording = True
        start_ns = 3_000_000_000

        rec._on_raw_move(10, 0, start_ns)
        rec._on_raw_move(-10, 5, start_ns + 2_000_000)

        trace = rec.build_input_trace()
        assert [(event.at_us, event.kind, event.values)
                for event in trace.events] == [
            (0, "move", (10, 0)),
            (2000, "move", (-10, 5)),
        ]

    def test_raw_relative_components_are_clamped_to_signed_unit_range(self):
        rec = _make_recorder([])
        assert rec._raw_delta_to_canvas_ratio(2000, -1600) == (1.0, -1.0)

    def test_absolute_coordinates_are_clamped_to_canvas_unit_range(self):
        rec = _make_recorder([])
        assert rec._screen_to_canvas_ratio(-100, 900) == (0.0, 1.0)

    def test_raw_move_is_preserved_while_mouse_button_is_held(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        rec._button_presses["left"] = (100, 100, 0.9)

        rec._on_raw_move(20, 20, 1_000_000_000)
        with rec._lock:
            rec._flush_raw_frame()

        assert lines == ["move by (0.02, 0.025) duration 0.001"]
