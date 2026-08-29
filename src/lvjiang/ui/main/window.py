"""通用主窗口。

包含完整的基础功能：
- 用户管理、场景管理、图库管理、图像识别
- 窗口/设备扫描与定位
- 工作流加载、执行
- 运行日志面板
- 全局热键（默认 F9 开始、F10 结束、F11 暂停/恢复；按键位可在配置管理→
  热键设置里改，保存后立即生效；定位/连接后回调方生效）

插件通过 hooks 机制扩展左侧/右侧 Tab 和菜单项。
"""
from __future__ import annotations

import threading

from loguru import logger
from PyQt6.QtCore import QEvent, QObject, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QStyleOptionComboBox,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps import get_registry

from ...core.config import load_available_envs, load_env, load_user_config
from ...core.config.users import SessionManager
from ...core.layout_manager import LayoutConfigManager
from ...core.user_config import UserConfigManager
from ...i18n import tr
from ..button_styles import apply_button_style
from ..overlay import BorderOverlay
from ..widgets import TrimmedLogEdit
from .capture_ops import CaptureOpsMixin
from .menu_ops import MenuOpsMixin
from .run_control import RunControlMixin
from .startup_ops import StartupOpsMixin
from .tray_ops import TrayOpsMixin
from .ui_state import UiStateMixin
from .window_ops import WindowOpsMixin


class _LogBridge(QObject):
    """信号桥：将后台线程的日志安全转发到主线程"""
    append_log = pyqtSignal(str)


DEFAULT_TITLE = tr("律匠 - 通用视觉 RPA 引擎")
_TOP_COMBO_CHARACTER_CAPACITY = 6
_BATCH_CONTEXT_LOCK_MESSAGE = tr("批量任务运行过程中不可修改环境和布局")


class _BatchContextComboBox(QComboBox):
    """Combo whose user interaction can be locked for a running batch."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._batch_locked = False
        self.installEventFilter(self)

    def set_batch_locked(self, locked: bool) -> None:
        self._batch_locked = locked
        self.setEnabled(not locked)
        self.setCursor(
            Qt.CursorShape.ForbiddenCursor if locked
            else Qt.CursorShape.ArrowCursor
        )
        self.setToolTip(_BATCH_CONTEXT_LOCK_MESSAGE if locked else "")

    def eventFilter(self, watched, event):
        if watched is self and self._batch_locked:
            if event.type() == QEvent.Type.MouseButtonPress:
                QMessageBox.information(
                    self.window(), tr("提示"), _BATCH_CONTEXT_LOCK_MESSAGE
                )
            if event.type() in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
                QEvent.Type.MouseButtonDblClick,
            ):
                return True
        return super().eventFilter(watched, event)


def _set_combo_character_capacity(
    combo: QComboBox,
    character_count: int = _TOP_COMBO_CHARACTER_CAPACITY,
    *,
    expanding: bool = False,
) -> int:
    """Fix a combo width that leaves room for N full-width Chinese characters.

    ``setMinimumContentsLength`` is based on an average Latin glyph and can still
    truncate CJK text.  Ask the active Qt style to add its frame and arrow chrome
    around an explicitly measured full-width text area instead.

    ``expanding=True`` 时该宽度只作为**下限**，控件随所在分栏一起变宽——
    左侧分栏拉宽后，长脚本名就能完整显示，而不是恒定停在下限宽度上。
    """
    metrics = combo.fontMetrics()
    content = QSize(
        metrics.horizontalAdvance("汉" * character_count),
        metrics.height(),
    )
    option = QStyleOptionComboBox()
    option.initFrom(combo)
    option.editable = combo.isEditable()
    option.frame = combo.hasFrame()
    style = combo.style()
    assert style is not None
    width = style.sizeFromContents(
        QStyle.ContentsType.CT_ComboBox,
        option,
        content,
        combo,
    ).width()
    if expanding:
        combo.setMinimumWidth(width)
        combo.setMaximumWidth(16777215)   # 撤掉可能存在的固定宽
        policy = combo.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
        combo.setSizePolicy(policy)
    else:
        combo.setFixedWidth(width)

    def _refresh_popup_width(*_args) -> None:
        longest = max(
            (metrics.horizontalAdvance(combo.itemText(index))
             for index in range(combo.count())),
            default=0,
        )
        popup_width = style.sizeFromContents(
            QStyle.ContentsType.CT_ComboBox,
            option,
            QSize(longest, metrics.height()),
            combo,
        ).width() + 12
        view = combo.view()
        assert view is not None
        view.setMinimumWidth(max(width, popup_width))

    model = combo.model()
    assert model is not None
    model.rowsInserted.connect(_refresh_popup_width)
    model.modelReset.connect(_refresh_popup_width)
    model.dataChanged.connect(_refresh_popup_width)
    _refresh_popup_width()
    return width


def _create_workflow_note_label() -> QLabel:
    """创建脚本说明标签；按左侧 Tab 可用宽度自动换行。"""
    label = QLabel()
    label.setObjectName("workflowNote")
    label.setWordWrap(True)
    label.setAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setStyleSheet("padding: 2px 4px;")
    label.setVisible(False)
    return label


def _get_title_with_version() -> str:
    """获取带版本号的窗口标题"""
    try:
        from ..._version import __version__
        if __version__ and __version__ != "0.0.0.dev0":
            return f"{DEFAULT_TITLE} v{__version__}"
    except Exception:
        pass
    return DEFAULT_TITLE


class MainWindow(
    WindowOpsMixin,
    RunControlMixin,
    CaptureOpsMixin,
    TrayOpsMixin,
    StartupOpsMixin,
    MenuOpsMixin,
    UiStateMixin,
    QMainWindow,
):
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
    pause_pressed = pyqtSignal()
    _scrcpy_frame_ready = pyqtSignal(object)
    # 宿主信号：自动化状态（"running" / "paused" / "not_ready" / "idle"）与用户切换
    automation_state_changed = pyqtSignal(str)
    user_changed = pyqtSignal(str)
    # app 业务事件统一走带命名空间的 AppEvent 信封。
    app_event = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        # 构造期间禁止重绘，防止 adjustSize / resize 等操作
        # 在 show() 之前触发 DWM 短暂渲染出一帧小窗口
        self.setUpdatesEnabled(False)
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
        self._stop_requested = False
        self._current_worker = None
        self._overlay = BorderOverlay()
        self._capture = None
        self._last_capture = None
        self._close_cleanup_started = False

        # ── 管理器 ──
        self._user_manager = UserConfigManager()
        self._session_manager = SessionManager()
        self._layout_manager = LayoutConfigManager()
        self._user_config = load_user_config()
        self._backend = None
        self._cleanup_callbacks: list = []  # 插件注册的关闭时清理回调

        # ── OCR / 输入 ──
        from ...core.ocr import OCREngine
        self._ocr = OCREngine()
        # 非 Windows 时返回 None（无桌面投屏后端，仅支持 ADB 模式）
        from ...core.platforms import create_desktop_input
        self._win_input = create_desktop_input(input_sim=self._user_config.input_sim)
        self._input = self._win_input

        # ── 构建 UI ──
        self._setup_menu()
        self._setup_ui()
        self._build_tray_icon()
        self._refresh_run_button()
        self._refresh_user_combo()
        self._refresh_layout_combo()
        self._load_workflow_configs()
        self._restore_daily_config()

        # ── 全局热键（默认 F9-F11；回调内按后端就绪门控，定位/连接后方生效）──
        self.f9_pressed.connect(self._on_f9_start)
        self.f10_pressed.connect(self._request_stop)
        self.pause_pressed.connect(self._on_pause_resume)
        self._scrcpy_frame_ready.connect(self._on_scrcpy_frame_ui)
        # 启动全局热键（内部先安装 pynput 防护补丁）；
        # macOS 未授权时返回 None，降级为窗口内热键（keyPressEvent 使用当前配置）
        from ...core.platforms import start_global_hotkeys
        self._hotkey_listener = start_global_hotkeys(
            self._main_global_hotkey_bindings())

        # 注册 SessionStore 的 UI 回调，用于多进程文件锁失败时显示重试对话框
        self._setup_session_ui_callback()

        logger.info("主窗口已初始化")

    def _main_global_hotkey_bindings(self):
        """主窗口常驻全局热键；F12 由录制对话框临时管理。

        按键位从「配置管理 → 热键设置」读取（默认 F9/F10/F11）。
        """
        from ...core.platforms import hotkey_pynput_token
        hk = self._user_config.hotkeys
        return {
            hotkey_pynput_token(hk.start): self._on_global_f9,
            hotkey_pynput_token(hk.stop): self._on_global_f10,
            hotkey_pynput_token(hk.pause): self._on_global_pause,
        }

    # ─── SessionStore UI 回调 ──────────────────────────────────────

    def _setup_session_ui_callback(self):
        """为 SessionStore 注册 UI 回调，用于显示多进程锁失败的重试对话框"""
        from ...core.config import get_session_store

        def show_confirm_dialog(title: str, message: str) -> bool:
            """显示确认对话框，返回用户是否选择重试"""
            reply = QMessageBox.warning(
                self,
                title,
                message,
                QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Cancel
            )
            return reply == QMessageBox.StandardButton.Retry

        get_session_store().set_ui_callback(lambda cmd, *args:
            show_confirm_dialog(*args) if cmd == "confirm" else None
        )

    # ─── 热键回调 ────────────────────────────────────────────

    def _on_global_f9(self):
        if not self._backend_ready():
            return
        self.f9_pressed.emit()

    def _on_global_f10(self):
        # 运行中的停止请求不依赖后端仍然可用；断连/窗口消失恰恰是最需要
        # F10 立即退出的场景。空闲时仍保留 ready 门控以免误触。
        if not self._running and not self._backend_ready():
            return
        self.f10_pressed.emit()

    def _on_global_pause(self):
        """全局暂停热键：暂停/恢复。"""
        if not self._backend_ready():
            return
        self.pause_pressed.emit()

    def _on_f9_start(self):
        """F9 启动入口（全局热键 / 窗口按键共用）"""
        if not self._running:
            self._on_start()

    # ─── UI 构建 ─────────────────────────────────────────────

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # === 顶部：用户 + 环境 + 布局 ===
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel(tr("用户")))
        self.user_combo = QComboBox()
        self.user_combo.setMinimumWidth(150)
        self.user_combo.currentIndexChanged.connect(self._on_user_changed)
        top_row.addWidget(self.user_combo)
        top_row.addSpacing(20)
        top_row.addWidget(QLabel(tr("环境")))
        self._env_combo = _BatchContextComboBox()
        _set_combo_character_capacity(self._env_combo)
        for key, display in load_available_envs():
            self._env_combo.addItem(display, key)
        current_env = load_env()
        idx = self._env_combo.findData(current_env)
        if idx >= 0:
            self._env_combo.setCurrentIndex(idx)
        self._env_combo.currentIndexChanged.connect(self._on_env_changed)
        top_row.addWidget(self._env_combo)
        # 环境说明按钮
        env_tips_btn = QPushButton("?")
        env_tips_btn.setFixedSize(20, 20)
        _env_tip_text = (
            tr("目标环境决定导航策略和输入方式（指游戏运行的环境，非本机系统）：") + "\n"
            "• " + tr("桌面（desktop）：PC 游戏窗口，使用窗口投屏 + 鼠标点击") + "\n"
            "• " + tr("安卓（android）：手机或模拟器，使用 ADB 截图 + 触摸输入") + "\n\n"
            + tr("【重要】PC 端使用模拟器（如 MuMu、雷电）必须选择「安卓」，") + "\n"
            + tr("否则导航流程会因输入方式不匹配而失败。") + "\n\n"
            + tr("【模拟器画面比例】如果目标模拟器没有 20:9 的画面比例预设，") + "\n"
            + tr("可采用自定义设置 2000:900（等效 20:9）。")
        )
        env_tips_btn.setToolTip(_env_tip_text)
        env_tips_btn.clicked.connect(
            lambda: QMessageBox.information(self, tr("目标环境说明"), _env_tip_text))
        env_tips_btn.setStyleSheet(
            "QPushButton{border:1px solid palette(mid);border-radius:10px;"
            "color:palette(text);font-weight:bold;font-size:12px;}"
            "QPushButton:hover{background:palette(midlight);}"
        )
        top_row.addWidget(env_tips_btn)
        top_row.addSpacing(20)
        top_row.addWidget(QLabel(tr("布局")))
        self.layout_combo = _BatchContextComboBox()
        _set_combo_character_capacity(self.layout_combo)
        self.layout_combo.currentIndexChanged.connect(self._on_layout_changed)
        top_row.addWidget(self.layout_combo)
        # 布局描述标签（显示布局的 desc 字段）
        self.layout_desc_label = QLabel()
        self.layout_desc_label.setStyleSheet("color: palette(mid);")
        top_row.addWidget(self.layout_desc_label)
        top_row.addStretch()
        # 最小化到状态栏：单屏玩家看不到任务栏时靠托盘图标颜色判断运行状态。
        # 平时不显示托盘图标（不想常驻），点这个按钮才临时出现。
        self.btn_minimize_to_tray = QPushButton(tr("最小化到状态栏"))
        apply_button_style(self.btn_minimize_to_tray, variant="neutral")
        self.btn_minimize_to_tray.clicked.connect(self._minimize_to_tray)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.btn_minimize_to_tray.setVisible(False)
        top_row.addWidget(self.btn_minimize_to_tray)
        main_layout.addLayout(top_row)

        # === ADB 断连警告条（单行，默认隐藏）===
        # resume_event 存在主窗口级别，不随 device 对象断连/重连而丢失
        self._adb_resume_event = threading.Event()
        self._adb_resume_event.set()  # 初始为已恢复，不阻塞
        self._adb_banner = QFrame()
        self._adb_banner.setStyleSheet("background-color: #d32f2f;")
        self._adb_banner.setVisible(False)
        _bl = QHBoxLayout(self._adb_banner)
        _bl.setContentsMargins(8, 2, 8, 2)
        self._adb_banner_label = QLabel()
        self._adb_banner_label.setStyleSheet("color: white; font-weight: bold;")
        _bl.addWidget(self._adb_banner_label, stretch=1)
        self._adb_banner_btn = QPushButton(tr("恢复"))
        self._adb_banner_btn.setStyleSheet(
            "background-color: white; color: #d32f2f; font-weight: bold; "
            "padding: 2px 12px; border: none; font-size: 12px;"
        )
        _bl.addWidget(self._adb_banner_btn)
        main_layout.addWidget(self._adb_banner)

        # === 窗口/设备选择 ===
        window_group = QGroupBox()
        self.window_group = window_group
        window_main_layout = QVBoxLayout(window_group)

        row1 = QHBoxLayout()
        self.btn_scan_window = QPushButton(tr("扫描窗口"))
        self.btn_scan_window.setFixedWidth(90)
        self.btn_scan_window.clicked.connect(self._on_scan_window)
        from ...core.platforms import DESKTOP_BACKEND_AVAILABLE
        if not DESKTOP_BACKEND_AVAILABLE:
            # 非 Windows 仅支持 ADB 模式，隐藏窗口投屏入口
            self.btn_scan_window.setVisible(False)
        row1.addWidget(self.btn_scan_window)

        self.btn_scan_device = QPushButton(tr("扫描设备"))
        self.btn_scan_device.setFixedWidth(90)
        self.btn_scan_device.clicked.connect(self._on_scan_devices)
        row1.addWidget(self.btn_scan_device)

        self.window_combo = QComboBox()
        self.window_combo.setMinimumWidth(300)
        self.window_combo.currentIndexChanged.connect(self._on_window_selected)
        row1.addWidget(self.window_combo)

        self.btn_locate = QPushButton(tr("定位"))
        self.btn_locate.setFixedWidth(70)
        self.btn_locate.setEnabled(False)
        self.btn_locate.clicked.connect(self._on_locate_window)
        row1.addWidget(self.btn_locate)

        self.btn_hide_window = QPushButton(tr("显示预览"))
        self.btn_hide_window.setFixedWidth(80)
        self.btn_hide_window.setCheckable(True)
        self.btn_hide_window.setChecked(True)
        self.btn_hide_window.clicked.connect(self._on_toggle_preview)
        row1.addWidget(self.btn_hide_window)
        apply_button_style(
            self.btn_scan_window,
            self.btn_scan_device,
            self.btn_locate,
            self.btn_hide_window,
            variant="neutral",
        )
        window_main_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.lbl_window_info = QLabel(tr("未选择窗口"))
        self.lbl_window_info.setStyleSheet("color: gray;")
        row2.addWidget(self.lbl_window_info)
        row2.addStretch()

        # 红框标定：定位后窗口边缘是否显示红色标记框，纯运行期状态，不持久化，
        # 每次启动默认勾选。
        self.chk_red_box = QCheckBox(tr("红框标定"))
        self.chk_red_box.setVisible(False)
        self.chk_red_box.setChecked(True)
        self.chk_red_box.setToolTip(tr("定位窗口后是否显示红色边框标记；不保存配置，仅本次运行期间生效"))
        self.chk_red_box.stateChanged.connect(self._on_red_box_changed)
        row2.addWidget(self.chk_red_box)

        self.chk_bg_mode = QCheckBox(tr("后台模式"))
        self.chk_bg_mode.setVisible(False)
        self.chk_bg_mode.stateChanged.connect(self._on_bg_mode_changed)
        row2.addWidget(self.chk_bg_mode)

        self.chk_scrcpy = QCheckBox(tr("流式截图"))
        self.chk_scrcpy.setVisible(False)
        self.chk_scrcpy.stateChanged.connect(self._on_capture_method_changed)
        row2.addWidget(self.chk_scrcpy)

        # Beta 输入通道：只替代 adb shell input，不接管截图方式；连接时生效
        self.chk_agent = QCheckBox(tr("设备端手势 (Beta)"))
        self.chk_agent.setVisible(False)
        self.chk_agent.setToolTip(tr("需安装律匠 App 并开启无障碍服务；仅改变输入通道，不改变截图方式；不可达时回退 ADB shell input"))
        self.chk_agent.stateChanged.connect(self._on_agent_mode_changed)
        row2.addWidget(self.chk_agent)
        window_main_layout.addLayout(row2)

        self._apply_backend_ui(self._backend)
        main_layout.addWidget(window_group)

        # === 截屏预览区（左：实时画面 / 右：采集控制面板）===
        self.preview_container = QWidget()
        self.preview_container.setFixedHeight(320)
        preview_layout = QHBoxLayout(self.preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(6)

        self.preview_label = QLabel(tr("定位窗口后自动截屏"))
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

        # 右侧面板：Tab + 告警区域
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        right_layout.addWidget(self.tabs, stretch=1)

        # 告警面板（在 Tab 下方）
        from ..alert_panel import AlertPanel
        self._alert_panel = AlertPanel()
        right_layout.addWidget(self._alert_panel)

        splitter.addWidget(right_panel)

        # 初始比例：左 1/3、右 2/3
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([333, 667])
        self._main_splitter = splitter
        main_layout.addWidget(splitter, stretch=1)

        # === 底部状态栏 ===
        hk = self._user_config.hotkeys
        self.statusBar().showMessage(
            f"{tr('就绪')} | {hk.start} {tr('开始')} | {hk.pause} {tr('暂停')} | {hk.stop} {tr('结束')}")
        self.adjustSize()
        self.setMinimumHeight(self.height())
        self._migrate_ui_state()
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

        # 开始/停止 + 暂停/恢复按钮（第一行）
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(8)
        self.btn_run_workflow = QPushButton(f"{tr('开始执行')} ({self._user_config.hotkeys.start})")
        self.btn_run_workflow.clicked.connect(self._on_run_workflow)
        self.btn_run_workflow.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
        )
        btn_layout.addWidget(self.btn_run_workflow)

        self.btn_pause_resume = QPushButton(tr("暂停"))
        self.btn_pause_resume.clicked.connect(self._on_pause_resume)
        self.btn_pause_resume.setEnabled(False)  # 初始禁用
        self.btn_pause_resume.setStyleSheet(
            "background-color: #9E9E9E; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
        )
        btn_layout.addWidget(self.btn_pause_resume)
        daily_layout.addLayout(btn_layout)

        wf_group = QGroupBox(tr("脚本"))
        wf_layout = QHBoxLayout(wf_group)
        self.workflow_combo = QComboBox()
        self.workflow_combo.setFixedHeight(34)
        # 脚本名可以很长，下拉框随左侧分栏一起变宽
        _set_combo_character_capacity(
            self.workflow_combo, 10, expanding=True)
        self.workflow_combo.currentIndexChanged.connect(self._on_workflow_combo_changed)
        wf_layout.addWidget(self.workflow_combo)
        self.btn_load_workflow = QPushButton(tr("加载"))
        self.btn_load_workflow.setFixedSize(68, 34)
        self.btn_load_workflow.clicked.connect(self._on_load_workflow)
        self.btn_load_workflow.setStyleSheet(
            "background-color: #607D8B; color: white; font-weight: bold; padding: 6px;"
        )
        wf_layout.addWidget(self.btn_load_workflow)
        wf_layout.addStretch(1)
        daily_layout.addWidget(wf_group)

        self._workflow_note_label = _create_workflow_note_label()
        daily_layout.addWidget(self._workflow_note_label)

        self._param_panel = QGroupBox(tr("参数设置"))
        self._param_layout = QFormLayout(self._param_panel)
        self._param_panel.setVisible(False)
        daily_layout.addWidget(self._param_panel)

        daily_layout.addStretch()
        daily_scroll.setWidget(daily_panel)
        self._left_tabs.addTab(daily_scroll, tr("日常"))

        # ── Tab 2: 批量 ──
        from ..batch import BatchTab
        self._batch_tab = BatchTab(host=self)
        self._left_tabs.addTab(self._batch_tab, tr("批量"))

        # ── 插件注入的左侧 Tab（按 -reg 顺序追加）──
        self._add_plugin_tabs(self._left_tabs, "left_tab_builders")

        # 连接 Tab 切换信号，保存当前页签
        self._left_tabs.currentChanged.connect(self._save_tab_indices)

    def _build_right_tabs(self):
        """构建内置右侧 Tab，再按注册顺序追加插件 Tab。"""
        self._log_buffer: list[tuple[int, str]] = []  # (level, text)
        self._log_min_level = 20  # INFO=20, DEBUG=10

        # 日志面板容器：日志文本 + 底部级别过滤栏
        log_container = QWidget()
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(2)

        self.log_text = TrimmedLogEdit()
        self.log_text.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        log_layout.addWidget(self.log_text, 1)

        # 底部级别过滤栏
        filter_bar = QHBoxLayout()
        filter_bar.setContentsMargins(4, 0, 4, 2)
        filter_bar.addStretch()
        filter_bar.addWidget(QLabel(tr("日志级别")))
        self._log_level_combo = QComboBox()
        self._log_level_combo.addItem("DEBUG", 10)
        self._log_level_combo.addItem("INFO", 20)
        self._log_level_combo.addItem("WARNING", 30)
        self._log_level_combo.addItem("ERROR", 40)
        self._log_level_combo.setCurrentIndex(1)  # 默认 INFO，与 _log_min_level=20 一致
        self._log_level_combo.setToolTip(tr("切换日志显示级别：低于选中级别的日志将被隐藏"))
        self._log_level_combo.currentIndexChanged.connect(self._on_log_level_changed)
        filter_bar.addWidget(self._log_level_combo)
        log_layout.addLayout(filter_bar)

        self.tabs.addTab(log_container, tr("运行日志"))

        # Profile 是主引擎共享的用户档案能力，不由任何插件注入。
        from ...core.profile.engine import get_or_create_engine, stop_engine
        from ..profile import ProfileTab, UserInfoTab

        profile_engine = get_or_create_engine(self._user_manager)
        if not profile_engine.isRunning():
            profile_engine.start()
            self.register_cleanup(stop_engine)

        self.tabs.addTab(ProfileTab(self), tr("用户总览"))
        self.tabs.addTab(UserInfoTab(self), tr("用户信息"))

        # ── 插件注入的右侧 Tab（按 -reg 顺序追加）──
        self._add_plugin_tabs(self.tabs, "right_tab_builders")

        # 连接 Tab 切换信号，保存当前页签
        self.tabs.currentChanged.connect(self._save_tab_indices)

    def _add_plugin_tabs(self, tab_widget: QTabWidget, registry_key: str):
        """消费注册表中的插件 Tab builder；单个失败只记日志不中断。"""
        for label, builder in get_registry().get(registry_key, []):
            try:
                tab_widget.addTab(builder(self), tr(label))
            except Exception:  # noqa: BLE001
                logger.exception(f"插件 Tab「{label}」构建失败")

    # ─── 宿主 API（供插件页面使用）──────────────────────

    def register_cleanup(self, callback) -> None:
        """注册关闭时清理回调（插件在初始化时调用）"""
        self._cleanup_callbacks.append(callback)

    @property
    def user_manager(self) -> "UserConfigManager":
        """用户配置管理器（只读）"""
        return self._user_manager

    @property
    def session_manager(self) -> "SessionManager":
        """会话数据管理器（只读）"""
        return self._session_manager

    @property
    def alert_panel(self):
        """告警面板（供插件推送告警）"""
        return self._alert_panel

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

    def request_pause_resume(self):
        """切换暂停/恢复（等价当前配置的暂停热键）"""
        self._on_pause_resume()

    def append_log(self, text: str):
        """向运行日志面板追加一行消息"""
        self._log_append(text)

    def _log_append(self, text: str):
        """带级别检测的日志追加：缓冲全部，按当前级别过滤显示"""
        import logging
        # 支持两种格式：[LEVEL] 前缀 或 loguru 格式 "| LEVEL"
        prefix = text[:40]
        if text.startswith("[ERROR]") or "| ERROR" in prefix:
            level = logging.ERROR
        elif text.startswith("[WARNING]") or "| WARNING" in prefix:
            level = logging.WARNING
        elif text.startswith("[DEBUG]") or "| DEBUG" in prefix:
            level = logging.DEBUG
        else:
            level = logging.INFO
        self._log_buffer.append((level, text))
        if level >= self._log_min_level:
            self.log_text.append(text)

    def _on_log_level_changed(self):
        """日志级别切换：更新阈值，重建显示"""
        self._log_min_level = self._log_level_combo.currentData()
        self.log_text.clear()
        for level, text in self._log_buffer:
            if level >= self._log_min_level:
                self.log_text.append(text)

    def _setup_log_redirect(self):
        self._log_bridge = _LogBridge(self)
        self._log_bridge.append_log.connect(self._log_append)

        class QtSink:
            def __init__(self, bridge):
                self._bridge = bridge
            def write(self, message):
                self._bridge.append_log.emit(message.strip())

        sink = QtSink(self._log_bridge)
        logger.add(sink, level="DEBUG", format="{time:HH:mm:ss} | {level:<7} | {message}")

    # ─── 批处理执行 ───────────────────────────────────────

    def run_batch(self, enabled_rows, scripts) -> bool:
        """启动批量执行，返回是否成功

        enabled_rows: list[tuple[int, dict]] - [(index, row_data), ...]
        """
        if not self._backend_ready():
            if self._backend == "adb":
                self._log_append(tr("[错误] 请先连接设备"))
            else:
                self._log_append(tr("[错误] 请先定位窗口"))
            return False

        if not self._begin_automation(tr("批量执行")):
            return False

        layout_name = self.layout_combo.currentText()
        layout = self._layout_manager.load_layout(layout_name)
        if not layout:
            self._log_append(f"[错误] 无法加载布局: {layout_name}")
            self._end_automation(tr("批量执行"))
            return False

        if self._backend == "adb":
            window_left, window_top = 0, 0
        else:
            if self._input.background_mode and self._target_window:
                self._input.target_hwnd = self._target_window["hwnd"]
            window_left = self._target_window["left"]
            window_top = self._target_window["top"]

        from ...core.batch_config import load_batch_config
        from ..batch import BatchContext, BatchWorker

        ctx = BatchContext(
            capture=self._capture,
            ocr=self._ocr,
            input_ctrl=self._input,
            layout=layout,
            run_env=self._selected_run_env(),
            input_sim=self._user_config.input_sim,
            delay_params=self._user_config.delay_params,
            window_left=window_left,
            window_top=window_top,
            pause_event=getattr(self, '_pause_event', None),
            ui_callback=self._create_ui_callback(),
        )

        # 获取当前配置
        cfg = load_batch_config()
        config = cfg.get_active()
        if not config:
            self._log_append(tr("[错误] 暂无配置，请先通过 工具 → 批量配置 添加"))
            self._end_automation(tr("批量执行"))
            return False

        worker = BatchWorker(
            enabled_rows=enabled_rows,
            scripts=scripts,
            config=config,
            ctx=ctx,
            session_manager=self._session_manager,
            stop_check=self._is_stopped,
        )

        # 信号连接：进度 → batch_tab，日志 → log_text
        # （批量层显式传递用户，不再联动主页面用户下拉）
        worker.progress.connect(self._batch_tab.update_progress)
        worker.log.connect(self._log_append)
        worker.finished_all.connect(self._batch_tab.on_batch_finished)
        worker.finished_all.connect(
            lambda _: self._end_automation(tr("批量执行"))
        )

        self._current_worker = worker  # type: ignore[assignment]
        self._set_batch_context_controls_locked(True)
        worker.start()
        return True

    def _open_batch_config(self):
        """工具菜单 → 批量配置：打开配置对话框"""
        from ..batch import BatchConfigDialog
        dlg = BatchConfigDialog(self)
        if dlg.exec():
            # 保存后刷新批量 Tab 的条目概览和脚本勾选
            self._batch_tab.refresh_config()

    # ─── 快捷键 + 关闭 ───────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):  # type: ignore[override]
        # macOS 无全局热键时的窗口内兜底；按键位跟随「热键设置」配置。
        hk = self._user_config.hotkeys
        key = event.key()
        if key == getattr(Qt.Key, f"Key_{hk.start}", None):
            self._on_f9_start()
        elif key == getattr(Qt.Key, f"Key_{hk.pause}", None):
            # 全局热键已处理暂停键（避免双触发导致暂停/恢复互相抵消）
            if self._hotkey_listener is not None:
                return
            self._on_pause_resume()
        elif key == getattr(Qt.Key, f"Key_{hk.stop}", None):
            self._request_stop()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        # QApplication 退出或重复 close() 可能再次投递关闭事件。资源清理只
        # 允许执行一次，尤其不能重复卸载热键、销毁 overlay 或关闭采集后端。
        if self._close_cleanup_started:
            super().closeEvent(event)
            return
        if self._running:
            reply = QMessageBox.question(
                self, tr("工作流运行中"),
                tr("当前有工作流正在运行，关闭程序将终止工作流。\n确定要退出吗？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._request_stop()
        self._close_cleanup_started = True
        if self._tray_icon is not None:
            self._tray_icon.hide()
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
        # 插件清理回调（如 ProfileEngine 停止）
        for cb in self._cleanup_callbacks:
            try:
                cb()
            except Exception as e:
                logger.warning(f"插件清理回调失败: {e}")
        # 录屏进行中/待保存时自动转正保存，不丢数据
        self._abort_screen_record(tr("关闭程序"))
        if self._backend == "adb":
            self._teardown_adb_backend()
        else:
            self._stop_capture_backend()
        self._overlay.destroy()
        super().closeEvent(event)
