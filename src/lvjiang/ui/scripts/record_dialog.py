"""脚本编辑工作台内的录制面板（兼容独立容器）。

工作台可见期间临时注册
系统全局录制热键（默认 F12，可在配置管理→热键设置里改），用于开始/停止
录制；对话框关闭后立即注销。低精度实时生成可编辑 DSL；高精度在内存中
保存统一输入时间线，保存 WF 时自动写入 workflows/lvtrace 配套文件。
F9/F10/F11 是主窗口常驻全局热键，录制热键是本对话框打开期间的临时全局
热键；这些按键在按键录制时会被忽略，不会被误录成 press 语句。
"""

import os
from pathlib import Path

from loguru import logger
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
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

from ...core.config.resolver import get_resolver
from ...i18n import tr
from ..dialog_guards import EscapeCloseConfirmationMixin

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


class ScriptRecordDialog(EscapeCloseConfirmationMixin, QDialog):
    """脚本录制：录制按钮 + 实时 DSL 展示 + 保存/复制/清除"""

    line_captured = pyqtSignal(str)
    f12_pressed = pyqtSignal()

    def __init__(self, main_window, *, editor_host=None):
        super().__init__(main_window)
        self._main = main_window
        self._editor_host = editor_host
        self._embedded = editor_host is not None
        if self._embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self._recorder = None
        self._pending_trace = None
        self._saved_trace_ref = ""
        self._target_id = ""
        self._transferred = False
        self._f12_hotkey_listener = None
        self._preserved = False   # 已保存/复制过（防误关丢失）
        self.setWindowTitle(tr("脚本录制"))
        # 独立对话框需要足够的初始空间；嵌入工作台时作为
        # 中央页签使用，尺寸由编辑区统一分配。
        if not self._embedded:
            self.setMinimumSize(560, 520)
        self._setup_ui()
        self.line_captured.connect(self._append_line)
        self.f12_pressed.connect(self.toggle_recording)
        self._refresh_buttons()
        if self._embedded:
            # 嵌入脚本工作台：录制结果只落在这里，什么时候进代码由用户决定。
            # 文件保存交给编辑器自己的“保存”，这里不再另存 .wf。
            self.btn_save.hide()
            self.btn_insert.show()
            self.btn_copy.setText(tr("复制录制结果"))
            self.btn_clear.setText(tr("清除录制结果"))

    # ─── F12 热键生命周期 ───────────────────────────────

    @property
    def _record_key(self) -> str:
        """当前配置的录制热键（默认 F12），来自「配置管理 → 热键设置」。"""
        user_config = getattr(self._main, "_user_config", None)
        hotkeys = getattr(user_config, "hotkeys", None)
        return str(getattr(hotkeys, "record", "F12"))

    def _start_f12_hotkey(self):
        """对话框打开后才注册系统全局录制热键。"""
        if self._f12_hotkey_listener is not None:
            return
        from ...core.access import is_readonly
        if is_readonly():
            return
        from ...core.platforms import hotkey_pynput_token, start_global_hotkeys
        try:
            self._f12_hotkey_listener = start_global_hotkeys({
                hotkey_pynput_token(self._record_key): self.f12_pressed.emit,
            })
        except Exception as exc:
            logger.warning(f"脚本录制 {self._record_key} 全局热键注册失败: {exc}")

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
                logger.warning(f"脚本录制 {self._record_key} 热键监听线程 3 秒内未退出")
        except Exception as exc:
            logger.warning(f"脚本录制 {self._record_key} 全局热键注销失败: {exc}")

    def showEvent(self, event):  # type: ignore[override]
        super().showEvent(event)
        self._start_f12_hotkey()

    def done(self, result: int):  # type: ignore[override]
        """仅在对话框真正结束时注销；失焦/最小化不影响 F12。"""
        self.stop_f12_hotkey()
        super().done(result)

    def keyPressEvent(self, event):  # type: ignore[override]
        from ...core.access import is_readonly
        if is_readonly():
            super().keyPressEvent(event)
            return
        if event.key() == getattr(Qt.Key, f"Key_{self._record_key}", None):
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
        record_key = self._record_key

        control_row = QHBoxLayout()
        from ...core.access import is_readonly
        hotkeys_enabled = not is_readonly()
        record_label = tr("● 录制脚本")
        if hotkeys_enabled:
            record_label = f"{record_label} ({record_key})"
        self.btn_record = QPushButton(record_label)
        self.btn_record.setStyleSheet(_STYLE_IDLE)
        self.btn_record.clicked.connect(self.toggle_recording)
        control_row.addWidget(self.btn_record)

        idle_text = tr("待机 | 点击「录制脚本」")
        if hotkeys_enabled:
            idle_text = f"{tr('待机 | 点击「录制脚本」或按')} {record_key} {tr('开始')}"
        self.lbl_status = QLabel(idle_text)
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color: palette(mid);")
        control_row.addWidget(self.lbl_status, 1)
        layout.addLayout(control_row)

        # 结果动作紧贴结果区，不再和录制开关挤在一行。
        self.btn_save = QPushButton(tr("保存"))
        self.btn_save.setStyleSheet(_STYLE_ACTION)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_insert = QPushButton(tr("写入编辑区域"))
        self.btn_insert.setStyleSheet(_STYLE_ACTION)
        self.btn_insert.setToolTip(tr("把录制结果按语句插入代码编辑器的光标处"))
        self.btn_insert.clicked.connect(self._on_insert)
        self.btn_insert.hide()   # 只在嵌入脚本工作台时可见
        self.btn_copy = QPushButton(tr("复制"))
        self.btn_copy.setStyleSheet(_STYLE_ACTION)
        self.btn_copy.clicked.connect(self._on_copy)
        self.btn_clear = QPushButton(tr("清除"))
        self.btn_clear.setStyleSheet(_STYLE_ACTION)
        self.btn_clear.clicked.connect(self._on_clear)

        precision_row = QHBoxLayout()
        lbl_precision = QLabel(tr("录制精度："))
        precision_row.addWidget(lbl_precision)
        self.radio_precision_low = QRadioButton(tr("低精度"))
        low_tip = tr(
            "生成可读、可直接编辑的 WF DSL。录制停止时会合并能够安全合并的"
            "按键按下、按住、释放和后续等待；连续鼠标移动及短等待按约 100ms"
            "粒度归并。适合普通界面操作和后续手工调整，不适合要求逐个原始"
            "输入事件精确回放的游戏视角。"
        )
        self.radio_precision_low.setToolTip(low_tip)
        self.radio_precision_low.setChecked(True)
        self.radio_precision_high = QRadioButton(tr("高精度"))
        high_tip = tr(
            "记录键盘、鼠标按钮、滚轮以及可选鼠标移动的原始时间线。保存脚本"
            "时会同时生成 workflows/lvtrace 下的配套轨迹文件，回放时序更接近"
            "录制过程，适合游戏视角等高时序场景。脚本依赖配套轨迹文件，不能"
            "只复制 WF 文本进行迁移。"
        )
        self.radio_precision_high.setToolTip(high_tip)
        self._precision_group = QButtonGroup(self)
        self._precision_group.addButton(self.radio_precision_low)
        self._precision_group.addButton(self.radio_precision_high)
        precision_row.addWidget(self.radio_precision_low)
        precision_row.addWidget(self.radio_precision_high)
        precision_row.addStretch()
        layout.addLayout(precision_row)

        mouse_row = QHBoxLayout()
        mouse_tip = tr(
            "开启后记录鼠标移动轨迹；关闭时只忽略单纯移动，点击、按住、拖拽"
            "和滚轮仍会正常录制。普通界面通常无需开启；需要还原游戏视角转动"
            "或其他连续移动时再开启。"
        )
        lbl_mouse_movement = QLabel(tr("录制鼠标移动"))
        lbl_mouse_movement.setToolTip(mouse_tip)
        mouse_row.addWidget(lbl_mouse_movement)
        self.check_mouse_movement = QCheckBox()
        self.check_mouse_movement.setAccessibleName(tr("录制鼠标移动"))
        self.check_mouse_movement.setToolTip(mouse_tip)
        self.check_mouse_movement.setChecked(False)
        mouse_row.addWidget(self.check_mouse_movement)
        mouse_row.addStretch()
        layout.addLayout(mouse_row)

        result_row = QHBoxLayout()
        result_row.addWidget(QLabel(tr("录制结果")))
        result_row.addStretch()
        result_row.addWidget(self.btn_save)
        result_row.addWidget(self.btn_insert)
        result_row.addWidget(self.btn_copy)
        result_row.addWidget(self.btn_clear)
        layout.addLayout(result_row)

        hk = getattr(getattr(self._main, "_user_config", None), "hotkeys", None)
        reserved = "/".join(str(getattr(hk, key, default)) for key, default in (
            ("start", "F9"), ("pause", "F10"), ("stop", "F11"), ("record", "F12")
        ))
        self.text_edit = QTextEdit()
        self.text_edit.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13px;")
        placeholder = tr(
            "录制结果将显示在这里（画布归一化坐标）\n"
            "停止录制后可先在此修改，再「写入编辑区域」或「复制录制结果」"
            if self._embedded else
            "录制结果将显示在这里（画布归一化坐标，可保存为 .wf）")
        if hotkeys_enabled:
            placeholder += f"，{reserved} {tr('不会被录制')}"
        self.text_edit.setPlaceholderText(placeholder)
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
        if self._editor_host is not None \
                and not self._editor_host.can_accept_recording():
            self.lbl_status.setText(tr("请先选择或新建一个可编辑脚本，并保存已有高精度录制"))
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
        layout_name = main._layout_manager.get_active_layout_key()
        layout = main._layout_manager.load_layout(layout_name)
        if not layout:
            self.lbl_status.setText(tr("无法加载布局: {layout_name}").format(layout_name=layout_name))
            return
        if main._capture is None:
            from ...core.desktop import DesktopCapture
            main._capture = DesktopCapture()
        main._capture.set_capture_region(
            w["left"], w["top"], w["width"], w["height"])
        from ...core.macro_recorder import MacroRecorder
        hk = main._user_config.hotkeys
        from ...core.access import is_readonly
        self._target_id = (
            self._editor_host.recording_target_id()
            if self._editor_host is not None else ""
        )
        self._transferred = False
        if self._editor_host is not None:
            self._editor_host.begin_recording()
        try:
            self._recorder = MacroRecorder(
                target_window=w, capture=main._capture, layout=layout,
                win_left=w["left"], win_top=w["top"],
                on_line=self.line_captured.emit,
                precision=self.precision,
                reserved_keys={hk.start, hk.pause, hk.stop, hk.record},
                record_mouse_movement=self.check_mouse_movement.isChecked(),
            )
            self._recorder.start()
        except Exception as e:
            self._recorder = None
            if self._editor_host is not None:
                self._editor_host.end_recording()
            self.lbl_status.setText(tr("启动失败: {e}").format(e=e))
            logger.error(f"录制启动失败: {e}")
            return
        if self.precision == "high":
            status = tr("高精度录制中…原始输入写入统一时间线")
        else:
            status = tr("低精度录制中…连续移动将合并")
        if not is_readonly():
            status += f"，{hk.record} {tr('或点击停止')}"
        self.lbl_status.setText(status)
        self._refresh_buttons()

    def _stop_recording(self):
        recorder = self._recorder
        self._recorder = None
        if recorder is not None:
            try:
                dsl = recorder.stop()
            finally:
                if self._editor_host is not None:
                    self._editor_host.end_recording()
            self._pending_trace = (
                recorder.build_input_trace()
                if recorder.precision == "high" and dsl.strip() else None
            )
            self._saved_trace_ref = ""
            if dsl.strip():
                # 全文兜底刷新，防实时追加漏行
                self.text_edit.setPlainText(dsl)
                self._preserved = False
                if self._embedded:
                    self.lbl_status.setText(
                        tr("录制结束，可修改结果后「写入编辑区域」或「复制录制结果」"))
                else:
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
        record_key = self._record_key
        from ...core.access import is_readonly
        if recording:
            label = tr("■ 停止录制")
            self.btn_record.setStyleSheet(_STYLE_RECORDING)
        else:
            label = tr("● 录制脚本")
            self.btn_record.setStyleSheet(_STYLE_IDLE)
        self.btn_record.setText(
            label if is_readonly() else f"{label} ({record_key})")
        self.btn_record.setEnabled(
            self._main is not None and not getattr(self._main, "_running", False)
        )
        self.btn_save.setEnabled(not recording and has_text)
        can_insert = (
            not recording and has_text and self._editor_host is not None
            and not self._transferred
            and self._editor_host.can_accept_recording(self._target_id))
        self.btn_insert.setEnabled(can_insert)
        if self._transferred:
            self.btn_insert.setToolTip(
                tr("这份录制结果已写入编辑区域；清除后可开始新录制"))
        elif self._embedded and has_text and not recording and not can_insert:
            self.btn_insert.setToolTip(
                tr("当前脚本不可编辑（系统脚本请先复制到本地），或已带有高精度轨迹"))
        else:
            self.btn_insert.setToolTip(tr("把录制结果按语句插入代码编辑器的光标处"))
        self.btn_copy.setEnabled(
            not recording and has_text and self._pending_trace is None)
        self.btn_copy.setToolTip(
            tr("高精度 WF 依赖配套轨迹文件，不能单独复制")
            if self._pending_trace is not None else ""
        )
        self.btn_clear.setEnabled(not recording and has_text)
        self.radio_precision_low.setEnabled(not recording)
        self.radio_precision_high.setEnabled(not recording)
        self.check_mouse_movement.setEnabled(not recording)
        # 录制中只读；停止后可修改，但一旦写入编辑区就锁定，
        # 避免相同结果被连续写入多次。
        self.text_edit.setReadOnly(recording or self._transferred)

    def _on_text_changed(self):
        """文本变化后，之前的保存/复制视为失效"""
        self._preserved = False
        self._refresh_buttons()

    # ─── 保存 / 复制 / 清除 ───────────────────────────────

    @staticmethod
    def _with_script_traits(text: str, path: str) -> str:
        """录制产物没有 front-matter，补上「可运行」声明

        未声明 ``runnable`` 的 .wf 不会被发现层注册——用户录完保存，脚本会
        静默地从日常列表里消失。这里按文件名补一份最小声明。
        """
        from ...workflows.metadata import parse_metadata
        try:
            meta = parse_metadata(text)
        except Exception:  # noqa: BLE001 元数据有问题交给发现层告警
            return text
        if "runnable" in meta or "batchable" in meta:
            return text
        name = Path(path).stem
        return (f"#% name: {name}\n#% runnable: true\n"
                f"#% batchable: true\n\n{text}")

    def _on_save(self):
        """保存当前文本为 .wf 文件（默认目录为当前模式的可写 workflows 目录）"""
        default_path = str(get_resolver().write_dir("workflows") / "recorded.wf")
        path, _ = QFileDialog.getSaveFileName(
            self, tr("保存为工作流文件"), default_path,
            tr("工作流文件 (*.wf);;所有文件 (*)"))
        if not path:
            return
        try:
            text = self._with_script_traits(
                self.text_edit.toPlainText(), path)
            if self._pending_trace is not None:
                from ...core.input_trace import (
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

    def _on_insert(self):
        """把录制结果按语句插入编辑器光标处（嵌入模式）。

        高精度录制连同轨迹一起交给编辑器：编辑器保存脚本时才落地
        lvtrace 文件，这里只是把所有权移交过去。
        """
        if self._editor_host is None:
            return
        if self._transferred:
            self.lbl_status.setText(tr("这份录制结果已写入，请清除后重新录制"))
            return
        text = self.text_edit.toPlainText().rstrip()
        if not text.strip():
            return
        if self._editor_host.accept_recording(
                text, self._pending_trace, self._target_id):
            self._preserved = True
            self._transferred = True
            if self._pending_trace is not None:
                # 轨迹只能属于一份脚本，移交后本地不再持有
                self._pending_trace = None
            self.lbl_status.setText(tr("已写入编辑区域；请在编辑器中保存脚本"))
        else:
            self.lbl_status.setText(tr("未能写入：当前脚本不可编辑或已带有高精度轨迹"))
        self._refresh_buttons()

    def _on_copy(self):
        """复制当前文本到系统剪贴板"""
        QApplication.clipboard().setText(self.text_edit.toPlainText())
        self._preserved = True
        self.lbl_status.setText(tr("已复制到剪贴板"))

    def _on_clear(self):
        """清除文本区；只有内容还没写入/复制/保存过时才确认，免得每次多点一下"""
        if self.text_edit.toPlainText().strip() and not self._preserved:
            reply = QMessageBox.question(
                self, tr("清除"), tr("确定清除已录制的 DSL 内容吗？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._reset_result()

    def _reset_result(self) -> None:
        """无提示地重置录制暂存；调用方负责完成必要的确认。"""
        self.text_edit.clear()
        self._pending_trace = None
        self._saved_trace_ref = ""
        self._target_id = ""
        self._transferred = False
        self._preserved = False
        self.lbl_status.setText(tr("已清除"))
        self._refresh_buttons()

    @property
    def has_pending_result(self) -> bool:
        """是否有尚未交给目标脚本的录制结果。"""
        return bool(self.text_edit.toPlainText().strip()) and not self._transferred

    def confirm_script_change(self, next_target_id: str) -> bool:
        """录制结果绑定开始时的脚本；切换前必须明确放弃。"""
        if not self.has_pending_result or next_target_id == self._target_id:
            return True
        return self.confirm_abandon_result()

    def confirm_abandon_result(self) -> bool:
        """删除或替换目标脚本前，确认放弃未写入的录制结果。"""
        if not self.has_pending_result:
            return True
        reply = QMessageBox.question(
            self, tr("放弃录制结果？"),
            tr("录制结果尚未写入开始录制时的脚本。\n"
               "切换脚本将清除这份录制结果，是否继续？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False
        self._reset_result()
        return True

    # ─── 关闭保护 ─────────────────────────────────────────

    def _escape_needs_confirmation(self) -> bool:
        return self.is_recording or (
            bool(self.text_edit.toPlainText().strip()) and not self._preserved)

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
