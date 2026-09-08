"""脚本工作台的调试面板 —— 截图画布取点/取色/取区域 + 运行/单步 + 变量/日志

挂在脚本编辑对话框右侧。职责分两半：

1. **画布**（PickCanvas）：从主窗口当前后端抓一帧，按活动布局的 canvas 裁过再显示，
   所以画布上取到的归一化坐标就是 DSL 直接能用的画布坐标。点=取点/取色、拖=取区域，
   三个「插入」按钮把 ``(x, y)`` / ``"#rrggbb"`` / ``(x, y, w, h)`` 写进编辑器光标处。

2. **运行**：把编辑器文本写到 ``workflows/_editor_run.wf``（与场景编辑器试运行同一个
   临时文件，import 相对路径因此可用），在 WorkflowWorker 线程里跑 WorkflowEngine。
   引擎的 ``statement_hook`` 每条语句前回传 (行号, 变量快照)，面板高亮当前行、刷新变量表；
   ``step_mode`` 让引擎在每条语句前停住等 pause_event——「单步」set 一次走一条，
   「继续」关掉 step_mode 再 set，「暂停」只打开 step_mode（下一条语句就停），
   「停止」置停止标志并 set 事件让阻塞中的引擎立刻醒来退出。

运行环境（截图/输入/OCR/布局/用户）全部从主窗口借，与日常页执行同源；
没定位窗口 / 没连设备时拒绝运行，只剩画布不可用、编辑与检查照常。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable

from loguru import logger
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.config.resolver import get_resolver
from ...i18n import tr
from ..button_styles import apply_button_style
from ..ocr.pick_canvas import PickCanvas

#: 临时运行文件（与场景编辑器试运行共用；``_`` 前缀不会被发现层当成正式脚本）
EDITOR_RUN_REL = "workflows/_editor_run.wf"


def format_value(value) -> str:
    """变量表里的值展示：与场景编辑器结果区一致的可读形式"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return repr(value) if isinstance(value, str) else str(value)
    if isinstance(value, (list, dict, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            return str(value)
    return f"<{type(value).__name__}> {value}"


def crop_to_canvas(img, canvas):
    """按布局 canvas（x/y/w/h 比例）裁截图；canvas 为 None 或裁空时返回原图"""
    if img is None or canvas is None:
        return img
    h, w = img.shape[:2]
    x1 = int(canvas.x_ratio * w)
    y1 = int(canvas.y_ratio * h)
    x2 = int((canvas.x_ratio + canvas.w_ratio) * w)
    y2 = int((canvas.y_ratio + canvas.h_ratio) * h)
    crop = img[y1:y2, x1:x2]
    return img if crop.size == 0 else crop


def snippet_point(x: float, y: float) -> str:
    return f"({x:.4f}, {y:.4f})"


def snippet_color(r: int, g: int, b: int) -> str:
    return f'"#{r:02x}{g:02x}{b:02x}"'


def snippet_rect(x: float, y: float, w: float, h: float) -> str:
    return f"({x:.4f}, {y:.4f}, {w:.4f}, {h:.4f})"


class _DebugBridge(QObject):
    """工作流线程 → UI 线程的信号桥"""
    stepped = pyqtSignal(int, object)     # (line_no, variables 快照)
    finished = pyqtSignal(object)         # result | Exception
    log_line = pyqtSignal(str)


class _LogSink:
    def __init__(self, bridge: _DebugBridge):
        self._bridge = bridge

    def write(self, message: str):
        self._bridge.log_line.emit(message.rstrip("\n"))


class DebugPanel(QWidget):
    """画布 + 运行控制 + 变量/日志

    editor 接口（由脚本编辑对话框提供）：
        insert_text(str)       光标处插入
        script_text() -> str   当前文本
        highlight_line(int|None)
        set_locked(bool)       运行期间编辑器只读
    """

    def __init__(self, main_window, editor, parent=None):
        super().__init__(parent)
        self._main = main_window
        self._editor = editor
        self._last_pick: tuple[float, float, int, int, int] | None = None
        self._last_rect: tuple[float, float, float, float] | None = None
        # 运行态
        self._worker = None
        self._engine = None
        self._stop_flag = False
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._bridge = _DebugBridge(self)
        self._bridge.stepped.connect(self._on_stepped)
        self._bridge.finished.connect(self._on_finished)
        self._bridge.log_line.connect(self._append_log)
        self._sink_id: int | None = None
        self._setup_ui()
        self._refresh_run_buttons()

    # ─── UI ────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        vsplit = QSplitter(Qt.Orientation.Vertical)

        # 画布区
        canvas_box = QWidget()
        cl = QVBoxLayout(canvas_box)
        cl.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.btn_snap = QPushButton(tr("刷新截图"))
        self.btn_snap.clicked.connect(self.refresh_screenshot)
        self.btn_ins_point = QPushButton(tr("插入坐标"))
        self.btn_ins_point.clicked.connect(self._insert_point)
        self.btn_ins_color = QPushButton(tr("插入颜色"))
        self.btn_ins_color.clicked.connect(self._insert_color)
        self.btn_ins_rect = QPushButton(tr("插入区域"))
        self.btn_ins_rect.clicked.connect(self._insert_rect)
        apply_button_style(
            self.btn_snap,
            self.btn_ins_point,
            self.btn_ins_color,
            self.btn_ins_rect,
            variant="neutral",
        )
        for b in (self.btn_snap, self.btn_ins_point, self.btn_ins_color, self.btn_ins_rect):
            row.addWidget(b)
        row.addStretch()
        cl.addLayout(row)
        self.canvas = PickCanvas()
        self.canvas.setMinimumSize(320, 200)
        self.canvas.hovered.connect(self._on_hover)
        self.canvas.picked.connect(self._on_pick)
        self.canvas.region_changed.connect(self._on_region)
        cl.addWidget(self.canvas, 1)
        self.lbl_readout = QLabel(tr("（未截图）"))
        self.lbl_readout.setStyleSheet(
            "font-family: Menlo, Consolas, monospace; color: palette(mid); padding: 2px;")
        cl.addWidget(self.lbl_readout)
        vsplit.addWidget(canvas_box)

        # 运行区
        run_box = QWidget()
        rl = QVBoxLayout(run_box)
        rl.setContentsMargins(0, 0, 0, 0)
        row2 = QHBoxLayout()
        self.btn_run = QPushButton(tr("运行"))
        self.btn_run.clicked.connect(lambda: self._start(step=False))
        self.btn_step = QPushButton(tr("单步"))
        self.btn_step.clicked.connect(self._on_step)
        self.btn_continue = QPushButton(tr("继续"))
        self.btn_continue.clicked.connect(self._on_continue)
        self.btn_pause = QPushButton(tr("暂停"))
        self.btn_pause.clicked.connect(self._on_pause)
        self.btn_stop = QPushButton(tr("停止"))
        self.btn_stop.clicked.connect(self._on_stop)
        apply_button_style(self.btn_run)
        apply_button_style(
            self.btn_step, self.btn_continue, self.btn_pause,
            variant="neutral",
        )
        apply_button_style(self.btn_stop, variant="danger")
        for b in (self.btn_run, self.btn_step, self.btn_continue, self.btn_pause, self.btn_stop):
            row2.addWidget(b)
        row2.addStretch()
        rl.addLayout(row2)
        self.lbl_run = QLabel(tr("就绪"))
        self.lbl_run.setStyleSheet("color: palette(mid); padding: 2px;")
        rl.addWidget(self.lbl_run)
        self.vars = QTableWidget(0, 2)
        self.vars.setHorizontalHeaderLabels([tr("变量"), tr("值")])
        self.vars.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.vars.verticalHeader().setVisible(False)
        self.vars.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        rl.addWidget(self.vars, 1)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setStyleSheet("font-family: Menlo, Consolas, monospace; font-size: 11px;")
        rl.addWidget(self.log, 1)
        vsplit.addWidget(run_box)
        root.addWidget(vsplit, 1)

    # ─── 画布 ──────────────────────────────────────────

    @property
    def last_pick(self):
        """最近一次取点 (x, y, r, g, b)；指令面板用"""
        return self._last_pick

    @property
    def last_rect(self):
        """最近一次框选 (x, y, w, h)；指令面板用"""
        return self._last_rect

    def refresh_screenshot(self) -> bool:
        main = self._main
        refresh = getattr(main, "_refresh_capture", None)
        if refresh is None:
            self.lbl_readout.setText(tr("无主窗口，无法截图"))
            return False
        img, err = refresh()
        if img is None:
            self.lbl_readout.setText(err or tr("截图失败"))
            return False
        canvas = None
        lm = getattr(main, "_layout_manager", None)
        if lm is not None:
            try:
                layout = lm.load_layout(lm.get_active_layout_name())
                canvas = layout.get_canvas() if layout else None
            except Exception as e:  # noqa: BLE001
                logger.warning(f"读取布局画布失败，按整帧显示: {e}")
        self.canvas.set_image(crop_to_canvas(img, canvas))
        self._last_rect = None
        self.lbl_readout.setText(tr("已截图 {w}×{h}（画布裁剪后）").format(
            w=self.canvas._img_w, h=self.canvas._img_h))
        self._refresh_insert_buttons()
        return True

    def _on_hover(self, x, y, r, g, b):
        text = f"x={x:.4f} y={y:.4f}  rgb=#{r:02x}{g:02x}{b:02x}"
        if self._last_pick:
            px, py, pr, pg, pb = self._last_pick
            text += f"   |  取点 ({px:.4f}, {py:.4f}) #{pr:02x}{pg:02x}{pb:02x}"
        if self._last_rect:
            text += "   |  区域 " + snippet_rect(*self._last_rect)
        self.lbl_readout.setText(text)

    def _on_pick(self, x, y, r, g, b):
        self._last_pick = (x, y, r, g, b)
        self._on_hover(x, y, r, g, b)
        self._refresh_insert_buttons()

    def _on_region(self, x, y, w, h):
        self._last_rect = (x, y, w, h)
        self.lbl_readout.setText(tr("区域 ") + snippet_rect(x, y, w, h))
        self._refresh_insert_buttons()

    def _refresh_insert_buttons(self):
        self.btn_ins_point.setEnabled(self._last_pick is not None)
        self.btn_ins_color.setEnabled(self._last_pick is not None)
        self.btn_ins_rect.setEnabled(self._last_rect is not None)

    def _insert_point(self):
        if self._last_pick:
            x, y, *_ = self._last_pick
            self._editor.insert_text(snippet_point(x, y))

    def _insert_color(self):
        if self._last_pick:
            _x, _y, r, g, b = self._last_pick
            self._editor.insert_text(snippet_color(r, g, b))

    def _insert_rect(self):
        if self._last_rect:
            self._editor.insert_text(snippet_rect(*self._last_rect))

    # ─── 运行 ──────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._worker is not None

    def _refresh_run_buttons(self):
        running = self.running
        self.btn_run.setEnabled(not running)
        self.btn_step.setEnabled(True)
        self.btn_continue.setEnabled(running)
        self.btn_pause.setEnabled(running)
        self.btn_stop.setEnabled(running)

    def _check_env(self) -> str | None:
        """返回错误说明；None = 可以跑"""
        main = self._main
        if main is None:
            return tr("无主窗口，无法获取运行环境")
        if getattr(main, "_running", False):
            return tr("主窗口有工作流在运行，先停止它")
        if getattr(main, "_backend", "windows") == "adb":
            if not getattr(main, "_device_ready", False):
                return tr("请先在主窗口连接设备")
        elif not getattr(main, "_target_window", None):
            return tr("请先在主窗口定位游戏窗口")
        return None

    def _build_engine(self):
        from ...workflows.engine import WorkflowEngine

        main = self._main
        lm = main._layout_manager
        layout = lm.load_layout(main.layout_combo.currentText())
        if not layout:
            raise RuntimeError(tr("无法加载当前布局"))
        if getattr(main, "_backend", "windows") == "adb":
            left = top = 0
        else:
            w = main._target_window
            left, top = w["left"], w["top"]
            inp = main._input
            if getattr(inp, "background_mode", False):
                inp.target_hwnd = w.get("hwnd")
        engine = WorkflowEngine(
            capture=main._capture, ocr=main._ocr, input_ctrl=main._input,
            layout=layout,
            input_sim=main._user_config.input_sim,
            delay_params=main._user_config.delay_params,
            android_apps=main._user_config.android_apps,
            android_device=getattr(main, "_device", None),
            run_env=main._selected_run_env(),
            window_left=left, window_top=top,
            stop_check=lambda: self._stop_flag,
            pause_event=self._pause_event,
        )
        username = main._user_manager.get_active_user_name()
        engine.session = main._session_manager.load(username)
        engine.run_username = username
        engine._save_callback = main._session_manager.save_fn(username, engine.session)
        make_cb = getattr(main, "_create_ui_callback", None)
        if make_cb is not None:
            engine._ui_callback = make_cb()
        engine.statement_hook = self._bridge.stepped.emit
        return engine

    def _start(self, step: bool) -> bool:
        if self.running:
            return False
        err = self._check_env()
        if err:
            self.lbl_run.setText(err)
            return False
        text = self._editor.script_text()
        if not text.strip():
            self.lbl_run.setText(tr("脚本为空"))
            return False
        from ..main.run_control import WorkflowWorker

        try:
            path: Path = get_resolver().write_entity(EDITOR_RUN_REL, text if text.endswith("\n") else text + "\n")
            engine = self._build_engine()
        except Exception as e:  # noqa: BLE001
            self.lbl_run.setText(f"{tr('启动失败')}: {e}")
            logger.error(f"脚本工作台启动失败: {e}")
            return False
        engine.step_mode = step
        self._engine = engine
        self._stop_flag = False
        self._pause_event.set()
        self.log.clear()
        self.vars.setRowCount(0)
        self._sink_id = logger.add(
            _LogSink(self._bridge), level="INFO", format="{time:HH:mm:ss} | {level:<5} | {message}")
        self._editor.set_locked(True)
        worker = WorkflowWorker("_editor_run", lambda: engine.execute(path))
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        self.lbl_run.setText(tr("单步模式：在第一条语句前停住") if step else tr("运行中…"))
        self._refresh_run_buttons()
        worker.start()
        return True

    def _on_worker_finished(self):
        """QThread 已真正退出后再向调试面板发布执行结果。"""
        worker = self._worker
        if worker is not None:
            self._bridge.finished.emit(worker.result_or_exception)

    def _on_step(self):
        if not self.running:
            self._start(step=True)
            return
        if self._engine is not None:
            self._engine.step_mode = True
        self._pause_event.set()
        self.lbl_run.setText(tr("单步…"))

    def _on_continue(self):
        if not self.running:
            return
        if self._engine is not None:
            self._engine.step_mode = False
        self._pause_event.set()
        self.lbl_run.setText(tr("运行中…"))

    def _on_pause(self):
        if not self.running or self._engine is None:
            return
        # 只打开 step_mode：引擎在下一条语句前自己 clear 并停住，不会切断正在执行的动作
        self._engine.step_mode = True
        self.lbl_run.setText(tr("将在下一条语句前暂停"))

    def _on_stop(self):
        if not self.running:
            return
        self._stop_flag = True
        self._pause_event.set()   # 唤醒阻塞在 _wait_if_paused 的引擎，让它看见停止标志
        self.lbl_run.setText(tr("停止中…"))

    def _on_stepped(self, line_no: int, variables: dict):
        self._editor.highlight_line(line_no if line_no > 0 else None)
        if self._engine is not None and self._engine.step_mode:
            self.lbl_run.setText(tr("停在第 {n} 行（单步 = 执行这一行）").format(n=line_no))
        self._fill_vars(variables)

    def _fill_vars(self, variables: dict):
        self.vars.setRowCount(len(variables))
        for row, (k, v) in enumerate(sorted(variables.items())):
            self.vars.setItem(row, 0, QTableWidgetItem(f"${k}"))
            self.vars.setItem(row, 1, QTableWidgetItem(format_value(v)))

    def _on_finished(self, result):
        engine = self._engine
        self._worker = None
        self._engine = None
        if self._sink_id is not None:
            try:
                logger.remove(self._sink_id)
            except ValueError:
                pass
            self._sink_id = None
        self._editor.set_locked(False)
        self._editor.highlight_line(None)
        if isinstance(result, BaseException):
            self.lbl_run.setText(f"{tr('异常退出')}: {result}")
        elif self._stop_flag:
            self.lbl_run.setText(tr("已停止"))
        else:
            rv = getattr(engine, "return_value", None)
            self.lbl_run.setText(tr("完成，返回值: {rv}").format(rv=format_value(rv)))
            if engine is not None:
                self._fill_vars(dict(engine.variables))
        self._refresh_run_buttons()

    def _append_log(self, line: str):
        self.log.appendPlainText(line)

    def shutdown(self):
        """对话框关闭前调用：有脚本在跑就停掉"""
        if self.running:
            self._on_stop()
            if self._worker is not None:
                self._worker.wait(3000)


__all__ = ["DebugPanel", "format_value", "crop_to_canvas", "snippet_point", "snippet_color", "snippet_rect",
           "EDITOR_RUN_REL"]

_ = Callable  # 保留给类型注释引用（editor 接口以鸭子类型描述）
