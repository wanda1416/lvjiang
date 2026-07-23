"""通用主窗口。

包含完整的基础功能：
- 用户管理、场景管理、图库管理、图像识别测试
- 窗口/设备扫描与定位
- 工作流加载、执行、录制
- 运行日志面板
- 全局热键（F9 执行、F10 停止、F8 录制）

插件通过 hooks 机制扩展左侧/右侧 Tab 和菜单项。
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtGui import QKeyEvent, QAction
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QGroupBox, QTextEdit,
    QTabWidget, QSplitter, QMessageBox, QFormLayout, QScrollArea,
    QCheckBox, QStatusBar, QMenuBar,
)
from loguru import logger

from pynput import keyboard as pynput_keyboard

from src.apps import get_registry
from .overlay import BorderOverlay
from .window_ops import WindowOpsMixin
from .run_control import RunControlMixin
from .widgets import TrimmedLogEdit
from ..config import load_user_config
from ..core.user_config import UserConfigManager
from ..core.session_manager import SessionManager
from ..core.layout_manager import LayoutConfigManager


class _LogBridge(QObject):
    """信号桥：将后台线程的日志安全转发到主线程"""
    append_log = pyqtSignal(str)


DEFAULT_TITLE = "律匠 - 通用视觉 RPA 引擎"


class MainWindow(WindowOpsMixin, RunControlMixin, QMainWindow):
    """通用主窗口。

    从全局注册表读取扩展点，由插件在启动时注入。
    子类可覆盖 ``_extra_menu_items`` / ``_extra_left_tabs`` / ``_extra_right_tabs``
    添加插件专属功能。
    """

    # 全局热键信号
    f10_pressed = pyqtSignal()
    f8_pressed = pyqtSignal()
    _scrcpy_frame_ready = pyqtSignal(object)

    def __init__(self, hooks_list: list[Any] | None = None) -> None:
        super().__init__()
        self._hooks_list = hooks_list or []
        registry = get_registry()

        # 标题
        title = registry.get("window_title") or DEFAULT_TITLE
        self.setWindowTitle(title)
        self.setMinimumSize(1000, 700)

        # ── 状态属性 ──
        self._target_window = None
        self._scanned_windows = []
        self._device = None
        self._device_ready = False
        self._running = False
        self._stop_requested = False
        self._current_worker = None
        self._overlay = BorderOverlay()
        self._recorder = None
        self._capture = None
        self._last_capture = None
        self._region_layout = None

        # ── 管理器 ──
        self._user_manager = UserConfigManager()
        self._session_manager = SessionManager()
        self._layout_manager = LayoutConfigManager()
        self._user_config = load_user_config()
        self._backend = None

        # ── OCR / 输入 ──
        from ..core.ocr import OCREngine
        self._ocr = OCREngine()
        from ..core.desktop import create_input_backend as _create_desktop_input
        self._win_input = _create_desktop_input(mode="post", delay_config=self._user_config.input_delay)
        self._input = self._win_input

        # ── 构建 UI ──
        self._setup_menu()
        self._setup_ui()
        self._refresh_run_button()
        self._refresh_user_combo()
        self._refresh_layout_combo()
        self._load_workflow_configs()

        # ── 全局热键 ──
        self.f10_pressed.connect(self._request_stop)
        self.f8_pressed.connect(self._toggle_recording)
        self._scrcpy_frame_ready.connect(self._on_scrcpy_frame_ui)
        self._hotkey_listener = pynput_keyboard.GlobalHotKeys({
            "<f10>": self._on_global_f10,
            "<f8>": self._on_global_f8,
        })
        self._hotkey_listener.start()

        logger.info("主窗口已初始化")

    # ─── 热键回调 ────────────────────────────────────────────

    def _on_global_f10(self):
        self.f10_pressed.emit()

    def _on_global_f8(self):
        self.f8_pressed.emit()

    # ─── 菜单栏 ──────────────────────────────────────────────

    def _setup_menu(self):
        menubar = self.menuBar()
        menubar.setStyleSheet("""
            QMenuBar::item { padding: 6px 16px; }
            QMenuBar::item:selected { background: #d4d4d4; }
            QMenu::item { padding: 8px 32px; }
            QMenu::item:selected { background: #0078d4; color: white; }
        """)

        # ── 管理 ──
        settings_menu = menubar.addMenu("管理")

        user_mgmt = QAction("用户管理", self)
        user_mgmt.setShortcut("F2")
        user_mgmt.triggered.connect(self._open_user_manager)
        settings_menu.addAction(user_mgmt)

        scene_editor = QAction("场景管理", self)
        scene_editor.setShortcut("F3")
        scene_editor.triggered.connect(self._open_scene_editor)
        settings_menu.addAction(scene_editor)

        reference_mgr = QAction("图库管理", self)
        reference_mgr.setShortcut("F4")
        reference_mgr.triggered.connect(self._open_reference_manager)
        settings_menu.addAction(reference_mgr)

        settings_menu.addSeparator()

        # 插件扩展点：额外菜单项
        for label, slot, shortcut in self._extra_menu_items():
            settings_menu.addSeparator() if shortcut == "---" else None
            action = QAction(label, self)
            if shortcut and shortcut != "---":
                action.setShortcut(shortcut)
            action.triggered.connect(slot)
            settings_menu.addAction(action)

        # ── 工具 ──
        tools_menu = menubar.addMenu("工具")

        ocr_test = QAction("图像识别测试", self)
        ocr_test.triggered.connect(self._open_ocr_test)
        tools_menu.addAction(ocr_test)

        # ── 帮助 ──
        help_menu = menubar.addMenu("帮助")
        about = QAction("关于", self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)

        # 插件 menu builders（兼容旧机制）
        registry = get_registry()
        for builder in registry.get("menu_builders", []):
            try:
                builder(menubar)
            except Exception:  # noqa: BLE001
                logger.exception("menu builder 执行失败")

    def _extra_menu_items(self) -> list[tuple[str, Any, str]]:
        """子类覆盖：返回额外菜单项 [(label, slot, shortcut), ...]"""
        return []

    # ─── 对话框 ──────────────────────────────────────────────

    def _open_ocr_test(self):
        from .ocr_test_dialog import OCRTestDialog
        dialog = OCRTestDialog(self)
        dialog.exec()

    def _open_scene_editor(self):
        from .scene_editor.scene_editor import SceneEditorDialog
        dialog = SceneEditorDialog(
            layout_manager=self._layout_manager,
            refresh_callback=self._refresh_capture,
            parent=self,
        )
        dialog.exec()
        self._refresh_layout_combo()

    def _open_reference_manager(self):
        from .reference_manager import ReferenceManagerDialog
        dialog = ReferenceManagerDialog(parent=self)
        dialog.exec()
        if dialog.data_changed:
            from ..workflows.base import BaseWorkflow
            if BaseWorkflow._shared_material_recognizer is not None:
                BaseWorkflow._shared_material_recognizer.reload()
                self.statusBar().showMessage("图库已刷新", 3000)

    def _show_about(self):
        QMessageBox.about(
            self, "关于律匠",
            "<h3>律匠 v0.1.0</h3>"
            "<p>通用视觉 RPA 引擎</p>"
            "<p>功能：窗口定位截屏 → 区域标注 → OCR识别 → 工作流执行</p>"
            "<hr>"
            "<p style='color: gray;'>基于 PyQt6 + RapidOCR</p>",
        )

    def _open_user_manager(self):
        from .user_manager_dialog import UserManagerDialog
        dialog = UserManagerDialog(self._user_manager, self)
        dialog.exec()
        self._refresh_user_combo()

    def _on_toggle_preview(self, checked: bool):
        self.preview_label.setVisible(not checked)
        self.btn_hide_window.setText("显示预览" if checked else "隐藏预览")

    # ─── UI 构建 ─────────────────────────────────────────────

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # === 顶部：用户 + 布局 ===
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("当前用户"))
        self.user_combo = QComboBox()
        self.user_combo.setMinimumWidth(150)
        self.user_combo.currentIndexChanged.connect(self._on_user_changed)
        top_row.addWidget(self.user_combo)
        top_row.addSpacing(20)
        top_row.addWidget(QLabel("当前布局"))
        self.layout_combo = QComboBox()
        self.layout_combo.setMinimumWidth(150)
        self.layout_combo.currentIndexChanged.connect(self._on_layout_changed)
        top_row.addWidget(self.layout_combo)
        top_row.addStretch()
        main_layout.addLayout(top_row)

        # === 窗口/设备选择 ===
        window_group = QGroupBox()
        self.window_group = window_group
        window_main_layout = QVBoxLayout(window_group)

        row1 = QHBoxLayout()
        self.btn_scan_window = QPushButton("扫描窗口")
        self.btn_scan_window.setFixedWidth(90)
        self.btn_scan_window.clicked.connect(self._on_scan_window)
        row1.addWidget(self.btn_scan_window)

        self.btn_scan_device = QPushButton("扫描设备")
        self.btn_scan_device.setFixedWidth(90)
        self.btn_scan_device.clicked.connect(self._on_scan_devices)
        row1.addWidget(self.btn_scan_device)

        self.window_combo = QComboBox()
        self.window_combo.setMinimumWidth(300)
        self.window_combo.currentIndexChanged.connect(self._on_window_selected)
        row1.addWidget(self.window_combo)

        self.btn_locate = QPushButton("定位")
        self.btn_locate.setFixedWidth(70)
        self.btn_locate.setEnabled(False)
        self.btn_locate.clicked.connect(self._on_locate_window)
        row1.addWidget(self.btn_locate)

        self.btn_hide_window = QPushButton("显示预览")
        self.btn_hide_window.setFixedWidth(80)
        self.btn_hide_window.setCheckable(True)
        self.btn_hide_window.setChecked(True)
        self.btn_hide_window.clicked.connect(self._on_toggle_preview)
        row1.addWidget(self.btn_hide_window)
        window_main_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.lbl_window_info = QLabel("未选择窗口")
        self.lbl_window_info.setStyleSheet("color: gray;")
        row2.addWidget(self.lbl_window_info)
        row2.addStretch()

        self.chk_bg_mode = QCheckBox("后台模式")
        self.chk_bg_mode.setVisible(False)
        self.chk_bg_mode.stateChanged.connect(self._on_bg_mode_changed)
        row2.addWidget(self.chk_bg_mode)

        self.chk_scrcpy = QCheckBox("流式截图")
        self.chk_scrcpy.setVisible(False)
        self.chk_scrcpy.stateChanged.connect(self._on_capture_method_changed)
        row2.addWidget(self.chk_scrcpy)
        window_main_layout.addLayout(row2)

        self._apply_backend_ui(self._backend)
        main_layout.addWidget(window_group)

        # === 截屏预览区 ===
        self.preview_label = QLabel("定位窗口后自动截屏")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setFixedHeight(320)
        self.preview_label.setStyleSheet("background-color: #2b2b2b; color: #888; font-size: 14px;")
        self.preview_label.setVisible(False)
        main_layout.addWidget(self.preview_label)

        # === 中部：左右分栏 ===
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧 Tab
        self._left_tabs = QTabWidget()
        self._left_tabs.setMinimumWidth(240)
        self._build_left_tabs()
        splitter.addWidget(self._left_tabs)

        # 右侧 Tab（与左侧结构一致，直接添加 QTabWidget）
        self.tabs = QTabWidget()
        self._build_right_tabs()
        splitter.addWidget(self.tabs)

        splitter.setSizes([250, 750])
        main_layout.addWidget(splitter, stretch=1)

        # === 底部状态栏 ===
        self.statusBar().showMessage("就绪 | F9 开始 | F10 停止 | F8 录制")
        self.adjustSize()
        self.setMinimumHeight(self.height())
        self._setup_log_redirect()

    def _build_left_tabs(self):
        """构建左侧 Tab（通用：日常）。子类覆盖可追加。"""
        # ── Tab 1: 日常 ──
        daily_scroll = QScrollArea()
        daily_scroll.setWidgetResizable(True)
        daily_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        daily_panel = QWidget()
        daily_layout = QVBoxLayout(daily_panel)
        daily_layout.setContentsMargins(4, 4, 4, 4)

        wf_group = QGroupBox("工作流")
        wf_layout = QVBoxLayout(wf_group)
        self.workflow_combo = QComboBox()
        self.workflow_combo.setMinimumWidth(200)
        self.workflow_combo.currentIndexChanged.connect(self._rebuild_param_panel)
        wf_layout.addWidget(self.workflow_combo)
        daily_layout.addWidget(wf_group)

        self.btn_run_workflow = QPushButton("开始执行 (F9)")
        self.btn_run_workflow.clicked.connect(self._on_run_workflow)
        self.btn_run_workflow.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px;"
        )
        daily_layout.addWidget(self.btn_run_workflow)

        tools_row = QHBoxLayout()
        self.btn_record = QPushButton("录制 (F8)")
        self.btn_record.clicked.connect(self._toggle_recording)
        self.btn_record.setStyleSheet(
            "background-color: #607D8B; color: white; font-weight: bold; padding: 8px;"
        )
        tools_row.addWidget(self.btn_record)

        self.btn_load_workflow = QPushButton("加载工作流")
        self.btn_load_workflow.clicked.connect(self._on_load_workflow)
        self.btn_load_workflow.setStyleSheet(
            "background-color: #607D8B; color: white; font-weight: bold; padding: 8px;"
        )
        tools_row.addWidget(self.btn_load_workflow)
        daily_layout.addLayout(tools_row)

        self._param_panel = QGroupBox("参数设置")
        self._param_layout = QFormLayout(self._param_panel)
        self._param_panel.setVisible(False)
        daily_layout.addWidget(self._param_panel)

        daily_layout.addStretch()
        daily_scroll.setWidget(daily_panel)
        self._left_tabs.addTab(daily_scroll, "日常")

    def _build_right_tabs(self):
        """构建右侧 Tab（通用：运行日志）。子类覆盖可追加。"""
        self.log_text = TrimmedLogEdit()
        self.log_text.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        self.tabs.addTab(self.log_text, "运行日志")

    def _setup_log_redirect(self):
        self._log_bridge = _LogBridge(self)
        self._log_bridge.append_log.connect(self.log_text.append)

        class QtSink:
            def __init__(self, bridge):
                self._bridge = bridge
            def write(self, message):
                self._bridge.append_log.emit(message.strip())

        sink = QtSink(self._log_bridge)
        logger.add(sink, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}")

    def _rebuild_param_panel(self):
        while self._param_layout.rowCount() > 0:
            self._param_layout.removeRow(0)
        flow_cfg = self._get_selected_flow_config()
        params = flow_cfg.get("parameters", []) if flow_cfg else []
        if not params:
            self._param_panel.setVisible(False)
            return
        from PyQt6.QtWidgets import QSpinBox
        for param_def in params:
            name = param_def["name"]
            label = param_def.get("label", name)
            param_type = param_def.get("type", "select")
            default = param_def.get("default")
            options = param_def.get("options", [])
            if param_type == "number":
                spin = QSpinBox()
                spin.setObjectName(name)
                spin.setRange(param_def.get("min", 1), param_def.get("max", 9999))
                spin.setValue(int(default) if default is not None else 1)
                self._param_layout.addRow(label + ":", spin)
            else:
                combo = QComboBox()
                combo.setObjectName(name)
                if param_type == "select" and options:
                    for opt in options:
                        if isinstance(opt, dict):
                            combo.addItem(opt["label"], opt["value"])
                        else:
                            combo.addItem(str(opt), str(opt))
                    if default is not None:
                        idx = combo.findData(str(default))
                        if idx >= 0:
                            combo.setCurrentIndex(idx)
                self._param_layout.addRow(label + ":", combo)
        self._param_panel.setVisible(True)

    # ─── 宏录制 ──────────────────────────────────────────────

    def _toggle_recording(self):
        if self._recorder is not None:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        if self._running:
            self.log_text.append("[录制] 工作流运行中，无法录制")
            return
        if self._backend == "adb":
            self.log_text.append("[录制] ADB 模式暂不支持录制")
            self.statusBar().showMessage("ADB 模式不支持录制")
            return
        if not self._target_window:
            self.log_text.append("[录制] 请先定位窗口")
            self.statusBar().showMessage("未定位窗口 | 请先扫描并定位窗口")
            return
        layout_name = self._layout_manager.get_active_layout_name()
        layout = self._layout_manager.load_layout(layout_name)
        if not layout:
            self.log_text.append(f"[录制] 无法加载布局: {layout_name}")
            return
        w = self._target_window
        if self._capture is None:
            from ..core.desktop import DesktopCapture
            self._capture = DesktopCapture()
        self._capture.set_capture_region(w["left"], w["top"], w["width"], w["height"])
        from ..macros import MacroRecorder
        try:
            self._recorder = MacroRecorder(
                target_window=w, capture=self._capture, layout=layout,
                win_left=w["left"], win_top=w["top"],
            )
            self._recorder.start()
        except Exception as e:
            self._recorder = None
            self.log_text.append(f"[录制] 启动失败: {e}")
            logger.error(f"录制启动失败: {e}")
            return
        self.btn_record.setText("停止录制 (F8)")
        self.btn_record.setStyleSheet(
            "background-color: #f44336; color: white; font-weight: bold; padding: 8px;"
        )
        self.statusBar().showMessage("录制中... | 在游戏窗口内点击/拖拽 | F8 停止")
        self.log_text.append("[录制] 开始录制，在游戏窗口内点击/拖拽，按 F8 结束")

    def _stop_recording(self):
        recorder = self._recorder
        self._recorder = None
        self.btn_record.setText("录制 (F8)")
        self.btn_record.setStyleSheet(
            "background-color: #607D8B; color: white; font-weight: bold; padding: 8px;"
        )
        self.statusBar().showMessage("录制结束")
        if recorder is None:
            return
        dsl = recorder.stop()
        if not dsl.strip():
            self.log_text.append("[录制] 未捕获到有效操作")
            return
        self.log_text.append("[录制] 录制完成，已生成 DSL 语句")
        from .macro_result_dialog import MacroResultDialog
        dialog = MacroResultDialog(dsl, self)
        dialog.exec()

    # ─── 快捷键 + 关闭 ───────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_F9:
            if not self._running:
                self._on_start()
        elif event.key() == Qt.Key.Key_F10:
            self._request_stop()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        if self._running:
            reply = QMessageBox.question(
                self, "工作流运行中",
                "当前有工作流正在运行，关闭程序将终止工作流。\n确定要退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._request_stop()
        if self._recorder is not None:
            try:
                self._recorder.stop()
            except Exception:
                pass
            self._recorder = None
        if self._backend == "adb":
            self._teardown_adb_backend()
        try:
            self._hotkey_listener.stop()
        except Exception:
            pass
        self._overlay.destroy()
        super().closeEvent(event)
