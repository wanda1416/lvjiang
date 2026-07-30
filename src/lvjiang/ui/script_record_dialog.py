"""脚本录制对话框 - 录制鼠标操作实时生成 DSL，支持保存/复制/清除

从「工具 → 脚本录制」打开（F8 也可）。录制中每生成一行 DSL 实时追加到
文本区（pynput 监听线程 → line_captured 信号 → UI 线程）；停止后用
recorder.stop() 全文兜底刷新，文本可编辑后再保存为 .wf。
"""

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QFileDialog, QMessageBox, QApplication,
)
from loguru import logger

from lvjiang.constants import SYSTEM_WORKFLOWS_DIR

_STYLE_IDLE = (
    "background-color: #607D8B; color: white; font-weight: bold; padding: 8px;"
)
_STYLE_RECORDING = (
    "background-color: #f44336; color: white; font-weight: bold; padding: 8px;"
)


class ScriptRecordDialog(QDialog):
    """脚本录制：录制按钮 + 实时 DSL 展示 + 保存/复制/清除"""

    line_captured = pyqtSignal(str)

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main = main_window
        self._recorder = None
        self._preserved = False   # 已保存/复制过（防误关丢失）
        self.setWindowTitle("脚本录制")
        self.setMinimumSize(560, 520)
        self._setup_ui()
        self.line_captured.connect(self._append_line)
        self._refresh_buttons()

    # ─── UI 构建 ─────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        btn_row = QHBoxLayout()
        self.btn_record = QPushButton("● 录制脚本 (F8)")
        self.btn_record.setStyleSheet(_STYLE_IDLE)
        self.btn_record.clicked.connect(self.toggle_recording)
        btn_row.addWidget(self.btn_record)
        btn_row.addStretch()
        self.btn_save = QPushButton("保存")
        self.btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self.btn_save)
        self.btn_copy = QPushButton("复制")
        self.btn_copy.clicked.connect(self._on_copy)
        btn_row.addWidget(self.btn_copy)
        self.btn_clear = QPushButton("清除")
        self.btn_clear.clicked.connect(self._on_clear)
        btn_row.addWidget(self.btn_clear)
        layout.addLayout(btn_row)

        self.lbl_status = QLabel("待机 | 点击「录制脚本」或按 F8 开始")
        self.lbl_status.setStyleSheet("color: #888;")
        layout.addWidget(self.lbl_status)

        self.text_edit = QTextEdit()
        self.text_edit.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13px;")
        self.text_edit.setPlaceholderText(
            "录制生成的 DSL 语句将实时显示在这里（画布归一化坐标，可直接保存为 .wf）")
        self.text_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.text_edit)

    # ─── 录制控制 ─────────────────────────────────────────

    @property
    def is_recording(self) -> bool:
        return self._recorder is not None

    def toggle_recording(self):
        """录制/停止切换（按钮与主窗口 F8 共用入口）"""
        if self.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        main = self._main
        if getattr(main, "_running", False):
            self.lbl_status.setText("工作流运行中，无法录制")
            return
        if getattr(main, "_backend", None) == "adb":
            self.lbl_status.setText("ADB 模式暂不支持录制")
            return
        w = getattr(main, "_target_window", None)
        if not w:
            self.lbl_status.setText("请先在主窗口扫描并定位窗口")
            return
        layout_name = main._layout_manager.get_active_layout_name()
        layout = main._layout_manager.load_layout(layout_name)
        if not layout:
            self.lbl_status.setText(f"无法加载布局: {layout_name}")
            return
        if main._capture is None:
            from ..core.desktop import DesktopCapture
            main._capture = DesktopCapture()
        main._capture.set_capture_region(
            w["left"], w["top"], w["width"], w["height"])
        from ..macros import MacroRecorder
        try:
            self._recorder = MacroRecorder(
                target_window=w, capture=main._capture, layout=layout,
                win_left=w["left"], win_top=w["top"],
                on_line=self.line_captured.emit,
            )
            self._recorder.start()
        except Exception as e:
            self._recorder = None
            self.lbl_status.setText(f"启动失败: {e}")
            logger.error(f"录制启动失败: {e}")
            return
        self.lbl_status.setText("录制中…在游戏窗口内点击/拖拽，F8 或点击停止")
        self._refresh_buttons()

    def _stop_recording(self):
        recorder = self._recorder
        self._recorder = None
        if recorder is not None:
            dsl = recorder.stop()
            if dsl.strip():
                # 全文兜底刷新，防实时追加漏行
                self.text_edit.setPlainText(dsl)
                self.lbl_status.setText("录制结束，可编辑后保存为 .wf")
            else:
                self.lbl_status.setText("录制结束，未捕获到有效操作")
        self._refresh_buttons()

    def _append_line(self, line: str):
        """实时追加一行 DSL（UI 线程，由 line_captured 信号触发）"""
        self.text_edit.append(line)

    # ─── 按钮可用性 ───────────────────────────────────────

    def _refresh_buttons(self):
        recording = self.is_recording
        has_text = bool(self.text_edit.toPlainText().strip())
        if recording:
            self.btn_record.setText("■ 停止录制 (F8)")
            self.btn_record.setStyleSheet(_STYLE_RECORDING)
        else:
            self.btn_record.setText("● 录制脚本 (F8)")
            self.btn_record.setStyleSheet(_STYLE_IDLE)
        self.btn_record.setEnabled(not getattr(self._main, "_running", False))
        for btn in (self.btn_save, self.btn_copy, self.btn_clear):
            btn.setEnabled(not recording and has_text)
        self.text_edit.setReadOnly(recording)

    def _on_text_changed(self):
        """文本变化后，之前的保存/复制视为失效"""
        self._preserved = False
        self._refresh_buttons()

    # ─── 保存 / 复制 / 清除 ───────────────────────────────

    def _on_save(self):
        """保存当前文本为 .wf 文件（默认目录为系统工作流目录）"""
        default_path = str(SYSTEM_WORKFLOWS_DIR / "recorded.wf")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存为工作流文件", default_path,
            "工作流文件 (*.wf);;所有文件 (*)")
        if not path:
            return
        try:
            Path(path).write_text(
                self.text_edit.toPlainText(), encoding="utf-8")
            self._preserved = True
            logger.info(f"录制 DSL 已保存: {path}")
            self.lbl_status.setText(f"已保存: {path}")
        except Exception as e:
            logger.error(f"保存录制 DSL 失败: {e}")
            QMessageBox.warning(self, "保存失败", str(e))

    def _on_copy(self):
        """复制当前文本到系统剪贴板"""
        QApplication.clipboard().setText(self.text_edit.toPlainText())
        self._preserved = True
        self.lbl_status.setText("已复制到剪贴板")

    def _on_clear(self):
        """清除文本区（非空时先确认）"""
        if self.text_edit.toPlainText().strip():
            reply = QMessageBox.question(
                self, "清除", "确定清除已录制的 DSL 内容吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.text_edit.clear()
        self._preserved = False
        self.lbl_status.setText("已清除")

    # ─── 关闭保护 ─────────────────────────────────────────

    def _confirm_discard(self) -> bool:
        """未保存/复制且有内容时弹确认，返回是否允许关闭"""
        if self._preserved or not self.text_edit.toPlainText().strip():
            return True
        reply = QMessageBox.question(
            self, "未保存",
            "录制内容尚未保存或复制到剪贴板，确定要关闭吗？",
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
            event.accept()
        else:
            event.ignore()
