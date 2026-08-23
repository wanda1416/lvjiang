"""鼠标/键盘操作录制器 - 将用户操作翻译为 DSL 语句文本

捕获 pynput 鼠标 + 键盘事件，把落在目标窗口内的点击/拖拽/滚轮归一化为
画布比例坐标，按键翻译成本项目标准键名，生成 click / drag / scroll /
press / wait 语句行。产物即合法 .wf 脚本，可直接剪切复用，回放完全走
现有 DSL 引擎（engine._coord_ratio_to_screen 的逆运算 + normalize_key
同源的标准键名表）。
"""

import threading
import time

from loguru import logger

from ...core.desktop.win32_keyboard import KEY_NAME_TO_VK
from ...i18n import tr

try:
    from pynput import keyboard as pynput_keyboard
    from pynput import mouse as pynput_mouse
except Exception:  # pragma: no cover - 环境缺失 pynput 时降级
    pynput_keyboard = None
    pynput_mouse = None


# ─── 事件聚合阈值 ─────────────────────────────────────────
_DRAG_THRESHOLD_PX = 10      # 位移 >= 该值判定为拖拽，否则为点击
_WAIT_THRESHOLD_S = 0.3      # 两次操作间隔 > 该值时插入 wait
_MIN_DRAG_DURATION = 0.1     # 拖拽时长下限（秒）
_PRESS_HOLD_THRESHOLD_S = 0.5  # 按键时长 >= 该值判定为长按（press "X" hold N）

# F8/F9/F10 是主窗口全局热键，F12 是录制对话框的临时全局热键，
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

    坐标归一化到画布比例，与引擎 _coord_ratio_to_screen 同源，
    窗口缩放/移动后回放仍准确。鼠标操作仅录制落在目标窗口矩形内的部分；
    键盘录制是全局的（pynput 无法判断按键是发给哪个窗口），F8/F9/F10/F12
    这几个全局热键会被排除，其余按键一律录制。
    """

    def __init__(self, target_window: dict, capture, layout, win_left: int, win_top: int,
                 on_line=None):
        self._win = target_window
        self._capture = capture
        self._layout = layout
        self._win_left = win_left
        self._win_top = win_top
        self._on_line = on_line                # 每生成一行 DSL 的实时回调（监听线程内调用）

        self._listener = None
        self._kb_listener = None
        self._lock = threading.Lock()
        self._lines: list[str] = []          # 已生成的 DSL 行
        self._press_pos: tuple[int, int] | None = None   # 左键按下屏幕坐标
        self._press_time = 0.0               # 左键按下时刻
        self._key_press_times: dict = {}     # 已按下但未松开的按键 → 按下时刻
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
        self._press_pos = None
        self._key_press_times = {}
        self._last_action_time = None
        self._recording = True
        # 防护补丁：避免退出竞态下 pynput 钩子回调返回 None（幂等）
        from ...core.pynput_patch import install as _install_pynput_patch
        _install_pynput_patch()
        self._listener = pynput_mouse.Listener(
            on_click=self._on_click, on_scroll=self._on_scroll)
        self._listener.start()
        if pynput_keyboard is not None:
            self._kb_listener = pynput_keyboard.Listener(
                on_press=self._on_key_press, on_release=self._on_key_release)
            self._kb_listener.start()
        else:  # pragma: no cover - 环境缺失 pynput.keyboard 时降级为纯鼠标录制
            logger.warning("pynput.keyboard 不可用，本次录制不捕获按键")
        logger.info("宏录制已开始")

    def stop(self) -> str:
        """停止录制，返回生成的 DSL 文本"""
        if not self._recording:
            return "\n".join(self._lines)
        self._recording = False
        # 收尾 wait：反映"最后一个动作"到"按下 F12 停止录制"之间的空闲时长，
        # 否则空等一段时间后按 F12 结束，脚本末尾会直接紧跟着最后一条动作，
        # 完全体现不出录制时实际经过的等待
        with self._lock:
            self._maybe_emit_wait(time.monotonic())
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
        logger.info(f"宏录制已结束，共生成 {len(self._lines)} 条语句")
        return "\n".join(self._lines)

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
        """pynput 左键按下/松开回调"""
        if pynput_mouse is None or button != pynput_mouse.Button.left:
            return
        with self._lock:
            try:
                if not self._recording:
                    return
                if pressed:
                    self._press_pos = (int(sx), int(sy))
                    self._press_time = time.monotonic()
                else:
                    self._handle_release(int(sx), int(sy))
            except Exception:  # noqa: BLE001 - pynput 低层钩子回调里的异常可能被
                # 静默吞掉（Windows 要求钩子过程不能抛出/长时间阻塞，否则可能被
                # 判定为无响应并卸载），必须自己兜底记录，否则一旦出 bug 会
                # 表现成"什么都没发生"、日志里也看不到任何报错线索。
                logger.exception("_on_click 处理异常")

    def _handle_release(self, sx: int, sy: int):
        """左键松开：按位移判定 click / drag，并按需插入 wait"""
        if self._press_pos is None:
            return
        px, py = self._press_pos
        self._press_pos = None

        # 窗口过滤：按下点必须落在目标窗口矩形内
        if not self._in_window(px, py):
            logger.debug(f"忽略窗口外操作: ({px},{py})")
            return

        # 与上一次操作的间隔 → wait（用按下时刻衡量空闲时间）
        self._maybe_emit_wait(self._press_time)

        dx = sx - px
        dy = sy - py
        dist = (dx * dx + dy * dy) ** 0.5

        if dist < _DRAG_THRESHOLD_PX:
            rx, ry = self._screen_to_canvas_ratio(px, py)
            self._emit_line(f"click ({rx}, {ry})")
            logger.debug(f"录制点击: ({rx}, {ry})")
        else:
            rx1, ry1 = self._screen_to_canvas_ratio(px, py)
            rx2, ry2 = self._screen_to_canvas_ratio(sx, sy)
            duration = round(time.monotonic() - self._press_time, 2)
            if duration < _MIN_DRAG_DURATION:
                duration = _MIN_DRAG_DURATION
            self._emit_line(f"drag ({rx1}, {ry1}) ({rx2}, {ry2}) {duration}")
            logger.debug(f"录制拖拽: ({rx1}, {ry1}) -> ({rx2}, {ry2}) {duration}s")

        self._last_action_time = time.monotonic()

    def _maybe_emit_wait(self, event_time: float):
        """若距上次操作完成的间隔超过阈值，插入 wait 行"""
        if self._last_action_time is None:
            return
        gap = event_time - self._last_action_time
        if gap > _WAIT_THRESHOLD_S:
            self._emit_line(f"wait {round(gap, 1)}")
            logger.debug(f"录制等待: {round(gap, 1)}s")

    def _on_scroll(self, sx, sy, dx, dy):  # noqa: ARG002 - dx（水平滚动）暂不支持
        """pynput 鼠标滚轮回调：落在目标窗口内才录制，带坐标目标避免回放时
        落到画布中心（scroll 语句省略目标时默认在画布中心滚动，与录制时
        实际滚动的位置不是一回事）"""
        with self._lock:
            try:
                if not self._recording:
                    return
                isx, isy = int(sx), int(sy)
                if not self._in_window(isx, isy):
                    return
                if dy == 0:
                    return
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
        """pynput 键盘按下回调：只记录按下时刻，松开时才判定 tap/hold 并生成语句"""
        with self._lock:
            try:
                if not self._recording:
                    return
                if key not in self._key_press_times:
                    self._key_press_times[key] = time.monotonic()
            except Exception:  # noqa: BLE001 - 见 _on_click 里同样处理的注释
                logger.exception("_on_key_press 处理异常")

    def _on_key_release(self, key):
        """pynput 键盘松开回调：按住时长 < 阈值判定为一次完整按键（press "X"），
        否则判定为长按（press "X" hold N）；F8/F9/F10/F12 是录制器自身的
        全局热键，不录制，也不更新 _last_action_time（等待时间累积到下一
        条真正被记录的动作上，与鼠标点击落在窗口外时的处理方式一致）"""
        with self._lock:
            try:
                if not self._recording:
                    return
                press_time = self._key_press_times.pop(key, None)
                if press_time is None:
                    return

                name = _pynput_key_to_dsl_name(key)
                if name is None:
                    logger.debug(f"忽略无法识别的按键: {key!r}")
                    return
                if name in _RESERVED_KEYS:
                    return

                self._maybe_emit_wait(press_time)

                duration = time.monotonic() - press_time
                if duration >= _PRESS_HOLD_THRESHOLD_S:
                    self._emit_line(f'press "{name}" hold {round(duration, 1)}')
                    logger.debug(f"录制长按: {name} {round(duration, 1)}s")
                else:
                    self._emit_line(f'press "{name}"')
                    logger.debug(f"录制按键: {name}")

                self._last_action_time = time.monotonic()
            except Exception:  # noqa: BLE001 - 见 _on_click 里同样处理的注释
                logger.exception("_on_key_release 处理异常")

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
        return round(rx, 4), round(ry, 4)
