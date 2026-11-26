"""Development-only, process-local task diagnostics controls and live frame viewer."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QFontDatabase, QImage, QKeyEvent, QPixmap
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import constants
from ..core.task_diagnostics import DiagnosticFrame, DiagnosticOptions, TaskDiagnostics
from ..core.task_diagnostics_dump import (
    DiagnosticFrameDumper,
    frame_identity,
    parse_frame_metadata,
)
from ..i18n import tr
from .widgets.button_styles import (
    apply_compact_button_style,
    apply_dialog_button_box_style,
)


def _decode_webp_pixmap(webp: bytes) -> QPixmap | None:
    """用 OpenCV 解码 WebP 为 QPixmap，避免依赖 Qt 发行包中的 qwebp 插件。"""
    import cv2
    import numpy as np

    buf = np.frombuffer(webp, dtype=np.uint8)
    bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if bgr is None or bgr.ndim != 3 or bgr.shape[2] < 3:
        return None
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    image = QImage(rgb.tobytes(), int(w), int(h), int(ch * w), QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(image)


def _format_captured_time(captured_ns: Any) -> str:
    try:
        ns = int(captured_ns)
    except (TypeError, ValueError):
        return "-"
    if ns <= 0:
        return "-"
    # captured_ns 可能是单调时钟或时间戳；大于 2001 年按墙钟格式化，否则展示秒数
    if ns > 1_000_000_000_000_000_000:
        return datetime.fromtimestamp(ns / 1e9).strftime("%H:%M:%S.%f")[:-3]
    return f"{ns / 1e9:.3f}s"


class TaskDiagnosticsDialog(QDialog):
    def __init__(
        self,
        diagnostics: TaskDiagnostics,
        parent: QWidget | None = None,
        *,
        dump_base_dir: Path | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("任务诊断"))
        self.resize(960, 680)
        self.setMinimumSize(760, 520)
        self._diagnostics = diagnostics
        self._dumper = DiagnosticFrameDumper(base_dir=dump_base_dir)
        self._last_dump_dir: Path | None = None

        # 临时区状态：正在查看的帧保持不可变引用，即使底层 FIFO 淘汰或切新任务也不被清除
        self._follow_latest = True
        self._current_frames: tuple[DiagnosticFrame, ...] = ()
        self._current_scope = ""
        self._pinned_frame: DiagnosticFrame | None = None
        self._pinned_meta: dict[str, Any] = {}
        self._pinned_key: tuple[str, int] | None = None
        self._pinned_pixmap: QPixmap | None = None

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        debug = QWidget()
        debug_layout = QVBoxLayout(debug)
        debug_layout.setSpacing(8)

        # ── 顶部开发预览说明 ──
        preview = QLabel(
            tr(
                "开发预览：默认关闭，重启程序后恢复关闭。修改仅对下次启动的任务或批次生效；不改变正在运行的任务。"
            )
        )
        preview.setWordWrap(True)
        debug_layout.addWidget(preview)

        # ── 配置参数行（独立一行，避免窄窗与长译文挤压状态摘要）──
        controls_row = QHBoxLayout()
        controls_row.setSpacing(8)
        self.enabled_check = QCheckBox(tr("启用开发诊断模式"))
        self.enabled_check.setChecked(diagnostics.options.enabled)
        controls_row.addWidget(self.enabled_check)

        controls_row.addSpacing(8)
        controls_row.addWidget(QLabel(tr("最近帧上限")))
        self.frames_spin = QSpinBox()
        self.frames_spin.setRange(1, 10_000)
        self.frames_spin.setValue(diagnostics.options.max_frames)
        self.frames_spin.setSuffix(" " + tr("帧"))
        controls_row.addWidget(self.frames_spin)

        controls_row.addSpacing(8)
        controls_row.addWidget(QLabel(tr("压缩缓存上限")))
        self.capacity_spin = QSpinBox()
        self.capacity_spin.setRange(1, 1_000)
        self.capacity_spin.setValue(max(1, diagnostics.options.max_bytes // 1_000_000))
        self.capacity_spin.setSuffix(" MB")
        controls_row.addWidget(self.capacity_spin)
        controls_row.addStretch(1)
        debug_layout.addLayout(controls_row)

        # ── 落盘控制与运行摘要行 ──
        dump_row = QHBoxLayout()
        dump_row.setSpacing(8)
        self.dump_dot_label = QLabel("●")
        self.dump_dot_label.setStyleSheet("color: #888; font-size: 12px;")
        dump_row.addWidget(self.dump_dot_label)

        self.btn_dump_toggle = QPushButton(tr("开始落盘"))
        self.btn_dump_toggle.setCheckable(True)
        self.btn_dump_toggle.setToolTip(
            tr("开启后将当前缓存帧与后续新帧异步写入 logs/diagnostics/ 下的独立新目录")
        )
        apply_compact_button_style(self.btn_dump_toggle, variant="action")
        dump_row.addWidget(self.btn_dump_toggle)

        self.dump_status_label = QLabel(tr("未开启落盘（仅内存缓存，退出清空）"))
        self.dump_status_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        dump_row.addWidget(self.dump_status_label, 1)

        self.btn_clear_cache = QPushButton(tr("清空缓存"))
        self.btn_clear_cache.setEnabled(False)
        self.btn_clear_cache.setToolTip(
            tr("清空当前内存缓存帧与临时区预览；后续开始落盘仅写入新采集的帧")
        )
        apply_compact_button_style(self.btn_clear_cache, variant="neutral")
        dump_row.addWidget(self.btn_clear_cache)

        self.btn_open_dump_dir = QPushButton(tr("打开目录"))
        self.btn_open_dump_dir.setEnabled(False)
        apply_compact_button_style(self.btn_open_dump_dir, variant="neutral")
        dump_row.addWidget(self.btn_open_dump_dir)
        debug_layout.addLayout(dump_row)

        # ── 运行摘要与当前帧元信息 ──
        self.summary_label = QLabel(tr("暂无诊断帧记录"))
        debug_layout.addWidget(self.summary_label)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        self.meta_label = QLabel(tr("启用开发诊断模式并运行任务后，此处将实时展示识别用帧。"))
        self.meta_label.setWordWrap(True)
        self.meta_label.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        meta_row.addWidget(self.meta_label, 1)

        self.btn_return_latest = QPushButton(tr("回到最新"))
        apply_compact_button_style(self.btn_return_latest, variant="action")
        self.btn_return_latest.setVisible(False)
        meta_row.addWidget(self.btn_return_latest)
        debug_layout.addLayout(meta_row)

        # ── 大图预览区 ──
        self.preview_label = QLabel(tr("暂无画面"))
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setFrameShape(QFrame.Shape.StyledPanel)
        self.preview_label.setStyleSheet(
            "QLabel { background-color: #1e1e1e; color: #bdbdbd; border-radius: 4px; }"
        )
        self.preview_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        self.preview_label.setMinimumSize(320, 200)
        debug_layout.addWidget(self.preview_label, 1)

        # ── 底部轮播控制条 ──
        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)
        self.btn_first = QPushButton("|<")
        self.btn_first.setToolTip(tr("最早一帧 (Home)"))
        self.btn_prev = QPushButton("<")
        self.btn_prev.setToolTip(tr("上一帧 (←)"))
        self.frame_slider = QSlider(Qt.Orientation.Horizontal)
        self.frame_slider.setRange(0, 0)
        self.frame_slider.setEnabled(False)
        self.btn_next = QPushButton(">")
        self.btn_next.setToolTip(tr("下一帧 (→)"))
        self.btn_last = QPushButton(">|")
        self.btn_last.setToolTip(tr("最新一帧 (End)"))
        apply_compact_button_style(
            self.btn_first,
            self.btn_prev,
            self.btn_next,
            self.btn_last,
            variant="neutral",
        )
        for btn in (self.btn_first, self.btn_prev, self.btn_next, self.btn_last):
            btn.setMinimumWidth(34)

        self.follow_check = QCheckBox(tr("跟踪最新"))
        self.follow_check.setChecked(True)
        self.index_label = QLabel("0 / 0")
        self.index_label.setMinimumWidth(64)
        self.index_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        for widget in (
            self.btn_first,
            self.btn_prev,
            self.frame_slider,
            self.btn_next,
            self.btn_last,
            self.follow_check,
            self.index_label,
        ):
            nav_row.addWidget(widget)
        debug_layout.addLayout(nav_row)

        self.tabs.addTab(debug, tr("诊断调试"))
        self.tabs.addTab(
            QWidget(), tr("模拟运行")
        )  # Deliberately blank until a real GUI exists.

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        apply_dialog_button_box_style(buttons)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

        # ── 信号绑定、键盘导航拦截与定时刷新 ──
        self._sync_limits_enabled()
        self.enabled_check.toggled.connect(self._changed)
        self.frames_spin.valueChanged.connect(self._changed)
        self.capacity_spin.valueChanged.connect(self._changed)
        self.btn_dump_toggle.toggled.connect(self._on_dump_toggled)
        self.btn_clear_cache.clicked.connect(self._clear_cache)
        self.btn_open_dump_dir.clicked.connect(self._open_dump_dir)
        self.btn_return_latest.clicked.connect(self._jump_last)
        self.btn_first.clicked.connect(self._jump_first)
        self.btn_prev.clicked.connect(self._step_prev)
        self.btn_next.clicked.connect(self._step_next)
        self.btn_last.clicked.connect(self._jump_last)
        self.frame_slider.valueChanged.connect(self._on_slider_changed)
        self.follow_check.toggled.connect(self._on_follow_toggled)

        self._install_keyboard_nav_filter()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(250)
        self._poll_timer.timeout.connect(self.refresh_frames)
        self.refresh_frames()

    def _install_keyboard_nav_filter(self) -> None:
        """在非输入子控件上安装事件过滤器，防止 TabBar/按钮/滑块吞掉方向键切帧。"""
        for child in self.findChildren(QWidget):
            if isinstance(child, (QAbstractSpinBox, QLineEdit)):
                continue
            child.installEventFilter(self)

    # ─── 配置同步 ─────────────────────────────────────────

    def _sync_limits_enabled(self) -> None:
        enabled = self.enabled_check.isChecked()
        self.frames_spin.setEnabled(enabled)
        self.capacity_spin.setEnabled(enabled)
        has_frames = bool(self._current_frames) or self._pinned_frame is not None
        self.btn_dump_toggle.setEnabled(enabled or has_frames or self._dumper.active)
        self.btn_clear_cache.setEnabled(has_frames)

    def _sync_dump_button_style(self, active: bool) -> None:
        if active:
            self.btn_dump_toggle.setText(tr("停止落盘"))
            apply_compact_button_style(self.btn_dump_toggle, variant="danger")
            self.dump_dot_label.setStyleSheet("color: #d93025; font-size: 12px;")
        else:
            self.btn_dump_toggle.setText(tr("开始落盘"))
            apply_compact_button_style(self.btn_dump_toggle, variant="action")
            self.dump_dot_label.setStyleSheet("color: #888; font-size: 12px;")

    def _changed(self, *_args) -> None:
        # 只在用户真的改动时回写：打开对话框不应把取整后的上限覆盖回去。
        self._sync_limits_enabled()
        self._diagnostics.options = DiagnosticOptions(
            self.enabled_check.isChecked(),
            self.frames_spin.value(),
            self.capacity_spin.value() * 1_000_000,
        )

    # ─── 落盘控制 ─────────────────────────────────────────

    def _format_dump_dir(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(constants.PROJECT_ROOT.resolve()).as_posix()
        except ValueError:
            return str(path)

    def _on_dump_toggled(self, checked: bool) -> None:
        if checked:
            try:
                session_dir = self._dumper.start()
            except OSError as exc:
                self.btn_dump_toggle.blockSignals(True)
                self.btn_dump_toggle.setChecked(False)
                self.btn_dump_toggle.blockSignals(False)
                self._sync_dump_button_style(False)
                self.dump_status_label.setText(
                    tr("创建落盘目录失败：{error}").format(error=exc)
                )
                self.dump_status_label.setToolTip(self.dump_status_label.text())
                return
            self._last_dump_dir = session_dir
            self.btn_open_dump_dir.setEnabled(True)
            self._sync_dump_button_style(True)
            # 先把临时区正在查看的图（可能已被 FIFO 淘汰）和当前缓存全部入队落盘
            initial_frames: list[DiagnosticFrame] = []
            if self._pinned_frame is not None:
                initial_frames.append(self._pinned_frame)
            history = self._diagnostics.latest
            if history is not None:
                initial_frames.extend(history.frames())
            self._diagnostics.set_frame_listener(self._dumper.enqueue_frame)
            self._dumper.enqueue_many(initial_frames)
            self._sync_limits_enabled()
            self._refresh_dump_status()
            return
        self._diagnostics.set_frame_listener(None)
        self._dumper.stop()
        self._sync_dump_button_style(False)
        self._sync_limits_enabled()
        self._refresh_dump_status()

    def _refresh_dump_status(self) -> None:
        session_dir = self._dumper.session_dir or self._last_dump_dir
        if self._dumper.active and session_dir is not None:
            rel = self._format_dump_dir(session_dir)
            text = tr("落盘中（已写入 {count} 帧）→ {path}").format(
                count=self._dumper.written_count, path=rel
            )
            if self._dumper.error_count:
                text += tr(" | 失败 {err}").format(err=self._dumper.error_count)
            self.dump_status_label.setText(text)
            self.dump_status_label.setToolTip(str(session_dir))
        elif session_dir is not None:
            rel = self._format_dump_dir(session_dir)
            self.dump_status_label.setText(
                tr("已停止落盘（共写入 {count} 帧）→ {path}").format(
                    count=self._dumper.written_count, path=rel
                )
            )
            self.dump_status_label.setToolTip(str(session_dir))
        else:
            self.dump_status_label.setText(tr("未开启落盘（仅内存缓存，退出清空）"))
            self.dump_status_label.setToolTip("")

    def _clear_cache(self) -> None:
        """清空内存缓存帧与临时区预览，避免后续开始落盘带入历史帧。"""
        self._diagnostics.clear_cache()
        self._current_frames = ()
        self._pinned_frame = None
        self._pinned_meta = {}
        self._pinned_key = None
        self._pinned_pixmap = None
        self._set_follow_latest(True)
        self._render_pixmap()
        self._render_pinned_metadata()
        self.refresh_frames()

    def _open_dump_dir(self) -> None:
        target = self._dumper.session_dir or self._last_dump_dir
        if target is not None and target.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    # ─── 轮播与临时区刷新 ─────────────────────────────────

    def refresh_frames(self) -> None:
        """拉取最新帧列表；跟踪模式下切到最新帧，查看旧帧时由临时区保活不被清除。"""
        history = self._diagnostics.latest
        if history is None:
            self._current_frames = ()
            self._current_scope = ""
            self.summary_label.setText(tr("暂无诊断帧记录"))
            self._sync_limits_enabled()
            self._sync_nav_controls()
            self._refresh_dump_status()
            return

        snap = history.snapshot()
        frames = history.frames()
        self._current_frames = frames
        self._current_scope = str(snap.get("capture_scope") or "")

        if self._dumper.active and frames:
            self._dumper.enqueue_many(frames)

        dropped_total = sum(int(v) for v in (snap.get("dropped") or {}).values())
        retained_mb = float(snap.get("retained_bytes") or 0) / 1_000_000.0
        state_map = {
            "recording": tr("录制中"),
            "draining": tr("收尾中"),
            "closed": tr("已封口"),
            "incomplete": tr("不完整"),
        }
        state_label = state_map.get(str(snap.get("state") or ""), str(snap.get("state") or ""))
        self.summary_label.setText(
            tr("{state} | {count}/{limit} 帧 ({mb:.1f} MB) | 淘汰 {evicted} | 丢帧 {dropped}").format(
                state=state_label,
                count=len(frames),
                limit=int(snap.get("max_frames") or 0),
                mb=retained_mb,
                evicted=int(snap.get("evicted") or 0),
                dropped=dropped_total,
            )
        )

        if self._follow_latest and frames:
            self._pin_frame(frames[-1])
        elif self._pinned_frame is None and frames:
            self._pin_frame(frames[-1])
        else:
            self._render_pinned_metadata()

        self._sync_limits_enabled()
        self._sync_nav_controls()
        self._refresh_dump_status()

    def _find_pinned_index(self) -> int | None:
        if self._pinned_key is None or not self._current_frames:
            return None
        for idx, frame in enumerate(self._current_frames):
            if frame_identity(frame) == self._pinned_key:
                return idx
        return None

    def _pin_frame(self, frame: DiagnosticFrame) -> None:
        meta = parse_frame_metadata(frame)
        key = frame_identity(frame, meta)
        changed = key != self._pinned_key or self._pinned_frame is None
        self._pinned_frame = frame
        self._pinned_meta = meta
        self._pinned_key = key
        if changed:
            self._pinned_pixmap = _decode_webp_pixmap(frame.webp)
            self._render_pixmap()
        self._render_pinned_metadata()

    def _render_pixmap(self) -> None:
        if self._pinned_pixmap is None or self._pinned_pixmap.isNull():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText(
                tr("画面解码失败") if self._pinned_frame is not None else tr("暂无画面")
            )
            return
        avail = self.preview_label.contentsRect().size()
        if avail.width() <= 1 or avail.height() <= 1:
            self.preview_label.setPixmap(self._pinned_pixmap)
            return
        scaled = self._pinned_pixmap.scaled(
            avail,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setText("")
        self.preview_label.setPixmap(scaled)

    def _render_pinned_metadata(self) -> None:
        if self._pinned_frame is None:
            self.meta_label.setText(
                tr("启用开发诊断模式并运行任务后，此处将实时展示识别用帧。")
            )
            return
        meta = self._pinned_meta
        seq = meta.get("frame_seq", "-")
        source = str(meta.get("source") or "-")
        shape = meta.get("shape") or []
        size_str = f"{shape[1]}x{shape[0]}" if len(shape) >= 2 else "-"
        ts = _format_captured_time(meta.get("captured_ns"))
        kb = len(self._pinned_frame.webp) / 1024.0
        parts = [
            f"#{seq}",
            f"source={source}",
            size_str,
            f"{kb:.1f} KB",
            ts,
        ]
        if self._find_pinned_index() is None:
            pinned_scope = str(meta.get("capture_scope") or "")
            if self._current_scope and pinned_scope and pinned_scope != self._current_scope:
                parts.append(tr("【临时区保留 · 来自上一任务】"))
            else:
                parts.append(tr("【临时区保留 · 原缓存已淘汰】"))
        self.meta_label.setText("  |  ".join(parts))

    def _sync_nav_controls(self) -> None:
        total = len(self._current_frames)
        idx = self._find_pinned_index()
        self.frame_slider.blockSignals(True)
        if total > 0:
            self.frame_slider.setRange(0, total - 1)
            self.frame_slider.setEnabled(True)
            self.frame_slider.setValue(idx if idx is not None else 0)
        else:
            self.frame_slider.setRange(0, 0)
            self.frame_slider.setValue(0)
            self.frame_slider.setEnabled(False)
        self.frame_slider.blockSignals(False)

        in_temp_slot = idx is None and self._pinned_frame is not None
        self.btn_first.setEnabled(total > 0 and idx != 0)
        self.btn_prev.setEnabled(total > 0 and idx is not None and idx > 0)
        self.btn_next.setEnabled(total > 0 and (idx is None or idx < total - 1))
        self.btn_last.setEnabled(total > 0 and idx != total - 1)
        self.btn_return_latest.setVisible(in_temp_slot and total > 0)

        if idx is not None:
            self.index_label.setText(f"{idx + 1} / {total}")
        elif in_temp_slot:
            self.index_label.setText(tr("临时 / {total}").format(total=total))
        else:
            self.index_label.setText(f"0 / {total}")

    def _set_follow_latest(self, follow: bool) -> None:
        self._follow_latest = follow
        if self.follow_check.isChecked() != follow:
            self.follow_check.blockSignals(True)
            self.follow_check.setChecked(follow)
            self.follow_check.blockSignals(False)

    def _select_index(self, index: int, *, follow: bool = False) -> None:
        if not self._current_frames:
            return
        clamped = max(0, min(index, len(self._current_frames) - 1))
        self._set_follow_latest(follow)
        self._pin_frame(self._current_frames[clamped])
        self._sync_nav_controls()

    def _on_slider_changed(self, value: int) -> None:
        if not self._current_frames:
            return
        self._select_index(value, follow=False)

    def _on_follow_toggled(self, checked: bool) -> None:
        self._follow_latest = checked
        if checked and self._current_frames:
            self._pin_frame(self._current_frames[-1])
            self._sync_nav_controls()

    def _jump_first(self) -> None:
        if self._current_frames:
            self._select_index(0, follow=False)

    def _jump_last(self) -> None:
        if self._current_frames:
            self._select_index(len(self._current_frames) - 1, follow=True)

    def _step_prev(self) -> None:
        if not self._current_frames:
            return
        idx = self._find_pinned_index()
        if idx is None or idx <= 0:
            return
        self._select_index(idx - 1, follow=False)

    def _step_next(self) -> None:
        if not self._current_frames:
            return
        idx = self._find_pinned_index()
        target = 0 if idx is None else min(len(self._current_frames) - 1, idx + 1)
        self._select_index(target, follow=False)

    # ─── 窗口事件与清理 ───────────────────────────────────

    def _handle_nav_key(self, key: int) -> bool:
        """在诊断调试页且焦点不在数字输入框时响应方向键/Home/End 切帧。"""
        if self.tabs.currentIndex() != 0:
            return False
        if isinstance(self.focusWidget(), (QAbstractSpinBox, QLineEdit)):
            return False
        if key == Qt.Key.Key_Left:
            self._step_prev()
            return True
        if key == Qt.Key.Key_Right:
            self._step_next()
            return True
        if key == Qt.Key.Key_Home:
            self._jump_first()
            return True
        if key == Qt.Key.Key_End:
            self._jump_last()
            return True
        return False

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:  # noqa: N802 - Qt
        if (
            event is not None
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and self._handle_nav_key(event.key())
        ):
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802 - Qt
        if event is None:
            return
        if self._handle_nav_key(event.key()):
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt
        super().resizeEvent(event)
        if self._pinned_pixmap is not None:
            self._render_pixmap()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt
        super().showEvent(event)
        self.refresh_frames()
        if not self._poll_timer.isActive():
            self._poll_timer.start()

    def hideEvent(self, event) -> None:
        self._poll_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt
        self._poll_timer.stop()
        self._diagnostics.set_frame_listener(None)
        if self._dumper.active:
            self._dumper.stop()
            self.btn_dump_toggle.blockSignals(True)
            self.btn_dump_toggle.setChecked(False)
            self.btn_dump_toggle.blockSignals(False)
            self._sync_dump_button_style(False)
        self._pinned_frame = None
        self._pinned_pixmap = None
        self._pinned_key = None
        self._pinned_meta = {}
        super().closeEvent(event)
