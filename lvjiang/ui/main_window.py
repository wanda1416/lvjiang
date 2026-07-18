"""PyQt6 主窗口 - 框架、菜单、UI 构建"""

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QGroupBox, QTextEdit,
    QTabWidget, QSplitter, QMessageBox, QFormLayout, QScrollArea,
    QSpinBox, QCheckBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtGui import QKeyEvent, QAction
from loguru import logger

from .widgets import TrimmedLogEdit

from pynput import keyboard as pynput_keyboard

from .overlay import BorderOverlay
from .window_ops import WindowOpsMixin
from .run_control import RunControlMixin
from ..config import load_user_config
from ..core.user_config import UserConfigManager
from ..core.region_config import LayoutConfigManager


class _LogBridge(QObject):
    """信号桥：将后台线程的日志安全转发到主线程（pyqtSignal 必须定义在模块级类上）"""
    append_log = pyqtSignal(str)


class MainWindow(WindowOpsMixin, RunControlMixin, QMainWindow):
    """律匠主窗口"""

    # 全局热键 F10 信号（跨线程，pynput 监听线程 emit，主线程处理）
    f10_pressed = pyqtSignal()
    # 全局热键 F8 信号（录制开始/停止切换）
    f8_pressed = pyqtSignal()
    # scrcpy 帧信号（解码线程 emit，主线程处理，传递 BGR numpy 数组）
    _scrcpy_frame_ready = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("律匠 - 燕云十六声装备调律工具 v0.1.0")
        self.setMinimumSize(1000, 700)

        # 投屏窗口信息（windows 后端使用）
        self._target_window = None  # dict: {title, hwnd, left, top, width, height}
        self._scanned_windows = []  # 扫描到的窗口/设备列表

        # ADB 设备后端信息（adb 后端使用）
        self._device = None          # AdbDevice（连接设备后创建）
        self._device_ready = False   # ADB 设备是否就绪

        # 运行状态
        self._running = False
        self._stop_requested = False
        self._current_worker = None  # 当前工作流线程，为 None 表示无自动化在运行

        # 边框覆盖层（定位/运行状态指示）
        self._overlay = BorderOverlay()

        # 宏录制器（录制中为 MacroRecorder 实例，否则为 None）
        self._recorder = None

        # 截屏器（定位后初始化，后续自动化复用）
        self._capture = None
        self._last_capture = None  # 最近一次截屏（numpy BGR）

        # 区域布局（定位后由场景编辑器设置）
        self._region_layout = None

        # 用户管理
        self._user_manager = UserConfigManager()

        # 布局管理
        self._layout_manager = LayoutConfigManager()

        # 用户配置（延迟参数等）
        self._user_config = load_user_config()
        # 设备后端运行态：windows（投屏窗口）| adb（adb screencap + adb shell input 直连手机）
        # 由用户在界面上「扫描窗口」/「扫描设备」动态切换，config.backend 仅作初始默认
        self._backend = (self._user_config.backend or "windows").lower()

        # OCR 引擎（懒加载）
        from ..core.ocr import OCREngine
        self._ocr = OCREngine()

        # 输入控制器：Windows 投屏常驻 PostMessageInput；ADB 连接设备后切换为 AdbInput
        from ..core.desktop import create_input_backend as _create_desktop_input
        self._win_input = _create_desktop_input(mode="post", delay_config=self._user_config.delay)
        self._input = self._win_input

        self._setup_menu()
        self._setup_ui()
        self._refresh_run_button()  # 根据后端就绪状态设置按钮初始样式
        self._refresh_user_combo()
        self._refresh_layout_combo()
        self._load_workflow_configs()

        # 全局热键监听（跨窗口焦点，自动化/录制时游戏窗口占焦点也能响应）
        # F10 停止工作流；F8 录制开始/停止切换
        self.f10_pressed.connect(self._request_stop)
        self.f8_pressed.connect(self._toggle_recording)
        self._scrcpy_frame_ready.connect(self._on_scrcpy_frame_ui)
        self._hotkey_listener = pynput_keyboard.GlobalHotKeys({
            "<f10>": self._on_global_f10,
            "<f8>": self._on_global_f8,
        })
        self._hotkey_listener.start()

        logger.info("主窗口已初始化")

    def _on_global_f10(self):
        """pynput 监听线程回调，转发到主线程处理"""
        self.f10_pressed.emit()

    def _on_global_f8(self):
        """pynput 监听线程回调（录制切换），转发到主线程处理"""
        self.f8_pressed.emit()

    # ─── 菜单栏 ────────────────────────────────────────────

    def _setup_menu(self):
        """构建顶部菜单栏"""
        menubar = self.menuBar()

        # 加宽菜单项和子项间距
        menubar.setStyleSheet("""
            QMenuBar::item { padding: 6px 16px; }
            QMenuBar::item:selected { background: #d4d4d4; }
            QMenu::item { padding: 8px 32px; }
            QMenu::item:selected { background: #0078d4; color: white; }
        """)

        # ── 设置 ──
        settings_menu = menubar.addMenu("设置")

        user_mgmt_action = QAction("用户管理", self)
        user_mgmt_action.setShortcut("F2")
        user_mgmt_action.triggered.connect(self._open_user_manager)
        settings_menu.addAction(user_mgmt_action)

        settings_menu.addSeparator()

        scene_editor_action = QAction("场景管理", self)
        scene_editor_action.setShortcut("F3")
        scene_editor_action.triggered.connect(self._open_scene_editor)
        settings_menu.addAction(scene_editor_action)

        material_manager_action = QAction("材料管理", self)
        material_manager_action.setShortcut("F4")
        material_manager_action.triggered.connect(self._open_material_manager)
        settings_menu.addAction(material_manager_action)

        # ── 工具 ──
        tools_menu = menubar.addMenu("工具")

        grad_calc_action = QAction("毕业率计算器", self)
        grad_calc_action.setEnabled(False)
        tools_menu.addAction(grad_calc_action)

        tools_menu.addSeparator()

        ocr_test_action = QAction("图像识别测试", self)
        ocr_test_action.triggered.connect(self._open_ocr_test)
        tools_menu.addAction(ocr_test_action)

        # ── 帮助 ──
        help_menu = menubar.addMenu("帮助")

        about_action = QAction("关于", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    # ─── 对话框打开 ────────────────────────────────────────

    def _open_ocr_test(self):
        """打开 OCR 测试对话框"""
        from .ocr_test_dialog import OCRTestDialog
        dialog = OCRTestDialog(self)
        dialog.exec()

    def _open_scene_editor(self):
        """打开场景编辑器（无需截图，按场景加载）"""
        from .scene_editor import SceneEditorDialog
        dialog = SceneEditorDialog(
            layout_manager=self._layout_manager,
            refresh_callback=self._refresh_capture,
            parent=self,
        )
        dialog.exec()
        self._refresh_layout_combo()

    def _open_material_manager(self):
        """打开材料管理对话框"""
        from .material_manager import MaterialManagerDialog
        dialog = MaterialManagerDialog(parent=self)
        dialog.exec()
        # 如果有数据变动，刷新共享的材料识别器
        if dialog.data_changed:
            from ..workflows.base import BaseWorkflow
            if BaseWorkflow._shared_material_recognizer is not None:
                BaseWorkflow._shared_material_recognizer.reload()
                self.statusBar().showMessage("材料库已刷新", 3000)

    def _show_about(self):
        """显示关于对话框"""
        QMessageBox.about(
            self,
            "关于律匠",
            "<h3>律匠 v0.1.0</h3>"
            "<p>燕云十六声装备调律辅助工具</p>"
            "<p>功能：窗口定位截屏 → 区域标注 → OCR识别 → 装备评估</p>"
            "<hr>"
            "<p style='color: gray;'>基于 PyQt6 + RapidOCR</p>",
        )

    def _open_user_manager(self):
        """打开用户管理对话框"""
        from .user_manager_dialog import UserManagerDialog
        dialog = UserManagerDialog(self._user_manager, self)
        dialog.exec()
        self._refresh_user_combo()

    def _on_toggle_preview(self, checked: bool):
        """切换预览区域的显示/隐藏"""
        self.preview_label.setVisible(not checked)
        self.btn_hide_window.setText("显示预览" if checked else "隐藏预览")

    # ─── UI 构建 ───────────────────────────────────────────

    def _setup_ui(self):
        """构建界面"""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # === 顶部：当前用户 + 当前布局 ===
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
        self.btn_scan_window.setToolTip("Windows 投屏窗口模式：扫描并定位桌面窗口")
        self.btn_scan_window.clicked.connect(self._on_scan_window)
        row1.addWidget(self.btn_scan_window)

        self.btn_scan_device = QPushButton("扫描设备")
        self.btn_scan_device.setFixedWidth(90)
        self.btn_scan_device.setToolTip("ADB 设备模式：扫描并连接 Android 手机")
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
        self.btn_hide_window.setChecked(True)  # 默认隐藏预览
        self.btn_hide_window.setToolTip("隐藏/显示实时预览区域")
        self.btn_hide_window.clicked.connect(self._on_toggle_preview)
        row1.addWidget(self.btn_hide_window)

        window_main_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.lbl_window_info = QLabel("未选择窗口")
        self.lbl_window_info.setStyleSheet("color: gray;")
        row2.addWidget(self.lbl_window_info)
        row2.addStretch()

        self.chk_bg_mode = QCheckBox("后台模式")
        self.chk_bg_mode.setToolTip("启用后鼠标操作不会移动光标，通过 PostMessage 直接向目标窗口发送鼠标事件")
        self.chk_bg_mode.setVisible(False)  # 点击扫描窗口后才显示
        self.chk_bg_mode.stateChanged.connect(self._on_bg_mode_changed)
        row2.addWidget(self.chk_bg_mode)

        self.chk_scrcpy = QCheckBox("流式截图")
        self.chk_scrcpy.setToolTip("启用后使用 scrcpy H.264 视频流截图（低延迟），关闭则使用 adb screencap")
        self.chk_scrcpy.setVisible(False)  # 点击扫描设备后才显示
        self.chk_scrcpy.stateChanged.connect(self._on_capture_method_changed)
        row2.addWidget(self.chk_scrcpy)

        window_main_layout.addLayout(row2)

        # 按初始默认后端设置定位按钮文案与后台开关可见性（两种扫描均保留）
        self._apply_backend_ui(self._backend)

        main_layout.addWidget(window_group)

        # === 截屏预览区（默认隐藏） ===
        self.preview_label = QLabel("定位窗口后自动截屏")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setFixedHeight(320)
        self.preview_label.setStyleSheet(
            "background-color: #2b2b2b; color: #888; font-size: 14px;"
        )
        self.preview_label.setVisible(False)
        main_layout.addWidget(self.preview_label)

        # === 中部：左右分栏 ===
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：配置区（可滚动）
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(4, 4, 4, 4)

        # 工作流选择
        wf_group = QGroupBox("工作流")
        wf_layout = QVBoxLayout(wf_group)
        self.workflow_combo = QComboBox()
        self.workflow_combo.setMinimumWidth(200)
        self.workflow_combo.currentIndexChanged.connect(self._rebuild_param_panel)
        wf_layout.addWidget(self.workflow_combo)
        left_layout.addWidget(wf_group)

        # 开始执行按钮
        self.btn_run_workflow = QPushButton("开始执行 (F9)")
        self.btn_run_workflow.clicked.connect(self._on_run_workflow)
        self.btn_run_workflow.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px;"
        )
        left_layout.addWidget(self.btn_run_workflow)

        # 录制 + 加载工作流（各占一半）
        tools_row = QHBoxLayout()

        self.btn_record = QPushButton("录制 (F8)")
        self.btn_record.setToolTip("在游戏窗口内点击/拖拽录制操作，生成可复用的 DSL 语句")
        self.btn_record.clicked.connect(self._toggle_recording)
        self.btn_record.setStyleSheet(
            "background-color: #607D8B; color: white; font-weight: bold; padding: 8px;"
        )
        tools_row.addWidget(self.btn_record)

        self.btn_load_workflow = QPushButton("加载工作流")
        self.btn_load_workflow.setToolTip("打开任意 .wf 文件并加入下拉列表（临时项，打开新文件会覆盖）")
        self.btn_load_workflow.clicked.connect(self._on_load_workflow)
        self.btn_load_workflow.setStyleSheet(
            "background-color: #607D8B; color: white; font-weight: bold; padding: 8px;"
        )
        tools_row.addWidget(self.btn_load_workflow)

        left_layout.addLayout(tools_row)

        # 工作流参数面板（动态生成）
        self._param_panel = QGroupBox("参数设置")
        self._param_layout = QFormLayout(self._param_panel)
        self._param_panel.setVisible(False)
        left_layout.addWidget(self._param_panel)

        left_layout.addStretch()

        left_scroll.setWidget(left_panel)

        # 右侧：日志/预览区
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        self.tabs = QTabWidget()

        self.log_text = TrimmedLogEdit()
        self.log_text.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        self.tabs.addTab(self.log_text, "运行日志")

        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.tabs.addTab(self.status_text, "装备状态")

        right_layout.addWidget(self.tabs)

        splitter.addWidget(left_scroll)
        splitter.addWidget(right_panel)
        splitter.setSizes([250, 750])

        main_layout.addWidget(splitter, stretch=1)

        # === 底部状态栏 ===
        self.statusBar().showMessage("就绪 | F9 开始 | F10 停止 | F8 录制")

        # 锁定窗口最小尺寸，防止 checkbox 显隐时布局重算导致窗口大小变化
        self.adjustSize()
        self.setMinimumHeight(self.height())

        self._setup_log_redirect()

    def _setup_log_redirect(self):
        """将 loguru 日志输出到 GUI 日志面板（线程安全）"""
        self._log_bridge = _LogBridge(self)
        self._log_bridge.append_log.connect(self.log_text.append)  # 跨线程自动排队到主线程

        class QtSink:
            def __init__(self, bridge):
                self._bridge = bridge

            def write(self, message):
                self._bridge.append_log.emit(message.strip())

        sink = QtSink(self._log_bridge)
        logger.add(sink, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}")

    def _rebuild_param_panel(self):
        """根据当前选中工作流的 parameters 声明重建参数面板"""
        # 清空现有控件
        while self._param_layout.rowCount() > 0:
            self._param_layout.removeRow(0)

        flow_cfg = self._get_selected_flow_config()
        params = flow_cfg.get("parameters", []) if flow_cfg else []

        if not params:
            self._param_panel.setVisible(False)
            return

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
                            # { value: "bag_1_1", label: "位置 1" }
                            combo.addItem(opt["label"], opt["value"])
                        else:
                            # 简单字符串
                            combo.addItem(str(opt), str(opt))

                    # 设置默认值
                    if default is not None:
                        idx = combo.findData(str(default))
                        if idx >= 0:
                            combo.setCurrentIndex(idx)

                self._param_layout.addRow(label + ":", combo)

        self._param_panel.setVisible(True)

    # ─── 宏录制 ────────────────────────────────────────────

    def _toggle_recording(self):
        """录制开关（按钮 / F8 全局热键统一入口）"""
        if self._recorder is not None:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        """开始录制鼠标操作"""
        if self._running:
            self.log_text.append("[录制] 工作流运行中，无法录制")
            return
        if self._backend == "adb":
            self.log_text.append("[录制] ADB 模式暂不支持录制（无投屏窗口）")
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

        # 确保截屏器已就绪并附着到目标窗口
        w = self._target_window
        if self._capture is None:
            from ..core.desktop import DesktopCapture
            self._capture = DesktopCapture()
        self._capture.set_capture_region(w["left"], w["top"], w["width"], w["height"])

        from ..macros import MacroRecorder
        try:
            self._recorder = MacroRecorder(
                target_window=w,
                capture=self._capture,
                layout=layout,
                win_left=w["left"],
                win_top=w["top"],
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
        """停止录制并展示生成的 DSL 结果"""
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

    # ─── 快捷键 + 关闭 ─────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        """全局快捷键处理（仅在主窗口有焦点时生效，F10 主要靠全局热键）"""
        if event.key() == Qt.Key.Key_F9:
            if not self._running:
                self._on_start()
        elif event.key() == Qt.Key.Key_F10:
            self._request_stop()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        """关闭主窗口时清理热键监听与原生覆盖层。

        若工作流正在运行，先弹确认框，避免误关终止自动化。
        """
        if self._running:
            reply = QMessageBox.question(
                self,
                "工作流运行中",
                "当前有工作流正在运行，关闭程序将终止工作流。\n确定要退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            # 用户确认退出：请求停止工作流
            self._request_stop()

        # 停止未结束的录制
        if self._recorder is not None:
            try:
                self._recorder.stop()
            except Exception:
                pass
            self._recorder = None

        # 清理 ADB 后端资源
        if self._backend == "adb":
            self._teardown_adb_backend()

        try:
            self._hotkey_listener.stop()
        except Exception:
            pass
        self._overlay.destroy()
        super().closeEvent(event)
