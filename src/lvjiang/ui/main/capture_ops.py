"""采集控制混入类 - 预览区右侧面板：录屏/暂停/截屏/保存/放弃

依赖主类提供:
    _capture, _scrcpy_streaming, _device_ready, _target_window,
    _grab_capture_image, log_text, statusBar()

录制状态机:
    idle ──录屏──► recording ◄─暂停/继续─► paused
                      │停止(recording/paused 均可)
                      ▼
                  pending(待保存) ──保存──► .part 转正 → idle
                      └──────────放弃──► 删除 .part → idle
"""

from datetime import datetime

from loguru import logger
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ...constants import PICTURE_DIR, VIDEO_DIR
from ...i18n import tr


def _cap_btn_style(bg: str) -> str:
    """面板按钮统一样式（含禁用态），与主窗口按钮风格一致"""
    return (
        f"QPushButton {{ background-color: {bg}; color: white; font-weight: bold; "
        f"padding: 6px; border: none; border-radius: 4px; }} "
        "QPushButton:disabled { background-color: #444; color: #888; }"
    )


def _cap_divider() -> QFrame:
    """面板内水平分隔线"""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: #555; background-color: #555; max-height: 1px;")
    return line


# 各状态按钮配色
_STYLE_RECORD_IDLE = _cap_btn_style("#607D8B")   # 蓝灰（与脚本录制按钮一致）
_STYLE_RECORD_STOP = _cap_btn_style("#d32f2f")   # 红色（录制中 → 停止）
_STYLE_PAUSE = _cap_btn_style("#455A64")
_STYLE_SNAP = _cap_btn_style("#2196F3")
_STYLE_SAVE = _cap_btn_style("#4CAF50")          # 绿色强调
_STYLE_DISCARD = _cap_btn_style("#757575")


class CaptureOpsMixin:
    """录屏/截屏采集面板混入类"""

    # 录制态的类级兜底：面板构建（_build_capture_panel）前被读也有明确默认值
    _screen_recorder = None
    _rec_state = "idle"
    _rec_timer = None

    # ─── 面板构建 ─────────────────────────────────────────

    def _build_capture_panel(self) -> QFrame:
        """构建预览区右侧采集控制面板（状态区/录制组/截屏/处置组）"""
        self._screen_recorder = None
        self._rec_state = "idle"
        self._rec_timer = QTimer(self)  # type: ignore[arg-type]
        self._rec_timer.setInterval(500)
        self._rec_timer.timeout.connect(self._refresh_rec_time)

        panel = QFrame()
        panel.setFixedWidth(150)
        panel.setStyleSheet("QFrame { background-color: #333; border-radius: 6px; }")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # 状态区：红点 + 时长 + 状态文字
        status_row = QHBoxLayout()
        self.lbl_rec_dot = QLabel("●")
        self.lbl_rec_dot.setStyleSheet("color: #666; font-size: 14px;")
        status_row.addWidget(self.lbl_rec_dot)
        self.lbl_rec_time = QLabel("00:00")
        self.lbl_rec_time.setStyleSheet(
            "color: #ddd; font-family: Consolas, monospace; font-size: 14px;"
        )
        status_row.addWidget(self.lbl_rec_time)
        status_row.addStretch()
        self.lbl_rec_state = QLabel(tr("待机"))
        self.lbl_rec_state.setStyleSheet("color: #888; font-size: 11px;")
        status_row.addWidget(self.lbl_rec_state)
        lay.addLayout(status_row)

        # 录制控制组（注意：btn_record/btn_pause 已被宏录制等占用，这里用独立命名）
        self.btn_rec_screen = QPushButton(tr("● 录屏"))
        self.btn_rec_screen.setStyleSheet(_STYLE_RECORD_IDLE)
        self.btn_rec_screen.clicked.connect(self._on_toggle_record)
        lay.addWidget(self.btn_rec_screen)

        self.btn_rec_pause = QPushButton(tr("⏸ 暂停"))
        self.btn_rec_pause.setStyleSheet(_STYLE_PAUSE)
        self.btn_rec_pause.clicked.connect(self._on_pause_record)
        lay.addWidget(self.btn_rec_pause)

        lay.addWidget(_cap_divider())

        # 截屏（独立于录屏状态机，点击立即保存）
        self.btn_snap = QPushButton(tr("截屏"))
        self.btn_snap.setStyleSheet(_STYLE_SNAP)
        self.btn_snap.clicked.connect(self._on_snap)
        lay.addWidget(self.btn_snap)

        lay.addWidget(_cap_divider())

        # 处置组：保存 | 放弃（仅待保存状态可用）
        disposal_row = QHBoxLayout()
        disposal_row.setSpacing(6)
        self.btn_save_rec = QPushButton(tr("保存"))
        self.btn_save_rec.setStyleSheet(_STYLE_SAVE)
        self.btn_save_rec.clicked.connect(self._on_save_recording)
        disposal_row.addWidget(self.btn_save_rec)
        self.btn_discard_rec = QPushButton(tr("放弃"))
        self.btn_discard_rec.setStyleSheet(_STYLE_DISCARD)
        self.btn_discard_rec.clicked.connect(self._on_discard_recording)
        disposal_row.addWidget(self.btn_discard_rec)
        lay.addLayout(disposal_row)

        lay.addStretch()
        self._apply_rec_state()
        return panel

    # ─── 状态机与按钮可用性 ───────────────────────────────

    def _apply_rec_state(self):
        """根据录制状态 + 连接态统一刷新面板控件（唯一 UI 更新入口）"""
        state = self._rec_state
        connected = (
            self._device_ready
            or self._target_window is not None
        )
        streaming = bool(self._scrcpy_streaming)

        if state == "idle":
            self.lbl_rec_dot.setStyleSheet("color: #666; font-size: 14px;")
            self.lbl_rec_state.setText(tr("待机"))
            self.btn_rec_screen.setText(tr("● 录屏"))
            self.btn_rec_screen.setStyleSheet(_STYLE_RECORD_IDLE)
            self.btn_rec_screen.setEnabled(connected and streaming)
            self.btn_rec_screen.setToolTip("" if streaming else tr("仅流式截图模式支持录屏"))
            self.btn_rec_pause.setText(tr("⏸ 暂停"))
            self.btn_rec_pause.setEnabled(False)
            self.btn_save_rec.setEnabled(False)
            self.btn_discard_rec.setEnabled(False)
        elif state == "recording":
            self.lbl_rec_dot.setStyleSheet("color: #f44336; font-size: 14px;")
            self.lbl_rec_state.setText(tr("录制中"))
            self.btn_rec_screen.setText(tr("■ 停止"))
            self.btn_rec_screen.setStyleSheet(_STYLE_RECORD_STOP)
            self.btn_rec_screen.setEnabled(True)
            self.btn_rec_pause.setText(tr("⏸ 暂停"))
            self.btn_rec_pause.setEnabled(True)
            self.btn_save_rec.setEnabled(False)
            self.btn_discard_rec.setEnabled(False)
        elif state == "paused":
            self.lbl_rec_dot.setStyleSheet("color: #FFA726; font-size: 14px;")
            self.lbl_rec_state.setText(tr("已暂停"))
            self.btn_rec_screen.setText(tr("■ 停止"))
            self.btn_rec_screen.setStyleSheet(_STYLE_RECORD_STOP)
            self.btn_rec_screen.setEnabled(True)
            self.btn_rec_pause.setText(tr("▶ 继续"))
            self.btn_rec_pause.setEnabled(True)
            self.btn_save_rec.setEnabled(False)
            self.btn_discard_rec.setEnabled(False)
        elif state == "pending":
            self.lbl_rec_dot.setStyleSheet("color: #FFD54F; font-size: 14px;")
            self.lbl_rec_state.setText(tr("待保存"))
            self.btn_rec_screen.setText(tr("● 录屏"))
            self.btn_rec_screen.setStyleSheet(_STYLE_RECORD_IDLE)
            self.btn_rec_screen.setEnabled(False)
            self.btn_rec_pause.setText(tr("⏸ 暂停"))
            self.btn_rec_pause.setEnabled(False)
            self.btn_save_rec.setEnabled(True)
            self.btn_discard_rec.setEnabled(True)

        self.btn_snap.setEnabled(connected)
        self._refresh_rec_time()

    def _refresh_rec_time(self):
        """刷新时长显示（视频时间，暂停期间不增长）"""
        rec = self._screen_recorder
        secs = int(rec.elapsed_video_seconds()) if rec is not None else 0
        self.lbl_rec_time.setText(f"{secs // 60:02d}:{secs % 60:02d}")

    # ─── 录屏 ─────────────────────────────────────────────

    def _on_toggle_record(self):
        """录屏按钮：待机 → 开始；录制中/暂停 → 停止（进入待保存）"""
        if self._rec_state == "idle":
            self._start_screen_record()
        elif self._rec_state in ("recording", "paused"):
            self._stop_screen_record()

    def _start_screen_record(self):
        if not self._scrcpy_streaming or self._capture is None:
            self.log_text.append(tr("[录屏] 仅流式截图模式支持录屏"))
            return
        w, h = self._capture.get_capture_size()
        if w <= 0 or h <= 0:
            self.log_text.append(tr("[录屏] 无法获取画面尺寸"))
            return
        from ...core.screen_recorder import ScreenRecorder
        # 流式模式已确认，_capture 必为 AndroidStreamCapture
        fps = self._capture.max_fps
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = VIDEO_DIR / f"recording_{ts}.mp4"
        recorder = ScreenRecorder(path, w, h, fps=fps)
        if not recorder.start():
            self.log_text.append(tr("[录屏] 启动失败（详见日志）"))
            return
        self._screen_recorder = recorder
        self._rec_state = "recording"
        self._rec_timer.start()
        self.log_text.append(f"[录屏] 开始录制 → {path.name}")
        self._apply_rec_state()

    def _stop_screen_record(self):
        rec = self._screen_recorder
        if rec is None:
            return
        result = rec.stop()
        self._rec_state = "pending"
        self._rec_timer.stop()
        msg = f"[录屏] 已停止 时长 {result.duration:.1f}s 帧数 {result.frames}"
        if result.dropped:
            msg += f" 丢帧 {result.dropped}"
        self.log_text.append(msg + tr("，请选择 保存 或 放弃"))
        self._apply_rec_state()

    def _on_pause_record(self):
        """暂停/继续切换"""
        rec = self._screen_recorder
        if rec is None:
            return
        if self._rec_state == "recording":
            rec.pause()
            self._rec_state = "paused"
        elif self._rec_state == "paused":
            rec.resume()
            self._rec_state = "recording"
        self._apply_rec_state()

    # ─── 保存 / 放弃 ──────────────────────────────────────

    def _on_save_recording(self):
        rec = self._screen_recorder
        if rec is None:
            return
        final = rec.finalize()
        if final is not None:
            self.log_text.append(f"[录屏] 已保存: {final}")
            self.statusBar().showMessage(f"录屏已保存: {final.name}", 3000)
        else:
            self.log_text.append(tr("[录屏] 保存失败（详见日志）"))
        self._screen_recorder = None
        self._rec_state = "idle"
        self._rec_timer.stop()
        self._apply_rec_state()

    def _on_discard_recording(self):
        rec = self._screen_recorder
        if rec is None:
            return
        rec.discard()
        self.log_text.append(tr("[录屏] 已放弃本次录制，文件已删除"))
        self._screen_recorder = None
        self._rec_state = "idle"
        self._rec_timer.stop()
        self._apply_rec_state()

    def _abort_screen_record(self, reason: str):
        """强制中断守卫：断连/切换截图方式/关闭程序时自动停录并转正保存（不丢数据）"""
        rec = self._screen_recorder
        if rec is None:
            return
        final = rec.finalize()  # 内部先 stop() 再转正
        self._screen_recorder = None
        self._rec_state = "idle"
        if self._rec_timer is not None:
            self._rec_timer.stop()
        if final is not None:
            self.log_text.append(f"[录屏] 录制已随{reason}自动保存: {final}")
        else:
            self.log_text.append(f"[录屏] {reason}时自动保存失败（详见日志）")
        self._apply_rec_state()

    # ─── 截屏 ─────────────────────────────────────────────

    def _on_snap(self):
        """截屏：立即保存当前帧到 data/picture"""
        img = self._grab_capture_image()
        if img is None:
            self.log_text.append(tr("[截屏] 截屏失败：无可用画面"))
            return
        import cv2
        try:
            PICTURE_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = PICTURE_DIR / f"screenshot_{ts}.png"
            i = 1
            while path.exists():
                path = PICTURE_DIR / f"screenshot_{ts}_{i}.png"
                i += 1
            ok, buf = cv2.imencode(".png", img)
            if not ok:
                self.log_text.append(tr("[截屏] PNG 编码失败"))
                return
            # imencode + tofile 规避 Windows 非 ASCII 路径问题
            buf.tofile(str(path))
            self.log_text.append(f"[截屏] 已保存: {path}")
            self.statusBar().showMessage(f"截屏已保存: {path.name}", 3000)
        except Exception as e:
            logger.error(f"[截屏] 保存失败: {e}")
            self.log_text.append(f"[截屏] 保存失败: {e}")
