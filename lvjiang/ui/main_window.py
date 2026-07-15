"""PyQt6 主窗口 - 框架、菜单、UI 构建"""

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QGroupBox, QTextEdit,
    QTabWidget, QSplitter, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QAction
from loguru import logger

from pynput import keyboard as pynput_keyboard

from .overlay import BorderOverlay
from .window_ops import WindowOpsMixin
from .run_control import RunControlMixin
from ..config import load_user_config
from ..core.user_config import UserConfigManager
from ..core.region_config import LayoutConfigManager


class MainWindow(WindowOpsMixin, RunControlMixin, QMainWindow):
    """律匠主窗口"""

    # 全局热键 F10 信号（跨线程，pynput 监听线程 emit，主线程处理）
    f10_pressed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("律匠 - 燕云十六声装备调律工具 v0.1.0")
        self.setMinimumSize(1000, 700)

        # 投屏窗口信息
        self._target_window = None  # dict: {title, hwnd, left, top, width, height}
        self._scanned_windows = []  # 扫描到的窗口列表

        # 运行状态
        self._running = False
        self._stop_requested = False
        self._current_worker = None  # 当前工作流线程，为 None 表示无自动化在运行

        # 边框覆盖层（定位/运行状态指示）
        self._overlay = BorderOverlay()

        # 截屏器（定位后初始化，后续自动化复用）
        self._capture = None
        self._last_capture = None  # 最近一次截屏（numpy BGR）

        # 区域布局（定位后由区域编辑器设置）
        self._region_layout = None

        # 用户管理
        self._user_manager = UserConfigManager()

        # 布局管理
        self._layout_manager = LayoutConfigManager()

        # 用户配置（延迟参数等）
        self._user_config = load_user_config()

        # OCR 引擎（懒加载）
        from ..core.ocr import OCREngine
        self._ocr = OCREngine()

        # 输入控制器（传入延迟配置）
        from ..core.input import InputController
        self._input = InputController(delay_config=self._user_config.delay)

        self._setup_menu()
        self._setup_ui()
        self._refresh_user_combo()
        self._refresh_layout_combo()

        # 全局热键监听 F10（跨窗口焦点，自动化时游戏窗口占焦点也能响应）
        self.f10_pressed.connect(self._request_stop)
        self._hotkey_listener = pynput_keyboard.GlobalHotKeys({
            "<f10>": self._on_global_f10,
        })
        self._hotkey_listener.start()

        logger.info("主窗口已初始化")

    def _on_global_f10(self):
        """pynput 监听线程回调，转发到主线程处理"""
        self.f10_pressed.emit()

    # ─── 菜单栏 ────────────────────────────────────────────

    def _setup_menu(self):
        """构建顶部菜单栏"""
        menubar = self.menuBar()

        # ── 设置 ──
        settings_menu = menubar.addMenu("设置")

        user_mgmt_action = QAction("用户管理", self)
        user_mgmt_action.triggered.connect(self._open_user_manager)
        settings_menu.addAction(user_mgmt_action)

        settings_menu.addSeparator()

        region_editor_action = QAction("区域编辑", self)
        region_editor_action.triggered.connect(self._open_region_editor)
        settings_menu.addAction(region_editor_action)

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

    def _open_region_editor(self):
        """打开区域编辑器（无需截图，按场景加载）"""
        from .region_editor import RegionEditorDialog
        dialog = RegionEditorDialog(
            layout_manager=self._layout_manager,
            refresh_callback=self._refresh_capture,
            parent=self,
        )
        dialog.exec()
        self._refresh_layout_combo()

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

        # === 目标窗口选择 ===
        window_group = QGroupBox("目标窗口")
        window_main_layout = QVBoxLayout(window_group)

        row1 = QHBoxLayout()

        self.btn_scan_window = QPushButton("扫描窗口")
        self.btn_scan_window.setFixedWidth(90)
        self.btn_scan_window.clicked.connect(self._on_scan_window)
        row1.addWidget(self.btn_scan_window)

        self.window_combo = QComboBox()
        self.window_combo.setMinimumWidth(300)
        self.window_combo.currentIndexChanged.connect(self._on_window_selected)
        row1.addWidget(self.window_combo)

        self.btn_locate = QPushButton("定位")
        self.btn_locate.setFixedWidth(70)
        self.btn_locate.setEnabled(False)
        self.btn_locate.clicked.connect(self._on_locate_window)
        row1.addWidget(self.btn_locate)

        window_main_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.lbl_window_info = QLabel("未选择窗口")
        self.lbl_window_info.setStyleSheet("color: gray;")
        row2.addWidget(self.lbl_window_info)
        row2.addStretch()
        window_main_layout.addLayout(row2)
        main_layout.addWidget(window_group)

        # === 截屏预览区 ===
        self.preview_label = QLabel("定位窗口后自动截屏")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setFixedHeight(320)
        self.preview_label.setStyleSheet(
            "background-color: #2b2b2b; color: #888; font-size: 14px;"
        )
        main_layout.addWidget(self.preview_label)

        # === 中部：左右分栏 ===
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：配置区
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        self.btn_graduation = QPushButton("计算毕业率")
        self.btn_graduation.clicked.connect(self._on_graduation)
        left_layout.addWidget(self.btn_graduation)

        self.btn_tune_test = QPushButton("单次调律测试")
        self.btn_tune_test.clicked.connect(self._on_tune_test)
        left_layout.addWidget(self.btn_tune_test)

        flow_group = QGroupBox("目标流派")
        flow_layout = QVBoxLayout(flow_group)
        self.flow_selector = QComboBox()
        self.flow_selector.addItems([
            "会心双刀", "裂石威", "明川药典", "九剑", "无名",
        ])
        flow_layout.addWidget(self.flow_selector)
        left_layout.addWidget(flow_group)

        mode_group = QGroupBox("处理模式")
        mode_layout = QVBoxLayout(mode_group)
        self.mode_selector = QComboBox()
        self.mode_selector.addItems(["批量筛选", "精调模式"])
        mode_layout.addWidget(self.mode_selector)
        left_layout.addWidget(mode_group)

        action_group = QGroupBox("操作")
        action_layout = QVBoxLayout(action_group)

        self.btn_scan = QPushButton("扫描穿戴装备")
        self.btn_scan.clicked.connect(self._on_scan)
        action_layout.addWidget(self.btn_scan)

        self.btn_run_toggle = QPushButton()
        self.btn_run_toggle.clicked.connect(self._on_toggle_running)
        self._refresh_run_button()
        action_layout.addWidget(self.btn_run_toggle)

        left_layout.addWidget(action_group)
        left_layout.addStretch()

        # 右侧：日志/预览区
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        self.tabs = QTabWidget()

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        self.tabs.addTab(self.log_text, "运行日志")

        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.tabs.addTab(self.status_text, "装备状态")

        right_layout.addWidget(self.tabs)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([300, 700])

        main_layout.addWidget(splitter)

        # === 底部状态栏 ===
        self.statusBar().showMessage("就绪 | F9 开始 | F10 停止")

        self._setup_log_redirect()

    def _setup_log_redirect(self):
        """将 loguru 日志输出到 GUI 日志面板"""
        class QtSink:
            def __init__(self, text_edit):
                self.text_edit = text_edit

            def write(self, message):
                try:
                    self.text_edit.append(message.strip())
                except RuntimeError:
                    pass

        sink = QtSink(self.log_text)
        logger.add(sink, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}")

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
        """关闭主窗口时清理热键监听与原生覆盖层。"""
        try:
            self._hotkey_listener.stop()
        except Exception:
            pass
        self._overlay.destroy()
        super().closeEvent(event)
