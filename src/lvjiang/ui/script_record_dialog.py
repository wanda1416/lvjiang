"""脚本录制对话框 - 低精度 WF / 高精度 lvtrace 录制与保存

只能由用户从「工具 → 脚本录制」打开。对话框可见期间临时注册
系统全局 F12，用于开始/停止录制；对话框关闭后立即注销。低精度实时生成
可编辑 DSL；高精度在内存中保存统一输入时间线，保存 WF 时自动写入
workflows/lvtrace 配套文件。F8/F9/F10 是主窗口全局热键，
F12 是本对话框打开期间的临时全局热键；这些按键在
按键录制时会被忽略，不会被误录成 press 语句。
"""

import os
from pathlib import Path

from loguru import logger
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
)

from ..core.config.resolver import get_resolver
from ..i18n import tr

_STYLE_IDLE = (
    "background-color: #607D8B; color: white; font-weight: bold; "
    "font-size: 13px; padding: 8px 16px;"
)
_STYLE_RECORDING = (
    "background-color: #f44336; color: white; font-weight: bold; "
    "font-size: 13px; padding: 8px 16px;"
)
# 保存/复制/清除：与录制按钮同一量级放大，紧挨着录制按钮放，
# 避免用户找不到或误以为要去别处才能保存。
_STYLE_ACTION = "font-weight: bold; font-size: 13px; padding: 8px 16px;"


class ScriptRecordDialog(QDialog):
    """脚本录制：录制按钮 + 实时 DSL 展示 + 保存/复制/清除"""

    line_captured = pyqtSignal(str)
    f12_pressed = pyqtSignal()

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main = main_window
        self._recorder = None
        self._pending_trace = None
        self._saved_trace_ref = ""
        self._f12_hotkey_listener = None
        self._preserved = False   # 已保存/复制过（防误关丢失）
        self.setWindowTitle(tr("脚本录制"))
        self.setMinimumSize(560, 520)
        self._setup_ui()
        self.line_captured.connect(self._append_line)
        self.f12_pressed.connect(self.toggle_recording)
        self._refresh_buttons()

    # ─── F12 热键生命周期 ───────────────────────────────

    def _start_f12_hotkey(self):
        """对话框打开后才注册系统全局 F12。"""
        if self._f12_hotkey_listener is not None:
            return
        from ..core.platforms import start_global_hotkeys
        try:
            self._f12_hotkey_listener = start_global_hotkeys({
                "<f12>": self.f12_pressed.emit,
            })
        except Exception as exc:
            logger.warning(f"脚本录制 F12 全局热键注册失败: {exc}")

    def stop_f12_hotkey(self):
        """对话框关闭时注销 F12，并等待钩子线程退出。"""
        listener = self._f12_hotkey_listener
        self._f12_hotkey_listener = None
        if listener is None:
            return
        try:
            listener.stop()
            listener.join(3.0)
            if listener.is_alive():
                logger.warning("脚本录制 F12 热键监听线程 3 秒内未退出")
        except Exception as exc:
            logger.warning(f"脚本录制 F12 全局热键注销失败: {exc}")

    def showEvent(self, event):  # type: ignore[override]
        super().showEvent(event)
        self._start_f12_hotkey()

    def done(self, result: int):  # type: ignore[override]
        """仅在对话框真正结束时注销；失焦/最小化不影响 F12。"""
        self.stop_f12_hotkey()
        super().done(result)

    def keyPressEvent(self, event):  # type: ignore[override]
        if event.key() == Qt.Key.Key_F12:
            # 全局 listener 已激活时，Qt 也可能收到同一次按键；
            # 只保留一个切换入口，避免开始后立即又停止。
            if self._f12_hotkey_listener is None:
                self.toggle_recording()
            event.accept()
            return
        super().keyPressEvent(event)

    # ─── UI 构建 ─────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        btn_row = QHBoxLayout()
        self.btn_record = QPushButton(tr("● 录制脚本 (F12)"))
        self.btn_record.setStyleSheet(_STYLE_IDLE)
        self.btn_record.clicked.connect(self.toggle_recording)
        btn_row.addWidget(self.btn_record)
        self.btn_save = QPushButton(tr("保存"))
        self.btn_save.setStyleSheet(_STYLE_ACTION)
        self.btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self.btn_save)
        self.btn_copy = QPushButton(tr("复制"))
        self.btn_copy.setStyleSheet(_STYLE_ACTION)
        self.btn_copy.clicked.connect(self._on_copy)
        btn_row.addWidget(self.btn_copy)
        self.btn_clear = QPushButton(tr("清除"))
        self.btn_clear.setStyleSheet(_STYLE_ACTION)
        self.btn_clear.clicked.connect(self._on_clear)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        mode_row = QHBoxLayout()
        lbl_precision = QLabel(tr("录制精度"))
        lbl_precision.setStyleSheet("font-size: 14px; font-weight: bold;")
        mode_row.addWidget(lbl_precision)
        self.radio_precision_low = QRadioButton(tr("低精度（普通界面，可编辑 WF）"))
        self.radio_precision_low.setToolTip(tr("合并连续移动，生成可编辑 DSL 指令"))
        self.radio_precision_low.setChecked(True)
        self.radio_precision_high = QRadioButton(tr("高精度（游戏视角，原始轨迹）"))
        self.radio_precision_high.setToolTip(
            tr("保存 workflows/lvtrace 配套文件，忠实还原原始输入"))
        self._precision_group = QButtonGroup(self)
        self._precision_group.addButton(self.radio_precision_low)
        self._precision_group.addButton(self.radio_precision_high)
        mode_row.addWidget(self.radio_precision_low)
        mode_row.addWidget(self.radio_precision_high)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        self.lbl_status = QLabel(tr("待机 | 点击「录制脚本」或按 F12 开始"))
        self.lbl_status.setStyleSheet("color: palette(mid);")
        layout.addWidget(self.lbl_status)

        self.text_edit = QTextEdit()
        self.text_edit.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13px;")
        self.text_edit.setPlaceholderText(
            tr("录制结果将显示在这里（画布归一化坐标，可保存为 .wf）\n"
               "低精度生成可编辑指令；高精度保存原始输入轨迹，"
               "F8/F9/F10/F12 不会被录制"))
        self.text_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.text_edit)

    # ─── 录制控制 ─────────────────────────────────────────

    @property
    def is_recording(self) -> bool:
        return self._recorder is not None

    @property
    def precision(self) -> str:
        return "high" if self.radio_precision_high.isChecked() else "low"

    def toggle_recording(self):
        """录制/停止切换（对话框按钮与临时 F12 共用入口）。"""
        if self.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        # 已有未清除的内容时拒绝开始新录制（按钮和 F12 共用这个入口）——
        # 否则用户录完忘了保存，误按 F12/录制按钮会把刚录好的内容直接冲掉。
        if self.text_edit.toPlainText().strip():
            QMessageBox.warning(
                self, tr("无法开始录制"),
                tr("已有未清除的录制内容，请清除后再次点击「录制脚本」，"
                   "避免覆盖丢失。"))
            return
        main = self._main
        if main._running:
            self.lbl_status.setText(tr("工作流运行中，无法录制"))
            return
        if main._backend == "adb":
            self.lbl_status.setText(tr("ADB 模式暂不支持录制"))
            return
        w = main._target_window
        if not w:
            self.lbl_status.setText(tr("请先在主窗口扫描并定位窗口"))
            return
        layout_name = main._layout_manager.get_active_layout_name()
        layout = main._layout_manager.load_layout(layout_name)
        if not layout:
            self.lbl_status.setText(tr("无法加载布局: {layout_name}").format(layout_name=layout_name))
            return
        if main._capture is None:
            from ..core.desktop import DesktopCapture
            main._capture = DesktopCapture()
        main._capture.set_capture_region(
            w["left"], w["top"], w["width"], w["height"])
        from .macros import MacroRecorder
        try:
            self._recorder = MacroRecorder(
                target_window=w, capture=main._capture, layout=layout,
                win_left=w["left"], win_top=w["top"],
                on_line=self.line_captured.emit,
                precision=self.precision,
            )
            self._recorder.start()
        except Exception as e:
            self._recorder = None
            self.lbl_status.setText(tr("启动失败: {e}").format(e=e))
            logger.error(f"录制启动失败: {e}")
            return
        if self.precision == "high":
            self.lbl_status.setText(tr(
                "高精度录制中…原始输入写入统一时间线，F12 或点击停止"))
        else:
            self.lbl_status.setText(tr(
                "低精度录制中…连续移动将合并，F12 或点击停止"))
        self._refresh_buttons()

    def _stop_recording(self):
        recorder = self._recorder
        self._recorder = None
        if recorder is not None:
            dsl = recorder.stop()
            self._pending_trace = (
                recorder.build_input_trace()
                if recorder.precision == "high" and dsl.strip() else None
            )
            self._saved_trace_ref = ""
            if dsl.strip():
                # 全文兜底刷新，防实时追加漏行
                self.text_edit.setPlainText(dsl)
                self.lbl_status.setText(tr("录制结束，可编辑后保存为 .wf"))
            else:
                self.lbl_status.setText(tr("录制结束，未捕获到有效操作"))
        self._refresh_buttons()

    def _append_line(self, line: str):
        """实时追加一行 DSL（UI 线程，由 line_captured 信号触发）"""
        self.text_edit.append(line)

    # ─── 按钮可用性 ───────────────────────────────────────

    def _refresh_buttons(self):
        recording = self.is_recording
        has_text = bool(self.text_edit.toPlainText().strip())
        if recording:
            self.btn_record.setText(tr("■ 停止录制 (F12)"))
            self.btn_record.setStyleSheet(_STYLE_RECORDING)
        else:
            self.btn_record.setText(tr("● 录制脚本 (F12)"))
            self.btn_record.setStyleSheet(_STYLE_IDLE)
        self.btn_record.setEnabled(not self._main._running)
        self.btn_save.setEnabled(not recording and has_text)
        self.btn_copy.setEnabled(
            not recording and has_text and self._pending_trace is None)
        self.btn_copy.setToolTip(
            tr("高精度 WF 依赖配套轨迹文件，不能单独复制")
            if self._pending_trace is not None else ""
        )
        self.btn_clear.setEnabled(not recording and has_text)
        self.radio_precision_low.setEnabled(not recording)
        self.radio_precision_high.setEnabled(not recording)
        self.text_edit.setReadOnly(recording)

    def _on_text_changed(self):
        """文本变化后，之前的保存/复制视为失效"""
        self._preserved = False
        self._refresh_buttons()

    # ─── 保存 / 复制 / 清除 ───────────────────────────────

    def _on_save(self):
        """保存当前文本为 .wf 文件（默认目录为当前模式的可写 workflows 目录）"""
        default_path = str(get_resolver().write_dir("workflows") / "recorded.wf")
        path, _ = QFileDialog.getSaveFileName(
            self, tr("保存为工作流文件"), default_path,
            tr("工作流文件 (*.wf);;所有文件 (*)"))
        if not path:
            return
        try:
            text = self.text_edit.toPlainText()
            if self._pending_trace is not None:
                from ..core.input_trace import (
                    TRACE_PLACEHOLDER,
                    save_input_trace_bundle,
                )

                template = text
                if TRACE_PLACEHOLDER not in template and self._saved_trace_ref:
                    template = template.replace(
                        self._saved_trace_ref, TRACE_PLACEHOLDER)
                wf_path, trace_path, final_text = save_input_trace_bundle(
                    path,
                    template,
                    self._pending_trace,
                    workflows_root=get_resolver().write_dir("workflows"),
                )
                trace_ref = Path(
                    os.path.relpath(trace_path, wf_path.parent)
                ).as_posix()
                self._saved_trace_ref = trace_ref
                self.text_edit.setPlainText(final_text.rstrip("\n"))
                logger.info(f"高精度录制已保存: {wf_path} + {trace_path}")
            else:
                Path(path).write_text(text, encoding="utf-8")
            self._preserved = True
            logger.info(f"录制 DSL 已保存: {path}")
            self.lbl_status.setText(f"已保存: {path}")
        except Exception as e:
            logger.error(f"保存录制 DSL 失败: {e}")
            QMessageBox.warning(self, tr("保存失败"), str(e))

    def _on_copy(self):
        """复制当前文本到系统剪贴板"""
        QApplication.clipboard().setText(self.text_edit.toPlainText())
        self._preserved = True
        self.lbl_status.setText(tr("已复制到剪贴板"))

    def _on_clear(self):
        """清除文本区（非空时先确认）"""
        if self.text_edit.toPlainText().strip():
            reply = QMessageBox.question(
                self, tr("清除"), tr("确定清除已录制的 DSL 内容吗？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.text_edit.clear()
        self._pending_trace = None
        self._saved_trace_ref = ""
        self._preserved = False
        self.lbl_status.setText(tr("已清除"))

    # ─── 关闭保护 ─────────────────────────────────────────

    def _confirm_discard(self) -> bool:
        """未保存/复制且有内容时弹确认，返回是否允许关闭"""
        if self._preserved or not self.text_edit.toPlainText().strip():
            return True
        reply = QMessageBox.question(
            self, tr("未保存"),
            tr("录制内容尚未保存或复制到剪贴板，确定要关闭吗？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def reject(self):
        """Esc / 关闭按钮：录制中先停止，再走丢弃确认"""
        if self.is_recording:
            self._stop_recording()
        if self._confirm_discard():
            super().reject()

    def closeEvent(self, event):
        if self.is_recording:
            self._stop_recording()
        if self._confirm_discard():
            self.stop_f12_hotkey()
            event.accept()
        else:
            event.ignore()
