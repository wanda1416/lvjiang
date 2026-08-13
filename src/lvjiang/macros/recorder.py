"""鼠标操作录制器 - 将鼠标操作翻译为 DSL 语句文本

捕获 pynput 鼠标事件，把落在目标窗口内的点击/拖拽归一化为画布比例坐标，
翻译成 click / drag / wait 语句行。产物即合法 .wf 脚本，可直接剪切复用，
回放完全走现有 DSL 引擎（engine._coord_ratio_to_screen 的逆运算）。
"""

import threading
import time

from loguru import logger

from ..i18n import tr

try:
    from pynput import mouse as pynput_mouse
except Exception:  # pragma: no cover - 环境缺失 pynput 时降级
    pynput_mouse = None


# ─── 事件聚合阈值 ─────────────────────────────────────────
_DRAG_THRESHOLD_PX = 10      # 位移 >= 该值判定为拖拽，否则为点击
_WAIT_THRESHOLD_S = 0.3      # 两次操作间隔 > 该值时插入 wait
_MIN_DRAG_DURATION = 0.1     # 拖拽时长下限（秒）


class MacroRecorder:
    """鼠标操作录制器 → 生成 DSL 语句文本

    坐标归一化到画布比例，与引擎 _coord_ratio_to_screen 同源，
    窗口缩放/移动后回放仍准确。仅录制落在目标窗口矩形内的操作。
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
        self._lock = threading.Lock()
        self._lines: list[str] = []          # 已生成的 DSL 行
        self._press_pos: tuple[int, int] | None = None   # 左键按下屏幕坐标
        self._press_time = 0.0               # 左键按下时刻
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
        self._last_action_time = None
        self._recording = True
        # 防护补丁：避免退出竞态下 pynput 钩子回调返回 None（幂等）
        from ..core.pynput_patch import install as _install_pynput_patch
        _install_pynput_patch()
        self._listener = pynput_mouse.Listener(on_click=self._on_click)
        self._listener.start()
        logger.info("宏录制已开始")

    def stop(self) -> str:
        """停止录制，返回生成的 DSL 文本"""
        if not self._recording:
            return "\n".join(self._lines)
        self._recording = False
        if self._listener is not None:
            try:
                # stop() 只投递停止信号，必须 join 等监听线程卸载钩子，
                # 否则残留钩子回调在退出时踩空
                self._listener.stop()
                self._listener.join(1.0)
            except Exception:
                pass
            self._listener = None
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
            if not self._recording:
                return
            if pressed:
                self._press_pos = (int(sx), int(sy))
                self._press_time = time.monotonic()
            else:
                self._handle_release(int(sx), int(sy))

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
