"""鼠标/键盘操作录制器 - 将用户操作翻译为 DSL 语句文本

捕获 pynput 鼠标 + 键盘事件，把落在目标窗口内的点击/拖拽/滚轮归一化为
画布比例坐标，Win32 Raw Input 转换为有符号归一化 ``move by``，
按键翻译成本项目标准键名，生成 click / drag / scroll / press / move /
wait 语句行。产物即合法 .wf 脚本，可直接剪切复用，回放完全走
现有 DSL 引擎（engine._coord_ratio_to_screen 的逆运算 + normalize_key
同源的标准键名表）。
"""

import sys
import threading
import time

from loguru import logger

from ...core.desktop.win32_keyboard import KEY_NAME_TO_VK
from ...core.input_trace import (
    TRACE_PLACEHOLDER,
    VALID_BUTTONS,
    InputTrace,
    InputTraceEvent,
)
from ...i18n import tr

try:
    from pynput import keyboard as pynput_keyboard
    from pynput import mouse as pynput_mouse
except Exception:  # pragma: no cover - 环境缺失 pynput 时降级
    pynput_keyboard = None
    pynput_mouse = None


# ─── 事件聚合阈值 ─────────────────────────────────────────
_LOW_PRECISION_INTERVAL_S = 0.100  # 低精度以 100ms 为合并/等待粒度
_RAW_IDLE_S = _LOW_PRECISION_INTERVAL_S
_RAW_LAST_DURATION_S = 0.001  # 轨迹末包无后继时间戳，以 1ms 完成

PRECISION_LOW = "low"
PRECISION_HIGH = "high"

# F8/F9/F10 是主窗口全局热键，F12 是录制对话框的临时全局热键（默认值，
# 用户可在配置管理→热键设置里改绑其他键，调用方应传入当前实际生效的
# 键位集合作为 __init__ 的 reserved_keys；这里只是没传时的兜底默认值）。
# 录制中按下这几个键是在操作录制器本身，不应该被误录成 press 语句
# （暂不处理"录制热键与游戏内快捷键冲突"这个更大的问题，见需求备注）。
_RESERVED_KEYS = {"F8", "F9", "F10", "F12"}

# pynput 特殊键 → 本项目标准键名（与 win32_keyboard.KEY_NAME_TO_VK 同源，
# 只覆盖 pynput.keyboard.Key 里游戏自动化场景常见的键，不追求全量覆盖）
_SPECIAL_KEY_NAMES: dict[str, str] = {
    "esc": "ESC", "space": "SPACE", "enter": "ENTER", "tab": "TAB",
    "backspace": "BACKSPACE", "delete": "DELETE", "insert": "INSERT",
    "home": "HOME", "end": "END", "page_up": "PAGEUP", "page_down": "PAGEDOWN",
    "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT",
    "shift": "SHIFT", "shift_l": "SHIFT", "shift_r": "SHIFT",
    "ctrl": "CTRL", "ctrl_l": "LCTRL", "ctrl_r": "RCTRL",
    "alt": "ALT", "alt_l": "LALT", "alt_r": "RALT", "alt_gr": "RALT",
    "cmd": "WIN", "cmd_l": "LWIN", "cmd_r": "RWIN",
    "caps_lock": "CAPSLOCK", "num_lock": "NUMLOCK", "scroll_lock": "SCROLLLOCK",
    "print_screen": "PRINTSCREEN", "pause": "PAUSE",
    **{f"f{i}": f"F{i}" for i in range(1, 13)},
}


def _format_number(value: float, digits: int = 3) -> str:
    """以固定最大精度输出 DSL 数字，同时去掉无意义的末尾 0。"""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _pynput_key_to_dsl_name(key) -> str | None:
    """pynput 按键对象 → 本项目 press 语句标准键名，无法识别返回 None

    普通字符键（KeyCode.char）直接转大写；特殊键（Key 枚举）查表；
    查不到/无法识别的键（如某些多媒体键）直接丢弃，不强行拼一个
    normalize_key() 校验不过的名字进脚本。
    """
    char = getattr(key, "char", None)
    if char:
        name = char.upper()
        return name if name in KEY_NAME_TO_VK else None
    name = _SPECIAL_KEY_NAMES.get(getattr(key, "name", ""))
    return name if name and name in KEY_NAME_TO_VK else None


class MacroRecorder:
    """鼠标/键盘操作录制器 → 生成 DSL 语句文本

    绝对坐标归一化到画布 [0,1]，相对位移归一化到 [-1,1]，
    与引擎的画布坐标换算同源，
    窗口缩放/移动后回放仍准确。鼠标操作仅录制落在目标窗口矩形内的部分；
    键盘录制是全局的（pynput 无法判断按键是发给哪个窗口），F8/F9/F10/F12
    这几个全局热键会被排除，其余按键一律录制。
    """

    def __init__(self, target_window: dict, capture, layout, win_left: int, win_top: int,
                 on_line=None, precision: str = PRECISION_LOW,
                 reserved_keys: set[str] | None = None,
                 record_mouse_movement: bool = True):
        if precision not in {PRECISION_LOW, PRECISION_HIGH}:
            raise ValueError(f"未知录制精度: {precision}")
        self._win = target_window
        self._capture = capture
        self._layout = layout
        self._win_left = win_left
        self._win_top = win_top
        self._on_line = on_line                # 每生成一行 DSL 的实时回调（监听线程内调用）
        self.precision = precision
        self.record_mouse_movement = record_mouse_movement
        # 当前生效的系统热键（跟随「配置管理→热键设置」，未传时用默认 F8/F9/F10/F12）
        self._reserved_keys = reserved_keys if reserved_keys is not None else _RESERVED_KEYS

        self._listener = None
        self._kb_listener = None
        self._raw_listener = None
        self._lock = threading.Lock()
        self._lines: list[str] = []          # 已生成的 DSL 行
        # 已按下的鼠标键；只用于过滤孤立/重复 up，不参与 down/up 合并。
        self._button_presses: dict[str, tuple[int, int, float]] = {}
        self._key_press_times: dict = {}     # 已按下但未松开的按键 → 按下时刻
        # 低精度模式把同一连续段合并为一个 move by；高精度模式不使用它。
        self._raw_pending: tuple[float, float, int, int] | None = None
        self._raw_canvas_size = (0.0, 0.0)
        self._foreground_user32 = None
        self._trace_origin_ns: int | None = None
        self._trace_events: list[InputTraceEvent] = []
        self._final_text = ""
        self._last_action_time: float | None = None      # 上次操作完成时刻
        self._recording = False

    # ─── 生命周期 ─────────────────────────────────────────

    def start(self):
        """开始录制（启动 pynput 监听线程）"""
        if pynput_mouse is None:
            raise RuntimeError(tr("pynput 未安装，无法录制"))
        if self._recording:
            return
        self._lines = []
        self._button_presses = {}
        self._key_press_times = {}
        self._trace_events = []
        self._trace_origin_ns = time.monotonic_ns()
        self._final_text = ""
        self._reset_raw_frame()
        w, h = self._capture.get_capture_size()
        canvas = self._layout.get_canvas()
        self._raw_canvas_size = (canvas.w_ratio * w, canvas.h_ratio * h)
        self._last_action_time = None
        self._recording = True
        # 防护补丁：避免退出竞态下 pynput 钩子回调返回 None（幂等）
        from ...core.pynput_patch import install as _install_pynput_patch
        _install_pynput_patch()
        # Raw Input 必须在 pynput 之前启动。即使过滤视角移动也要保持监听，
        # 因为部分游戏/鼠标驱动只在 Raw Input 中暴露物理侧键。
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            self._foreground_user32 = ctypes.windll.user32
            self._foreground_user32.GetForegroundWindow.restype = wintypes.HWND
            from ...core.desktop.raw_input import RawMouseListener
            try:
                self._raw_listener = RawMouseListener(
                    self._on_raw_move, self._on_raw_button)
                self._raw_listener.start()
            except Exception as exc:
                # 仍允许 pynput 兜底录制普通鼠标键；Raw Input 不可用不应让
                # 整个脚本录制功能失效。
                logger.warning(f"Raw Input 鼠标监听启动失败，降级到 pynput: {exc}")
                self._raw_listener = None
        try:
            self._listener = pynput_mouse.Listener(
                on_click=self._on_click, on_scroll=self._on_scroll)
            self._listener.start()
            if pynput_keyboard is not None:
                self._kb_listener = pynput_keyboard.Listener(
                    on_press=self._on_key_press, on_release=self._on_key_release)
                self._kb_listener.start()
            else:  # pragma: no cover - 环境缺失 pynput.keyboard 时降级为纯鼠标录制
                logger.warning("pynput.keyboard 不可用，本次录制不捕获按键")
        except Exception:
            self._recording = False
            if self._raw_listener is not None:
                self._raw_listener.stop()
                self._raw_listener = None
            if self._listener is not None:
                self._listener.stop()
                self._listener = None
            if self._kb_listener is not None:
                self._kb_listener.stop()
                self._kb_listener = None
            raise
        logger.info("宏录制已开始")

    def stop(self) -> str:
        """停止录制，返回生成的 DSL 文本"""
        if not self._recording:
            return self._final_text or "\n".join(self._lines)
        self._recording = False
        # 低精度收尾 wait：反映最后动作到停止录制之间的空闲时长。
        # 否则空等一段时间后按 F12 结束，脚本末尾会直接紧跟着最后一条动作，
        # 完全体现不出录制时实际经过的等待
        with self._lock:
            if self.precision == PRECISION_LOW:
                self._flush_raw_frame()
                self._maybe_emit_wait(time.monotonic())
        if self._raw_listener is not None:
            self._raw_listener.stop()
            self._raw_listener = None
        if self._listener is not None:
            try:
                # stop() 只投递停止信号，必须 join 等监听线程卸载钩子，
                # 否则残留钩子回调在退出时踩空
                self._listener.stop()
                self._listener.join(1.0)
            except Exception:
                pass
            self._listener = None
        if self._kb_listener is not None:
            try:
                self._kb_listener.stop()
                self._kb_listener.join(1.0)
            except Exception:
                pass
            self._kb_listener = None
        if self.precision == PRECISION_HIGH:
            logger.info(f"高精度录制已结束，共捕获 {len(self._trace_events)} 个事件")
            if not self._trace_events:
                return ""
            self._final_text = (
                "# 高精度输入轨迹由 workflows/lvtrace 配套文件保存\n"
                f'replay input_trace "{TRACE_PLACEHOLDER}"'
            )
            return self._final_text
        logger.info(f"低精度录制已结束，共生成 {len(self._lines)} 条语句")
        self._final_text = "\n".join(self._lines)
        return self._final_text

    def build_input_trace(self) -> InputTrace:
        """构造高精度轨迹；保存层负责序列化与生成内容哈希。"""
        if self.precision != PRECISION_HIGH:
            raise RuntimeError("低精度录制没有 lvtrace 数据")
        width = max(1, round(self._raw_canvas_size[0]))
        height = max(1, round(self._raw_canvas_size[1]))
        events = tuple(sorted(
            self._trace_events,
            key=lambda event: event.at_us,
        ))
        return InputTrace(width, height, events)

    # ─── 事件回调（监听线程） ──────────────────────────────

    def _emit_line(self, line: str):
        """追加一行 DSL 并触发实时回调（回调异常不影响录制）"""
        self._lines.append(line)
        if self._on_line is not None:
            try:
                self._on_line(line)
            except Exception:  # noqa: BLE001
                logger.exception("on_line 回调异常")

    def _on_click(self, sx, sy, button, pressed):
        """pynput 鼠标键按下/松开回调。

        高精度写入轨迹；低精度同样保留五种鼠标键的原始 down/up，并用
        place 固定每个事件发生时的指针坐标。不得把一对事件提前压成 click，
        否则会破坏与键盘事件交叠时的全局顺序。
        """
        if pynput_mouse is None:
            return
        with self._lock:
            try:
                if not self._recording:
                    return
                if self.precision == PRECISION_HIGH:
                    if not self._target_is_foreground():
                        return
                    # pynput 各平台后端的 Button 是具名枚举，枚举成员的
                    # .name 天然就是 "left"/"right"/"middle"/"x1"/"x2"
                    # （侧键 x1/x2 只有 Windows 后端才有），直接复用它比
                    # 维护一份「枚举成员 → 名字」的映射表更省事，也不会
                    # 在没有 x1/x2 成员的平台上因为引用不存在的属性报错。
                    button_name = getattr(button, "name", None)
                    if button_name in VALID_BUTTONS:
                        self._append_trace_event(
                            "button", (button_name, bool(pressed)),
                            time.monotonic_ns())
                    return
                button_name = getattr(button, "name", None)
                if button_name not in VALID_BUTTONS:
                    return
                # Windows 的物理侧键由 Raw Input 统一录制，避免与 pynput 的
                # legacy hook 同时命中而生成两条 click。
                if (sys.platform == "win32" and self._raw_listener is not None
                        and button_name in {"x1", "x2"}):
                    return
                self._flush_raw_frame()
                now = time.monotonic()
                self._record_mouse_button(
                    button_name, bool(pressed), int(sx), int(sy), now)
            except Exception:  # noqa: BLE001 - pynput 低层钩子回调里的异常可能被
                # 静默吞掉（Windows 要求钩子过程不能抛出/长时间阻塞，否则可能被
                # 判定为无响应并卸载），必须自己兜底记录，否则一旦出 bug 会
                # 表现成"什么都没发生"、日志里也看不到任何报错线索。
                logger.exception("_on_click 处理异常")

    def _maybe_emit_wait(self, event_time: float):
        """写入上一事件到本事件之间的完整时间，不丢弃短间隔。"""
        if self._last_action_time is None:
            return
        gap = event_time - self._last_action_time
        if gap > 0:
            gap_text = _format_number(gap, digits=6)
            self._emit_line(f"wait {gap_text}")
            logger.debug(f"录制等待: {gap_text}s")

    def _on_scroll(self, sx, sy, dx, dy):  # noqa: ARG002 - dx（水平滚动）暂不支持
        """pynput 鼠标滚轮回调：落在目标窗口内才录制，带坐标目标避免回放时
        落到画布中心（scroll 语句省略目标时默认在画布中心滚动，与录制时
        实际滚动的位置不是一回事）"""
        with self._lock:
            try:
                if not self._recording:
                    return
                if self.precision == PRECISION_HIGH:
                    if dy and self._target_is_foreground():
                        self._append_trace_event(
                            "wheel", (int(round(dy * 120)),),
                            time.monotonic_ns())
                    return
                isx, isy = int(sx), int(sy)
                if not self._in_window(isx, isy):
                    return
                if dy == 0:
                    return
                self._flush_raw_frame()
                now = time.monotonic()
                self._maybe_emit_wait(now)
                direction = "up" if dy > 0 else "down"
                amount = max(1, abs(round(dy)))
                rx, ry = self._screen_to_canvas_ratio(isx, isy)
                self._emit_line(f"scroll ({rx}, {ry}) {direction} {amount}")
                logger.debug(f"录制滚轮: ({rx}, {ry}) {direction} {amount}")
                self._last_action_time = now
            except Exception:  # noqa: BLE001 - 见 _on_click 里同样处理的注释
                logger.exception("_on_scroll 处理异常")

    def _on_key_press(self, key):
        """立即记录键盘 down；自动重复事件只保留第一次。"""
        with self._lock:
            try:
                if not self._recording:
                    return
                name = _pynput_key_to_dsl_name(key)
                if name is None or name in self._reserved_keys:
                    return
                # 只算一次前台判定：既避免高精度模式下每次按键都重复一遍
                # GetForegroundWindow 系统调用，也避免两次调用之间前台窗口
                # 恰好切换导致「_key_press_times 记了、trace 事件却没记」
                # 的不一致。
                high_precision = self.precision == PRECISION_HIGH
                if high_precision and not self._target_is_foreground():
                    return
                if key in self._key_press_times:
                    return
                now_ns = time.monotonic_ns()
                now = now_ns / 1_000_000_000
                self._key_press_times[key] = now
                if high_precision:
                    self._append_trace_event("key", (name, True), now_ns)
                    return
                self._flush_raw_frame()
                self._maybe_emit_wait(now)
                self._emit_line(f'press "{name}" down')
                self._last_action_time = now
            except Exception:  # noqa: BLE001 - 见 _on_click 里同样处理的注释
                logger.exception("_on_key_press 处理异常")

    def _on_key_release(self, key):
        """立即记录键盘 up，不与 down 合并，保留交叠输入的真实顺序。"""
        with self._lock:
            try:
                if not self._recording:
                    return
                if self._key_press_times.pop(key, None) is None:
                    return

                name = _pynput_key_to_dsl_name(key)
                if name is None:
                    logger.debug(f"忽略无法识别的按键: {key!r}")
                    return
                if name in self._reserved_keys:
                    return

                now_ns = time.monotonic_ns()
                now = now_ns / 1_000_000_000
                if self.precision == PRECISION_HIGH:
                    self._append_trace_event("key", (name, False), now_ns)
                    return
                self._flush_raw_frame()
                self._maybe_emit_wait(now)
                self._emit_line(f'press "{name}" up')
                self._last_action_time = now
            except Exception:  # noqa: BLE001 - 见 _on_click 里同样处理的注释
                logger.exception("_on_key_release 处理异常")

    # ─── Raw Input 相对移动 ──────────────────────────────────

    def _on_raw_move(self, dx: int, dy: int, timestamp_ns: int):
        """Raw Input 回调：高精度逐包保存，低精度以 100ms 空档切段。"""
        with self._lock:
            try:
                if (not self._recording or not self.record_mouse_movement
                        or not self._target_is_foreground()):
                    self._reset_raw_frame()
                    return
                if dx == 0 and dy == 0:
                    return
                if self.precision == PRECISION_HIGH:
                    canvas_w, canvas_h = self._ensure_raw_canvas_size()
                    self._append_trace_event(
                        "move",
                        (
                            max(-canvas_w, min(canvas_w, int(dx))),
                            max(-canvas_h, min(canvas_h, int(dy))),
                        ),
                        timestamp_ns,
                    )
                    return
                event_time = timestamp_ns / 1_000_000_000
                pending = self._raw_pending
                if pending is None:
                    self._raw_pending = (event_time, event_time, int(dx), int(dy))
                    return
                start, last, total_dx, total_dy = pending
                if event_time - last >= _RAW_IDLE_S:
                    self._flush_raw_frame()
                    self._raw_pending = (event_time, event_time, int(dx), int(dy))
                    return
                self._raw_pending = (
                    start, event_time, total_dx + int(dx), total_dy + int(dy))
            except Exception:  # noqa: BLE001 - Raw Input 消息线程不能被业务异常打断
                logger.exception("_on_raw_move 处理异常")

    def _on_raw_button(
        self,
        button_name: str,
        pressed: bool,
        sx: int,
        sy: int,
        timestamp_ns: int,
    ):
        """Raw Input 物理侧键回调；Windows 下作为 x1/x2 的权威来源。"""
        with self._lock:
            try:
                if not self._recording or not self._target_is_foreground():
                    return
                if button_name not in {"x1", "x2"}:
                    return
                if self.precision == PRECISION_HIGH:
                    self._append_trace_event(
                        "button", (button_name, bool(pressed)), timestamp_ns)
                    return
                self._flush_raw_frame()
                self._record_mouse_button(
                    button_name,
                    bool(pressed),
                    int(sx),
                    int(sy),
                    timestamp_ns / 1_000_000_000,
                )
            except Exception:  # noqa: BLE001
                logger.exception("_on_raw_button 处理异常")

    def _record_mouse_button(
        self,
        button_name: str,
        pressed: bool,
        sx: int,
        sy: int,
        event_time: float,
    ) -> None:
        """逐个写入鼠标 down/up；place 保留事件发生时的绝对位置。"""
        if pressed:
            if button_name in self._button_presses or not self._in_window(sx, sy):
                return
            self._button_presses[button_name] = (sx, sy, event_time)
        elif self._button_presses.pop(button_name, None) is None:
            return
        self._maybe_emit_wait(event_time)
        rx, ry = self._screen_to_canvas_ratio(sx, sy)
        self._emit_line(f"place ({rx}, {ry})")
        state = "down" if pressed else "up"
        self._emit_line(f"mouse {button_name} {state}")
        self._last_action_time = event_time

    def _raw_delta_to_canvas_ratio(self, dx: int, dy: int) -> tuple[float, float]:
        """像素位移 → 画布宽高比例；相对分量保留符号且限定在 [-1,1]。"""
        canvas_w, canvas_h = self._ensure_raw_canvas_size()
        rx = dx / canvas_w
        ry = dy / canvas_h
        return max(-1.0, min(1.0, rx)), max(-1.0, min(1.0, ry))

    def _ensure_raw_canvas_size(self) -> tuple[int, int]:
        """返回至少为 1px 的缓存画布尺寸。"""
        canvas_w, canvas_h = self._raw_canvas_size
        if not canvas_w or not canvas_h:
            w, h = self._capture.get_capture_size()
            canvas = self._layout.get_canvas()
            canvas_w = canvas.w_ratio * w
            canvas_h = canvas.h_ratio * h
            self._raw_canvas_size = (canvas_w, canvas_h)
        return max(1, round(canvas_w)), max(1, round(canvas_h))

    def _target_is_foreground(self) -> bool:
        """Raw Input 是系统级的，仅在已扫描的目标窗口处于前台时录制。"""
        hwnd = self._win.get("hwnd")
        if sys.platform != "win32" or not hwnd:
            return True
        user32 = self._foreground_user32
        if user32 is None:
            return False
        return int(user32.GetForegroundWindow() or 0) == int(hwnd)

    def _reset_raw_frame(self):
        self._raw_pending = None

    def _flush_raw_frame(self):
        """输出尚未获得后继时间戳的最后一个 Raw Input 包。"""
        pending = self._raw_pending
        if pending is None:
            return
        self._reset_raw_frame()
        start, end, dx, dy = pending
        dx_ratio, dy_ratio = self._raw_delta_to_canvas_ratio(dx, dy)
        self._emit_raw_packet(
            start,
            dx_ratio,
            dy_ratio,
            max(end - start, _RAW_LAST_DURATION_S),
        )

    def _append_trace_event(
        self,
        kind: str,
        values: tuple,
        timestamp_ns: int,
    ) -> None:
        origin = self._trace_origin_ns
        if origin is None:
            origin = timestamp_ns
            self._trace_origin_ns = origin
        at_us = max(0, (int(timestamp_ns) - origin) // 1000)
        self._trace_events.append(InputTraceEvent(at_us, kind, values))

    def _emit_raw_packet(
        self,
        event_time: float,
        dx: float,
        dy: float,
        duration: float,
    ):
        """将单个 Raw Input 包输出为 ``move by``，不合并轨迹点。"""
        if abs(dx) < 0.0000005 and abs(dy) < 0.0000005:
            return
        self._maybe_emit_wait(event_time)
        dx_text = _format_number(dx, digits=6)
        dy_text = _format_number(dy, digits=6)
        duration_text = _format_number(duration)
        self._emit_line(
            f"move by ({dx_text}, {dy_text}) duration {duration_text}")
        logger.debug(
            f"录制 Raw Input 移动: ({dx_text}, {dy_text}) "
            f"{duration_text}s")
        self._last_action_time = event_time + duration

    # ─── 坐标转换 ─────────────────────────────────────────

    def _in_window(self, sx: int, sy: int) -> bool:
        """屏幕坐标是否落在目标窗口矩形内"""
        left = self._win["left"]
        top = self._win["top"]
        right = left + self._win["width"]
        bottom = top + self._win["height"]
        return left <= sx <= right and top <= sy <= bottom

    def _screen_to_canvas_ratio(self, sx: int, sy: int) -> tuple[float, float]:
        """屏幕绝对坐标 → 画布归一化比例（_coord_ratio_to_screen 的逆运算）

        屏幕 = 窗口偏移 + 画布原点 + 归一化比例 × 画布尺寸，反解归一化比例。
        """
        cap_x = sx - self._win_left
        cap_y = sy - self._win_top
        w, h = self._capture.get_capture_size()
        canvas = self._layout.get_canvas()
        canvas_px_w = canvas.w_ratio * w
        canvas_px_h = canvas.h_ratio * h
        rx = (cap_x - canvas.x_ratio * w) / canvas_px_w if canvas_px_w else 0.0
        ry = (cap_y - canvas.y_ratio * h) / canvas_px_h if canvas_px_h else 0.0
        return (
            round(max(0.0, min(1.0, rx)), 4),
            round(max(0.0, min(1.0, ry)), 4),
        )
