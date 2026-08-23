"""脚本录制数据链路测试 — MacroRecorder on_line 实时回调

不依赖 pynput 真实监听/Qt：直接构造 recorder 并调用内部方法，
验证每生成一行 DSL 都会回调 on_line，且 stop() 全文与回调行一致。
（ScriptRecordDialog 仅把 on_line 桥接到 UI 线程追加文本，核心在此链路）
"""

import time

from lvjiang.ui.macros.recorder import MacroRecorder


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
        rec._last_action_time = time.monotonic() - 1.0  # 间隔 1s > 0.3s 阈值

        rec._on_scroll(100, 80, 0, -1)

        assert len(lines) == 2
        assert lines[0].startswith("wait ")
        assert lines[1] == "scroll (0.1, 0.1) down 1"


class TestPress:
    def test_quick_tap_emitted(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        key = _char_key("a")

        rec._key_press_times[key] = time.monotonic()
        rec._on_key_release(key)

        assert lines == ['press "A"']

    def test_special_key_name_resolved(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        key = _special_key("esc")

        rec._key_press_times[key] = time.monotonic()
        rec._on_key_release(key)

        assert lines == ['press "ESC"']

    def test_long_press_emits_hold_modifier(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        key = _char_key("w")

        rec._key_press_times[key] = time.monotonic() - 1.0  # 按住 1s > 0.5s 阈值
        rec._on_key_release(key)

        assert len(lines) == 1
        assert lines[0].startswith('press "W" hold ')

    def test_reserved_hotkeys_never_recorded(self):
        """F8/F9/F10/F12 是录制器自身的全局热键，不应被误录成 press 语句"""
        for name in ("f8", "f9", "f10", "f12"):
            lines: list[str] = []
            rec = _make_recorder(lines)
            rec._recording = True
            key = _special_key(name)

            rec._key_press_times[key] = time.monotonic()
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

        rec._press_pos = (100, 80)
        rec._press_time = time.monotonic()
        rec._handle_release(100, 80)

        # 跨越了被过滤掉的 F12 事件，等待时间仍然完整体现在下一条动作前
        assert lines[0].startswith("wait ")
        assert lines[1] == "click (0.1, 0.1)"

    def test_unrecognized_key_ignored(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        key = _special_key("media_play_pause")  # 不在标准键名表里

        rec._key_press_times[key] = time.monotonic()
        rec._on_key_release(key)

        assert lines == []

    def test_wait_inserted_before_press(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True
        rec._last_action_time = time.monotonic() - 1.0
        key = _char_key("a")

        rec._key_press_times[key] = time.monotonic()
        rec._on_key_release(key)

        assert len(lines) == 2
        assert lines[0].startswith("wait ")
        assert lines[1] == 'press "A"'


class TestTrailingWaitOnStop:
    def test_stop_emits_trailing_wait_after_idle_gap(self):
        """收尾：按 F12 停止录制前有明显空闲，应在脚本末尾补一条 wait，
        否则脚本读不出录制时实际经过的等待"""
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True

        rec._press_pos = (100, 80)
        rec._press_time = time.monotonic()
        rec._handle_release(100, 80)

        rec._last_action_time = time.monotonic() - 1.0  # 模拟停止前空等了 1s
        text = rec.stop()

        assert lines[-1].startswith("wait ")
        assert text == "\n".join(lines)

    def test_stop_no_trailing_wait_when_gap_below_threshold(self):
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True

        rec._press_pos = (100, 80)
        rec._press_time = time.monotonic()
        rec._handle_release(100, 80)

        rec.stop()  # 紧接着停止，间隔远小于 0.3s 阈值

        assert lines == ["click (0.1, 0.1)"]

    def test_stop_when_not_recording_has_no_side_effect(self):
        """已经不在录制状态时调用 stop() 只是返回现有文本，不应额外插入 wait"""
        lines: list[str] = []
        rec = _make_recorder(lines)
        rec._recording = True

        rec._press_pos = (100, 80)
        rec._press_time = time.monotonic()
        rec._handle_release(100, 80)

        rec._recording = False  # 无真实 listener，模拟已经停止过一次
        assert rec.stop() == "\n".join(lines)
        assert lines == ["click (0.1, 0.1)"]
