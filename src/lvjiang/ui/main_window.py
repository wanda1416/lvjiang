"""通用主窗口。

包含完整的基础功能：
- 用户管理、场景管理、图库管理、图像识别
- 窗口/设备扫描与定位
- 工作流加载、执行
- 运行日志面板
- 全局热键（仅 F8-F10：F9 执行、F10 停止、F8 脚本录制；定位/连接后方生效）

插件通过 hooks 机制扩展左侧/右侧 Tab 和菜单项。
"""
from __future__ import annotations

from typing import Any

from loguru import logger
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QKeyEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps import get_registry

from ..config import load_user_config
from ..core.layout_manager import LayoutConfigManager
from ..core.session_manager import SessionManager
from ..core.user_config import UserConfigManager
from .capture_ops import CaptureOpsMixin
from .overlay import BorderOverlay
from .run_control import RunControlMixin
from .widgets import TrimmedLogEdit
from .window_ops import WindowOpsMixin


class _LogBridge(QObject):
    """信号桥：将后台线程的日志安全转发到主线程"""
    append_log = pyqtSignal(str)


DEFAULT_TITLE = "律匠 - 通用视觉 RPA 引擎"


def _get_title_with_version() -> str:
    """获取带版本号的窗口标题"""
    try:
        from .._version import __version__
        if __version__ and __version__ != "0.0.0.dev0":
            return f"{DEFAULT_TITLE} v{__version__}"
    except Exception:
        pass
    return DEFAULT_TITLE


class MainWindow(WindowOpsMixin, RunControlMixin, CaptureOpsMixin, QMainWindow):
    """通用主窗口。

    从全局注册表读取扩展点，由插件在启动时注入：
    ``left_tab_builders`` / ``right_tab_builders`` / ``menu_builders``。
    插件页面通过宿主 API（active_user_name / is_running / request_stop /
    append_log / run_workflow_implementation）与信号（automation_state_changed /
    user_changed）与主窗口交互，不直接触碰私有属性。
    """

    # 全局热键信号
    f9_pressed = pyqtSignal()
    f10_pressed = pyqtSignal()
    f8_pressed = pyqtSignal()
    _scrcpy_frame_ready = pyqtSignal(object)
    # 宿主信号：自动化状态（"running" / "not_ready" / "ready"）与用户切换
    automation_state_changed = pyqtSignal(str)
    user_changed = pyqtSignal(str)

    def __init__(self, hooks_list: list[Any] | None = None) -> None:
        super().__init__()
        self._hooks_list = hooks_list or []
        registry = get_registry()

        # 标题
        title = registry.get("window_title") or _get_title_with_version()
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
        self._script_record_dialog = None
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
        # 非 Windows 时返回 None（无桌面投屏后端，仅支持 ADB 模式）
        from ..core.platforms import create_desktop_input
        self._win_input = create_desktop_input(input_sim=self._user_config.input_sim)
        self._input = self._win_input

        # ── 构建 UI ──
        self._setup_menu()
        self._setup_ui()
        self._refresh_run_button()
        self._refresh_user_combo()
        self._refresh_layout_combo()
        self._load_workflow_configs()
        self._restore_daily_config()

        # ── 全局热键（仅 F8-F10；回调内按后端就绪门控，定位/连接后方生效）──
        self.f9_pressed.connect(self._on_f9_start)
        self.f10_pressed.connect(self._request_stop)
        self.f8_pressed.connect(self._on_f8_script_record)
        self._scrcpy_frame_ready.connect(self._on_scrcpy_frame_ui)
        # 启动全局热键（内部先安装 pynput 防护补丁）；
        # macOS 未授权时返回 None，降级为窗口内热键（keyPressEvent 已处理 F8-F10）
        from ..core.platforms import start_global_hotkeys
        self._hotkey_listener = start_global_hotkeys({
            "<f9>": self._on_global_f9,
            "<f10>": self._on_global_f10,
            "<f8>": self._on_global_f8,
        })

        logger.info("主窗口已初始化")

    # ─── 启动时检查更新 ────────────────────────────────────────

    def check_update_on_startup(self):
        """启动时检查更新（窗口显示后调用）"""
        from ..core.update import UpdateChecker, should_prompt_update

        checker = UpdateChecker(self)

        def on_finished(latest_version: str, download_url: str):
            if not should_prompt_update(latest_version):
                return  # 用户已选择跳过此版本

            from .update_dialog import UpdateDialog
            dialog = UpdateDialog(latest_version, download_url, self)
            dialog.exec()

            if dialog.action == UpdateDialog.ACTION_EXIT:
                from PyQt6.QtWidgets import QApplication
                QApplication.quit()

        def on_error(_error_msg: str):
            pass  # 启动时检查失败静默忽略

        checker.finished.connect(on_finished)
        checker.error.connect(on_error)
        checker.start()
        self._startup_update_checker = checker  # 防止被 GC

    # ─── 热键回调 ────────────────────────────────────────────

    def _on_global_f9(self):
        if not self._backend_ready():
            return
        self.f9_pressed.emit()

    def _on_global_f10(self):
        if not self._backend_ready():
            return
        self.f10_pressed.emit()

    def _on_global_f8(self):
        if not self._backend_ready():
            return
        self.f8_pressed.emit()

    def _on_f9_start(self):
        """F9 启动入口（全局热键 / 窗口按键共用）"""
        if not self._running:
            self._on_start()

    # ─── 菜单栏 ──────────────────────────────────────────────

    def _setup_menu(self):
        menubar = self.menuBar()
        menubar.setStyleSheet("""
            QMenuBar::item { padding: 6px 16px; }
            QMenuBar::item:selected { background: #d4d4d4; }
            QMenu::item { padding: 8px 32px; }
            QMenu::item:selected { background: #0078d4; color: white; }
        """)

        # ── 通用 ──
        settings_menu = menubar.addMenu("通用")

        settings_mgmt = QAction("配置管理", self)
        settings_mgmt.triggered.connect(self._open_settings_manager)
        settings_menu.addAction(settings_mgmt)

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

        # ── 工具 ──
        tools_menu = menubar.addMenu("工具")

        ocr_action = QAction("图像识别", self)
        ocr_action.triggered.connect(self._open_ocr_dialog)
        tools_menu.addAction(ocr_action)

        script_record = QAction("脚本录制", self)
        script_record.triggered.connect(self._open_script_record)
        tools_menu.addAction(script_record)

        script_config = QAction("脚本配置", self)
        script_config.triggered.connect(self._open_script_config)
        tools_menu.addAction(script_config)

        batch_settings = QAction("批量配置", self)
        batch_settings.triggered.connect(self._open_batch_config)
        tools_menu.addAction(batch_settings)

        # ── 插件菜单（一个插件一个菜单，插在帮助之前）──
        registry = get_registry()
        for builder in registry.get("menu_builders", []):
            try:
                builder(self, menubar)
            except Exception:  # noqa: BLE001
                logger.exception("menu builder 执行失败")

        # ── 帮助 ──
        help_menu = menubar.addMenu("帮助")

        check_update = QAction("检查更新", self)
        check_update.triggered.connect(self._check_update)
        help_menu.addAction(check_update)

        docs = QAction("文档", self)
        docs.triggered.connect(self._open_docs)
        help_menu.addAction(docs)

        feedback = QAction("反馈", self)
        feedback.triggered.connect(self._open_feedback)
        help_menu.addAction(feedback)

        help_menu.addSeparator()

        about = QAction("关于", self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)

    # ─── 对话框 ──────────────────────────────────────────────

    def _open_ocr_dialog(self):
        from .ocr_dialog import OCRDialog
        dialog = OCRDialog(self, refresh_callback=self._refresh_capture)
        dialog.exec()

    def _open_script_record(self):
        """打开脚本录制对话框（录制生命周期由对话框自管）"""
        from .script_record_dialog import ScriptRecordDialog
        dialog = ScriptRecordDialog(self)
        self._script_record_dialog = dialog
        try:
            dialog.exec()
        finally:
            self._script_record_dialog = None

    def _on_f8_script_record(self):
        """F8：对话框已打开则切换录制，未打开则打开脚本录制对话框"""
        dialog = self._script_record_dialog
        if dialog is not None and dialog.isVisible():
            dialog.toggle_recording()
        else:
            self._open_script_record()

    def _open_script_config(self):
        """打开脚本配置对话框；保存后刷新日常页脚本下拉。"""
        from .script_config_dialog import ScriptConfigDialog
        dialog = ScriptConfigDialog(self)
        if dialog.exec():
            self._load_workflow_configs()

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
        dialog = ReferenceManagerDialog(parent=self, screenshot_callback=self._refresh_capture)
        dialog.exec()
        if dialog.data_changed:
            from ..workflows.base import BaseWorkflow
            if BaseWorkflow._shared_material_recognizer is not None:
                BaseWorkflow._shared_material_recognizer.reload()
                self.statusBar().showMessage("图库已刷新", 3000)

    def _show_about(self):
        from .about_dialog import AboutDialog
        dialog = AboutDialog(self)
        dialog.exec()

    def _check_update(self):
        """直接检查更新（帮助菜单 → 检查更新）"""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtWidgets import QMessageBox

        from ..core.update import UpdateChecker, get_version, is_newer_version

        checker = UpdateChecker(self)

        def on_finished(latest_version: str, download_url: str):
            current_version = get_version()

            if is_newer_version(latest_version, current_version):
                result = QMessageBox.information(
                    self,
                    "发现新版本",
                    f"发现新版本 v{latest_version}\n"
                    f"当前版本: v{current_version}\n\n"
                    "是否前往下载？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if result == QMessageBox.StandardButton.Yes:
                    QDesktopServices.openUrl(QUrl(download_url))
            else:
                QMessageBox.information(
                    self,
                    "已是最新版本",
                    f"当前版本 v{current_version} 已是最新版本",
                )

        def on_error(error_msg: str):
            QMessageBox.warning(self, "检查更新失败", error_msg)

        checker.finished.connect(on_finished)
        checker.error.connect(on_error)
        checker.start()
        self._update_checker = checker  # 防止被 GC

    def _open_docs(self):
        """打开 GitHub 文档"""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        from .about_dialog import GITHUB_REPO
        QDesktopServices.openUrl(QUrl(f"https://github.com/{GITHUB_REPO}/blob/master/docs/user-guide.md"))

    def _open_feedback(self):
        """打开 GitHub Issue 反馈页面"""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        from .about_dialog import GITHUB_REPO
        QDesktopServices.openUrl(QUrl(f"https://github.com/{GITHUB_REPO}/issues"))

    def _open_user_manager(self):
        from .user_manager_dialog import UserManagerDialog
        dialog = UserManagerDialog(self._user_manager, self)
        dialog.exec()
        self._refresh_user_combo()

    def _open_settings_manager(self):
        from .settings_dialog import SettingsDialog
        dialog = SettingsDialog(self)
        if dialog.exec():
            # 保存后重新加载配置；已创建的输入后端延迟参数在下次创建时生效
            self._user_config = load_user_config()
            self.statusBar().showMessage("配置已保存", 3000)

    def _on_toggle_preview(self, checked: bool):
        self.preview_container.setVisible(not checked)
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
        from ..core.platforms import DESKTOP_BACKEND_AVAILABLE
        if not DESKTOP_BACKEND_AVAILABLE:
            # 非 Windows 仅支持 ADB 模式，隐藏窗口投屏入口
            self.btn_scan_window.setVisible(False)
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

        # === 截屏预览区（左：实时画面 / 右：采集控制面板）===
        self.preview_container = QWidget()
        self.preview_container.setFixedHeight(320)
        preview_layout = QHBoxLayout(self.preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(6)

        self.preview_label = QLabel("定位窗口后自动截屏")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("background-color: #2b2b2b; color: #888; font-size: 14px;")
        preview_layout.addWidget(self.preview_label, stretch=1)

        preview_layout.addWidget(self._build_capture_panel())

        self.preview_container.setVisible(False)
        main_layout.addWidget(self.preview_container)

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

        # 初始比例：左 1/3、右 2/3
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([333, 667])
        self._main_splitter = splitter
        main_layout.addWidget(splitter, stretch=1)

        # === 底部状态栏 ===
        self.statusBar().showMessage("就绪 | F9 开始 | F10 停止 | F8 脚本录制")
        self.adjustSize()
        self.setMinimumHeight(self.height())
        self._restore_ui_state()
        self._setup_log_redirect()

    def _build_left_tabs(self):
        """构建左侧 Tab（通用：日常），再追加插件注入的 Tab。"""
        # ── Tab 1: 日常 ──
        daily_scroll = QScrollArea()
        daily_scroll.setWidgetResizable(True)
        daily_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        daily_panel = QWidget()
        daily_layout = QVBoxLayout(daily_panel)
        daily_layout.setContentsMargins(8, 8, 8, 8)
        daily_layout.setSpacing(8)

        # 开始/停止按钮（第一行）
        self.btn_run_workflow = QPushButton("开始执行 (F9)")
        self.btn_run_workflow.clicked.connect(self._on_run_workflow)
        self.btn_run_workflow.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px; font-size: 13px; margin: 4px 0;"
        )
        daily_layout.addWidget(self.btn_run_workflow)

        wf_group = QGroupBox("脚本")
        wf_layout = QHBoxLayout(wf_group)
        self.workflow_combo = QComboBox()
        self.workflow_combo.setMinimumWidth(150)
        self.workflow_combo.currentIndexChanged.connect(self._on_workflow_combo_changed)
        wf_layout.addWidget(self.workflow_combo, stretch=1)
        self.btn_load_workflow = QPushButton("加载")
        self.btn_load_workflow.setFixedWidth(64)
        self.btn_load_workflow.clicked.connect(self._on_load_workflow)
        self.btn_load_workflow.setStyleSheet(
            "background-color: #607D8B; color: white; font-weight: bold; padding: 6px;"
        )
        wf_layout.addWidget(self.btn_load_workflow)
        daily_layout.addWidget(wf_group)

        self._param_panel = QGroupBox("参数设置")
        self._param_layout = QFormLayout(self._param_panel)
        self._param_panel.setVisible(False)
        daily_layout.addWidget(self._param_panel)

        daily_layout.addStretch()
        daily_scroll.setWidget(daily_panel)
        self._left_tabs.addTab(daily_scroll, "日常")

        # ── Tab 2: 批量 ──
        from .batch import BatchTab
        self._batch_tab = BatchTab(host=self)
        self._left_tabs.addTab(self._batch_tab, "批量")

        # ── 插件注入的左侧 Tab（按 -reg 顺序追加）──
        self._add_plugin_tabs(self._left_tabs, "left_tab_builders")

    def _build_right_tabs(self):
        """构建右侧 Tab（通用：运行日志），再追加插件注入的 Tab。"""
        self.log_text = TrimmedLogEdit()
        self.log_text.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        self.tabs.addTab(self.log_text, "运行日志")

        # ── 插件注入的右侧 Tab（按 -reg 顺序追加）──
        self._add_plugin_tabs(self.tabs, "right_tab_builders")

    def _add_plugin_tabs(self, tab_widget: QTabWidget, registry_key: str):
        """消费注册表中的插件 Tab builder；单个失败只记日志不中断。"""
        for label, builder in get_registry().get(registry_key, []):
            try:
                tab_widget.addTab(builder(self), label)
            except Exception:  # noqa: BLE001
                logger.exception(f"插件 Tab「{label}」构建失败")

    # ─── 宿主 API（供插件页面使用）──────────────────────

    def active_user_name(self) -> str:
        """当前激活用户名（无用户时返回空串）"""
        return self._user_manager.get_active_user_name() or ""

    @property
    def is_running(self) -> bool:
        """当前是否有自动化在运行"""
        return self._running

    def request_stop(self):
        """请求停止当前自动化（等价 F10）"""
        self._request_stop()

    def append_log(self, text: str):
        """向运行日志面板追加一行消息"""
        self.log_text.append(text)

    # ─── 批处理执行 ───────────────────────────────────────

    def run_batch(self, enabled_rows, scripts) -> bool:
        """启动批量执行，返回是否成功

        enabled_rows: list[tuple[int, dict]] - [(index, row_data), ...]
        """
        if not self._backend_ready():
            if self._backend == "adb":
                self.log_text.append("[错误] 请先连接设备")
            else:
                self.log_text.append("[错误] 请先定位窗口")
            return False

        if not self._begin_automation("批量执行"):
            return False

        layout_name = self._layout_manager.get_active_layout_name()
        layout = self._layout_manager.load_layout(layout_name)
        if not layout:
            self.log_text.append(f"[错误] 无法加载布局: {layout_name}")
            self._end_automation("批量执行")
            return False

        if self._backend == "adb":
            window_left, window_top = 0, 0
        else:
            if self._input.background_mode and self._target_window:
                self._input.target_hwnd = self._target_window["hwnd"]
            window_left = self._target_window["left"]
            window_top = self._target_window["top"]

        from ..core.batch_config import load_batch_config
        from .batch import BatchContext, BatchWorker

        ctx = BatchContext(
            capture=self._capture,
            ocr=self._ocr,
            input_ctrl=self._input,
            layout=layout,
            input_sim=self._user_config.input_sim,
            delay_params=self._user_config.delay_params,
            window_left=window_left,
            window_top=window_top,
        )

        # 获取当前配置
        cfg = load_batch_config()
        config = cfg.get_active()
        if not config:
            self.log_text.append("[错误] 暂无配置，请先通过 工具 → 批量配置 添加")
            self._end_automation("批量执行")
            return False

        worker = BatchWorker(
            enabled_rows=enabled_rows,
            scripts=scripts,
            config=config,
            ctx=ctx,
            user_manager=self._user_manager,
            session_manager=self._session_manager,
            stop_check=self._is_stopped,
        )

        # 信号连接：进度 → batch_tab，日志 → log_text，用户切换 → 刷新用户下拉
        worker.progress.connect(self._batch_tab.update_progress)
        worker.log.connect(self.log_text.append)
        worker.user_changed.connect(lambda _: self._refresh_user_combo())
        worker.finished_all.connect(self._batch_tab.on_batch_finished)
        worker.finished_all.connect(
            lambda _: self._end_automation("批量执行")
        )

        self._current_worker = worker
        worker.start()
        return True

    def _open_batch_config(self):
        """工具菜单 → 批量配置：打开配置对话框"""
        from .batch import BatchConfigDialog
        dlg = BatchConfigDialog(self)
        if dlg.exec():
            # 保存后刷新批量 Tab 的条目概览和脚本勾选
            self._batch_tab.refresh_config()

    # ─── UI 状态持久化（session.json ui_state 节点）────────

    def _restore_ui_state(self):
        """启动时恢复窗口大小与左右分栏比例，免去每次手动拉伸"""
        import json

        from ..constants import SESSION_PATH
        if not SESSION_PATH.exists():
            return
        try:
            state = json.loads(
                SESSION_PATH.read_text(encoding="utf-8")).get("ui_state", {})
        except Exception:
            return
        size = state.get("window_size")
        if isinstance(size, list) and len(size) == 2:
            self.resize(int(size[0]), int(size[1]))
        sizes = state.get("splitter_sizes")
        if isinstance(sizes, list) and len(sizes) == 2 and all(s > 0 for s in sizes):
            self._main_splitter.setSizes([int(s) for s in sizes])

    def _save_ui_state(self):
        """退出时统一写入 ui_state（保留 session.json 其他字段）"""
        import json

        from ..constants import SESSION_CONFIG_DIR, SESSION_PATH
        data = {}
        if SESSION_PATH.exists():
            try:
                data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        # 仅更新主窗口 UI 状态，保留其他组件的 ui_state（如 scene_editor_*）
        ui = data.setdefault("ui_state", {})
        ui["window_size"] = [self.width(), self.height()]
        ui["splitter_sizes"] = self._main_splitter.sizes()
        try:
            SESSION_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            SESSION_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"保存 UI 状态失败: {e}")

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

    # ─── 日常页配置持久化（session.json daily 节点）───────

    def _on_workflow_combo_changed(self, index: int):
        """脚本下拉切换：先保存旧脚本参数，重建参数面板，再保存新状态"""
        # 面板仍显示旧脚本控件，用 _displayed_script_id 定位旧配置
        self._save_displayed_params()
        self._rebuild_param_panel()
        # 更新追踪为当前脚本
        flow_cfg = self._get_selected_flow_config()
        self._displayed_script_id = flow_cfg["id"] if flow_cfg else None
        self._save_daily_config()

    def _save_displayed_params(self):
        """将当前参数面板的值写入 _displayed_script_id 对应的配置项"""
        sid = getattr(self, '_displayed_script_id', None)
        if not sid or not self._param_panel or not self._param_panel.isVisible():
            return
        # 找到对应配置项，临时用 _collect_flow_params 的逻辑从面板搜集值
        target_cfg = next((c for c in self._workflow_configs if c["id"] == sid), None)
        if not target_cfg:
            return
        params = {}
        from PyQt6.QtWidgets import QCheckBox, QComboBox, QSpinBox
        for param_def in target_cfg.get("parameters", []):
            name = param_def["name"]
            widget = self._param_panel.findChild(QSpinBox, name)
            if widget is not None:
                params[name] = str(widget.value())
                continue
            widget = self._param_panel.findChild(QCheckBox, name)
            if widget is not None:
                params[name] = widget.isChecked()
                continue
            widget = self._param_panel.findChild(QComboBox, name)
            if widget is not None:
                data = widget.currentData()
                params[name] = data if data is not None else widget.currentText()
        target_cfg["_saved_params"] = params

    def _save_daily_config(self):
        """保存日常页脚本选择与参数到 session.json 的 daily 节点"""
        import json as _json

        from ..constants import SESSION_CONFIG_DIR, SESSION_PATH

        flow_cfg = self._get_selected_flow_config()
        if not flow_cfg:
            return

        # 汇总各脚本已保存的参数
        params_map: dict[str, dict] = {}
        for cfg in self._workflow_configs:
            saved = cfg.get("_saved_params")
            if saved:
                params_map[cfg["id"]] = saved

        daily = {
            "workflow_id": flow_cfg["id"],
            "params": params_map,
        }

        data = {}
        if SESSION_PATH.exists():
            try:
                data = _json.loads(SESSION_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        data["daily"] = daily
        try:
            SESSION_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            SESSION_PATH.write_text(
                _json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"保存日常配置失败: {e}")

    def _restore_daily_config(self):
        """启动时恢复日常页脚本选择与参数"""
        import json as _json

        from ..constants import SESSION_PATH

        # 加载 combo 时 block 了信号，参数面板始终为空，必须手动构建
        workflow_id = None
        params_map = {}

        if SESSION_PATH.exists():
            try:
                data = _json.loads(SESSION_PATH.read_text(encoding="utf-8"))
                daily = data.get("daily", {})
                workflow_id = daily.get("workflow_id")
                params_map = daily.get("params", {})
            except Exception:
                pass

        # 将保存的参数回写到 _workflow_configs 供 _rebuild_param_panel 使用
        for cfg in self._workflow_configs:
            saved = params_map.get(cfg["id"])
            if saved:
                cfg["_saved_params"] = saved

        # 选中上次使用的脚本
        if workflow_id:
            idx = self.workflow_combo.findData(workflow_id)
            if idx >= 0:
                self.workflow_combo.blockSignals(True)
                self.workflow_combo.setCurrentIndex(idx)
                self.workflow_combo.blockSignals(False)

        # 统一设置追踪变量并构建参数面板
        flow_cfg = self._get_selected_flow_config()
        self._displayed_script_id = flow_cfg["id"] if flow_cfg else None
        self._rebuild_param_panel()

    def _rebuild_param_panel(self):
        while self._param_layout.rowCount() > 0:
            self._param_layout.removeRow(0)
        flow_cfg = self._get_selected_flow_config()
        params = flow_cfg.get("parameters", []) if flow_cfg else []
        if not params:
            self._param_panel.setVisible(False)
            return
        saved = flow_cfg.get("_saved_params", {}) if flow_cfg else {}
        for param_def in params:
            name = param_def["name"]
            label = param_def.get("label", name)
            param_type = param_def.get("type", "select")
            # 已保存值优先于定义默认值
            default = saved.get(name, param_def.get("default"))
            options = param_def.get("options", [])
            if param_type == "number":
                spin = QSpinBox()
                spin.setObjectName(name)
                spin.setRange(param_def.get("min", 1), param_def.get("max", 9999))
                spin.setValue(int(default) if default is not None else 1)
                self._param_layout.addRow(label + ":", spin)
            elif param_type == "bool":
                chk = QCheckBox()
                chk.setObjectName(name)
                if isinstance(default, str):
                    chk.setChecked(default.lower() in ("true", "1", "yes", "on"))
                else:
                    chk.setChecked(bool(default))
                self._param_layout.addRow(label + ":", chk)
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

    # ─── 快捷键 + 关闭 ───────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_F9:
            self._on_f9_start()
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
        # 全局热键最先停：stop() 只投递停止信号不等线程结束，必须 join
        # 等钩子线程完成 UnhookWindowsHookEx；越早停，后续清理期间钩子
        # 回调踩空（退出时 TypeError 噪声乃至 access violation）的窗口越小
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
                self._hotkey_listener.join(3.0)
                if self._hotkey_listener.is_alive():
                    logger.warning("热键监听线程 3 秒内未退出，钩子可能未卸载")
            except Exception:
                pass
        self._save_displayed_params()
        self._save_daily_config()
        self._save_ui_state()
        # 录屏进行中/待保存时自动转正保存，不丢数据
        self._abort_screen_record("关闭程序")
        if self._backend == "adb":
            self._teardown_adb_backend()
        else:
            self._stop_capture_backend()
        self._overlay.destroy()
        super().closeEvent(event)
