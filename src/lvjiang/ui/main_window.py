"""通用主窗口。

包含完整的基础功能：
- 用户管理、场景管理、图库管理、图像识别
- 窗口/设备扫描与定位
- 工作流加载、执行
- 运行日志面板
- 全局热键（默认 F9 开始、F10 结束、F8 暂停/恢复；按键位可在配置管理→
  热键设置里改，保存后立即生效；定位/连接后回调方生效）

插件通过 hooks 机制扩展左侧/右侧 Tab 和菜单项。
"""
from __future__ import annotations

import sys
import threading

from loguru import logger
from PyQt6.QtCore import QEvent, QObject, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QKeyEvent
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
    QSpinBox,
    QSplitter,
    QStyle,
    QStyleOptionComboBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps import get_registry

from ..core.config import load_available_envs, load_env, load_user_config
from ..core.config.users import SessionManager
from ..core.layout_manager import LayoutConfigManager
from ..core.user_config import UserConfigManager
from ..i18n import tr
from .button_styles import apply_button_style
from .capture_ops import CaptureOpsMixin
from .overlay import BorderOverlay
from .run_control import RunControlMixin
from .theme import get_theme_manager
from .widgets import FlowLayout, TrimmedLogEdit
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
) -> int:
    """Fix a combo width that leaves room for N full-width Chinese characters.

    ``setMinimumContentsLength`` is based on an average Latin glyph and can still
    truncate CJK text.  Ask the active Qt style to add its frame and arrow chrome
    around an explicitly measured full-width text area instead.
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
    combo.setFixedWidth(width)
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
        from .._version import __version__
        if __version__ and __version__ != "0.0.0.dev0":
            return f"{DEFAULT_TITLE} v{__version__}"
    except Exception:
        pass
    return DEFAULT_TITLE


class _FlowContainer(QWidget):
    """FlowLayout 容器，正确传递 heightForWidth 给外层 QFormLayout。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._flow = None

    def set_flow_layout(self, flow: FlowLayout):
        self._flow = flow

    def hasHeightForWidth(self):
        return self._flow is not None and self._flow.hasHeightForWidth()

    def heightForWidth(self, width: int) -> int:
        if self._flow is not None:
            return self._flow.heightForWidth(width)
        return super().heightForWidth(width)


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
    # 宿主信号：自动化状态（"running" / "paused" / "not_ready" / "idle"）与用户切换
    automation_state_changed = pyqtSignal(str)
    user_changed = pyqtSignal(str)
    # 装备变更信号（用于通知战斗属性 Tab 刷新）
    equipment_changed = pyqtSignal()
    # 工作流请求打开"创建基础属性"面板并预填数值（战斗属性 Tab 订阅处理）
    open_play_style_form = pyqtSignal(dict)
    # 毕业率计算完成信号（携带计算结果，None 表示尚未计算）
    graduation_updated = pyqtSignal(object)

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

        # ── 管理器 ──
        self._user_manager = UserConfigManager()
        self._session_manager = SessionManager()
        self._layout_manager = LayoutConfigManager()
        self._user_config = load_user_config()
        self._backend = None
        self._cleanup_callbacks: list = []  # 插件注册的关闭时清理回调

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
        self.f8_pressed.connect(self._on_pause_resume)
        self._scrcpy_frame_ready.connect(self._on_scrcpy_frame_ui)
        # 启动全局热键（内部先安装 pynput 防护补丁）；
        # macOS 未授权时返回 None，降级为窗口内热键（keyPressEvent 已处理 F8-F10）
        from ..core.platforms import start_global_hotkeys
        self._hotkey_listener = start_global_hotkeys(
            self._main_global_hotkey_bindings())

        # 注册 SessionStore 的 UI 回调，用于多进程文件锁失败时显示重试对话框
        self._setup_session_ui_callback()

        logger.info("主窗口已初始化")

    def _main_global_hotkey_bindings(self):
        """主窗口常驻全局热键；F12 由录制对话框临时管理。

        按键位从「配置管理 → 热键设置」读取（默认 F9/F10/F8）。
        """
        from ..core.platforms import hotkey_pynput_token
        hk = self._user_config.hotkeys
        return {
            hotkey_pynput_token(hk.start): self._on_global_f9,
            hotkey_pynput_token(hk.stop): self._on_global_f10,
            hotkey_pynput_token(hk.pause): self._on_global_f8,
        }

    # ─── SessionStore UI 回调 ──────────────────────────────────────

    def _setup_session_ui_callback(self):
        """为 SessionStore 注册 UI 回调，用于显示多进程锁失败的重试对话框"""
        from ..core.config import get_session_store

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

    # ─── 启动时检查更新 ────────────────────────────────────────

    def check_update_on_startup(self):
        """启动时先检查公告，处理完成后再检查版本更新。"""
        from ..core.announcement import (
            AnnouncementChecker,
            AnnouncementFetchResult,
            applicable_notices,
            cache_manifest,
            get_last_notice_version,
            mark_notice_version,
            should_prompt_manifest,
        )

        checker = AnnouncementChecker(self)

        def continue_to_update():
            self._start_update_check_on_startup()

        def on_finished(result: AnnouncementFetchResult):
            try:
                manifest = result.manifest
                cache_manifest(manifest, result.etag)
                if should_prompt_manifest(manifest):
                    from .announcement_dialog import AnnouncementDialog
                    notices = applicable_notices(manifest)
                    dialog = AnnouncementDialog(
                        manifest, notices, self, allow_refresh=False)
                    dialog.exec()
                    # 窗口确实展示并关闭后才推进，避免拉取成功但弹窗失败时吞公告。
                    mark_notice_version(manifest.notice_version)
                elif manifest.notice_version > get_last_notice_version():
                    # 新清单没有覆盖当前客户端，也无需在以后每次启动重复判断。
                    mark_notice_version(manifest.notice_version)
            finally:
                continue_to_update()

        checker.finished.connect(on_finished)
        checker.error.connect(lambda _message: continue_to_update())
        checker.start()
        self._startup_announcement_checker = checker  # 防止被 GC

    def _start_update_check_on_startup(self):
        """公告检查完成后执行原有的静默版本检查。"""
        from ..core.update import UpdateChecker, should_prompt_update

        checker = UpdateChecker(self)

        def on_finished(release):
            if not should_prompt_update(release.version):
                return  # 用户已选择跳过此版本

            from .update_dialog import UpdateDialog
            dialog = UpdateDialog(release, self)
            dialog.exec()  # 用户选择“继续使用”时直接关闭对话框

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
        """全局 F8：暂停/恢复"""
        if not self._backend_ready():
            return
        self._on_pause_resume()

    def _on_f9_start(self):
        """F9 启动入口（全局热键 / 窗口按键共用）"""
        if not self._running:
            self._on_start()

    # ─── 菜单栏 ──────────────────────────────────────────────

    def _setup_menu(self):
        menubar = self.menuBar()
        # macOS 的原生全局菜单栏无法容纳 Qt corner widget；使用窗口内菜单栏
        # 才能保证主题按钮在所有桌面平台都位于菜单同行最右侧。
        if sys.platform == "darwin":
            menubar.setNativeMenuBar(False)

        # ── 通用 ──
        settings_menu = menubar.addMenu(tr("通用"))

        settings_mgmt = QAction(tr("配置管理"), self)
        settings_mgmt.triggered.connect(self._open_settings_manager)
        settings_menu.addAction(settings_mgmt)

        user_mgmt = QAction(tr("用户管理"), self)
        user_mgmt.setShortcut("F2")
        user_mgmt.triggered.connect(self._open_user_manager)
        settings_menu.addAction(user_mgmt)

        scene_editor = QAction(tr("场景管理"), self)
        scene_editor.setShortcut("F3")
        scene_editor.triggered.connect(self._open_scene_editor)
        settings_menu.addAction(scene_editor)

        reference_mgr = QAction(tr("图库管理"), self)
        reference_mgr.setShortcut("F4")
        reference_mgr.triggered.connect(self._open_reference_manager)
        settings_menu.addAction(reference_mgr)

        # ── 工具 ──
        tools_menu = menubar.addMenu(tr("工具"))

        ocr_action = QAction(tr("图像识别"), self)
        ocr_action.triggered.connect(self._open_ocr_dialog)
        tools_menu.addAction(ocr_action)

        script_record = QAction(tr("脚本录制"), self)
        script_record.triggered.connect(self._open_script_record)
        tools_menu.addAction(script_record)

        script_editor = QAction(tr("脚本编辑"), self)
        script_editor.triggered.connect(self._open_script_editor)
        tools_menu.addAction(script_editor)

        script_config = QAction(tr("脚本配置"), self)
        script_config.triggered.connect(self._open_script_config)
        tools_menu.addAction(script_config)

        batch_settings = QAction(tr("批量配置"), self)
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
        help_menu = menubar.addMenu(tr("帮助"))

        announcements = QAction(tr("公告"), self)
        announcements.triggered.connect(self._open_announcements)
        help_menu.addAction(announcements)

        check_update = QAction(tr("检查更新"), self)
        check_update.triggered.connect(self._check_update)
        help_menu.addAction(check_update)

        docs = QAction(tr("文档"), self)
        docs.triggered.connect(self._open_docs)
        help_menu.addAction(docs)

        feedback = QAction(tr("反馈"), self)
        feedback.triggered.connect(self._open_feedback)
        help_menu.addAction(feedback)

        help_menu.addSeparator()

        about = QAction(tr("关于"), self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)

        # ── 主题切换（不属于任何插件，固定在菜单栏最右侧）──
        self._theme_button = QToolButton(menubar)
        self._theme_button.setObjectName("themeToggleButton")
        self._theme_button.setAutoRaise(True)
        self._theme_button.setFixedSize(34, 28)
        self._theme_button.clicked.connect(self._toggle_theme)
        manager = get_theme_manager()
        manager.theme_changed.connect(self._update_theme_button)
        self._update_theme_button(manager.current)
        menubar.setCornerWidget(
            self._theme_button, Qt.Corner.TopRightCorner
        )

    def _update_theme_button(self, theme: str) -> None:
        """更新图标和辅助文本，描述按钮点击后的目标主题。"""
        if theme == "dark":
            text = tr("切换到浅色主题")
            self._theme_button.setText("☀")
        else:
            text = tr("切换到深色主题")
            self._theme_button.setText("☾")
        self._theme_button.setToolTip(text)
        self._theme_button.setAccessibleName(text)

    def _toggle_theme(self) -> None:
        manager = get_theme_manager()
        theme = manager.toggle()
        from ..core.config import save_settings
        save_settings({"theme": theme})

    # ─── 对话框 ──────────────────────────────────────────────

    def _open_ocr_dialog(self):
        from .ocr_dialog import OCRDialog
        dialog = OCRDialog(self, refresh_callback=self._refresh_capture)
        dialog.exec()

    def _open_script_record(self):
        """仅通过用户菜单操作打开脚本录制对话框。"""
        from .script_record_dialog import ScriptRecordDialog
        dialog = ScriptRecordDialog(self)
        try:
            dialog.exec()
        finally:
            dialog.stop_f12_hotkey()

    def _open_script_editor(self):
        """打开脚本编辑对话框；有新建/保存/删除时刷新日常页脚本下拉。"""
        from .script_editor_dialog import ScriptEditorDialog
        dialog = ScriptEditorDialog(self)
        dialog.exec()
        if dialog.changed:
            self._load_workflow_configs()

    def _open_script_config(self):
        """打开脚本配置对话框；保存后刷新日常页脚本下拉。"""
        from .script_config_dialog import ScriptConfigDialog
        dialog = ScriptConfigDialog(self)
        if dialog.exec():
            self._load_workflow_configs()

    def _open_scene_editor(self):
        from .scene_editor import SceneEditorDialog
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
                self.statusBar().showMessage(tr("图库已刷新"), 3000)

    def _show_about(self):
        from .about_dialog import AboutDialog
        dialog = AboutDialog(self)
        dialog.exec()

    def _open_announcements(self):
        """打开公告中心：先显示缓存，并在窗口内异步获取最新内容。"""
        from ..core.announcement import load_cached_manifest, mark_notice_version
        from .announcement_dialog import AnnouncementDialog

        dialog = AnnouncementDialog(load_cached_manifest(), parent=self)
        dialog.refresh()
        dialog.exec()
        # 帮助入口中用户已经实际看过当前窗口内容，关闭后记录其版本。
        if dialog.manifest is not None:
            mark_notice_version(dialog.manifest.notice_version)

    def _check_update(self):
        """直接检查更新（帮助菜单 → 检查更新）"""
        from PyQt6.QtWidgets import QMessageBox

        from ..core.update import UpdateChecker, get_version, is_newer_version
        from .update_dialog import UpdateDialog

        checker = UpdateChecker(self)

        def on_finished(release):
            current_version = get_version()

            if is_newer_version(release.version, current_version):
                UpdateDialog(release, self).exec()
            else:
                QMessageBox.information(
                    self,
                    tr("已是最新版本"),
                    tr("当前版本 v{current} 已是最新版本").format(current=current_version),
                )

        def on_error(error_msg: str):
            QMessageBox.warning(self, tr("检查更新失败"), error_msg)

        checker.finished.connect(on_finished)
        checker.error.connect(on_error)
        checker.start()
        self._update_checker = checker  # 防止被 GC

    def _open_docs(self):
        """打开 GitHub 文档"""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        from .about_dialog import GITHUB_REPO
        QDesktopServices.openUrl(QUrl(f"https://github.com/{GITHUB_REPO}/blob/master/docs/60-userguide/README.md"))

    def _open_feedback(self):
        """打开反馈规范与反馈渠道对话框。"""
        from .feedback_dialog import FeedbackDialog
        dialog = FeedbackDialog(self)
        dialog.exec()

    def _open_user_manager(self):
        from .user_manager_dialog import UserManagerDialog
        dialog = UserManagerDialog(self._user_manager, self)
        dialog.exec()
        self._refresh_user_combo()

    def _open_settings_manager(self):
        from .settings_dialog import SettingsDialog
        dialog = SettingsDialog(self)
        dialog.hotkeys_saved.connect(self._apply_hotkey_settings)
        if dialog.exec():
            # 保存后重新加载配置；已创建的输入后端延迟参数在下次创建时生效
            active_hotkeys = self._user_config.hotkeys
            self._user_config = load_user_config()
            # 热键在点击保存时已独立切换；其他配置重载不得
            # 覆盖切换失败时仍可用的运行期键位。
            self._user_config.hotkeys = active_hotkeys
            self.statusBar().showMessage(tr("配置已保存"), 3000)

    def _apply_hotkey_settings(self, values: dict) -> None:
        """保存热键后替换全局监听并刷新相关界面文案。"""
        from ..core.config import HotkeyConfig
        from ..core.platforms import start_global_hotkeys

        hotkeys = HotkeyConfig(**values)
        if hotkeys == self._user_config.hotkeys:
            return
        old_hotkeys = self._user_config.hotkeys
        old_listener = self._hotkey_listener

        # 先启动新监听；创建失败时旧监听仍可用，不会把
        # 当前进程留在无全局热键的半切换状态。
        self._user_config.hotkeys = hotkeys
        try:
            new_listener = start_global_hotkeys(
                self._main_global_hotkey_bindings())
        except Exception as exc:
            self._user_config.hotkeys = old_hotkeys
            logger.error(f"热键立即生效失败: {exc}")
            QMessageBox.warning(
                self, tr("热键设置"),
                tr("新热键已保存，但当前进程重建全局监听失败；"
                   "本次运行继续使用原热键，重启后将重试新设置。"))
            return

        self._hotkey_listener = new_listener
        if old_listener is not None:
            try:
                old_listener.stop()
                old_listener.join(3.0)
                if old_listener.is_alive():
                    logger.warning("旧热键监听线程 3 秒内未退出")
            except Exception as exc:
                logger.warning(f"旧热键监听注销失败: {exc}")

        self._refresh_run_button()
        self._refresh_pause_button()
        status_bar = self.statusBar()
        assert status_bar is not None
        status_bar.showMessage(tr("热键已更新并立即生效"), 3000)

    def _on_toggle_preview(self, checked: bool):
        self.preview_container.setVisible(not checked)
        self.btn_hide_window.setText(tr("显示预览") if checked else tr("隐藏预览"))

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
        from ..core.platforms import DESKTOP_BACKEND_AVAILABLE
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
        from .alert_panel import AlertPanel
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
        _set_combo_character_capacity(self.workflow_combo, 10)
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
        from .batch import BatchTab
        self._batch_tab = BatchTab(host=self)
        self._left_tabs.addTab(self._batch_tab, tr("批量"))

        # ── 插件注入的左侧 Tab（按 -reg 顺序追加）──
        self._add_plugin_tabs(self._left_tabs, "left_tab_builders")

        # 连接 Tab 切换信号，保存当前页签
        self._left_tabs.currentChanged.connect(self._save_tab_indices)

    def _build_right_tabs(self):
        """构建右侧 Tab（通用：运行日志），再追加插件注入的 Tab。"""
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
        """切换暂停/恢复（等价 F8）"""
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

        from ..core.batch_config import load_batch_config
        from .batch import BatchContext, BatchWorker

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
        from .batch import BatchConfigDialog
        dlg = BatchConfigDialog(self)
        if dlg.exec():
            # 保存后刷新批量 Tab 的条目概览和脚本勾选
            self._batch_tab.refresh_config()

    # ─── UI 状态持久化（session.json ui_state 节点，按页面归档）────────

    @staticmethod
    def _migrate_ui_state():
        """一次性迁移：旧扁平 ui_state → 按页面归档嵌套结构"""
        from ..core.config import get_session_store
        store = get_session_store()
        state = store.get_node("ui_state", {})
        if not isinstance(state, dict) or "main_page" in state:
            return  # 已是新格式或为空

        migrated = False

        # main_page
        old_main_keys = {"window_size", "splitter_sizes"}
        if any(k in state for k in old_main_keys):
            page = {}
            for k in old_main_keys:
                if k in state:
                    page[k] = state.pop(k)
            state["main_page"] = page
            migrated = True

        # scene_editor
        se_prefix = "scene_editor_"
        se_keys = [k for k in state if k.startswith(se_prefix)]
        if se_keys:
            se = state.get("scene_editor", {})
            for k in se_keys:
                se[k[len(se_prefix):]] = state.pop(k)
            state["scene_editor"] = se
            migrated = True

        # reference_manager
        if "reference_manager_size" in state:
            rm = state.get("reference_manager", {})
            rm["size"] = state.pop("reference_manager_size")
            state["reference_manager"] = rm
            migrated = True

        if migrated:
            store.set_node("ui_state", state)

    def _restore_ui_state(self):
        """启动时恢复窗口大小、左右分栏比例和当前 Tab 页签"""
        from ..core.config import load_ui_page_state
        page = load_ui_page_state("main_page")
        size = page.get("window_size")
        if isinstance(size, list) and len(size) == 2:
            self.resize(int(size[0]), int(size[1]))
        sizes = page.get("splitter_sizes")
        if isinstance(sizes, list) and len(sizes) == 2 and all(s > 0 for s in sizes):
            self._main_splitter.setSizes([int(s) for s in sizes])
        # 恢复左右 Tab 页签
        left_idx = page.get("left_tab_index", 0)
        right_idx = page.get("right_tab_index", 0)
        if 0 <= left_idx < self._left_tabs.count():
            self._left_tabs.setCurrentIndex(left_idx)
        if 0 <= right_idx < self.tabs.count():
            self.tabs.setCurrentIndex(right_idx)

    def _save_ui_state(self):
        """退出时安全合并 ui_state.main_page。"""
        from ..core.config import update_ui_page_state
        try:
            update_ui_page_state("main_page", {
                "window_size": [self.width(), self.height()],
                "splitter_sizes": self._main_splitter.sizes(),
                "left_tab_index": self._left_tabs.currentIndex(),
                "right_tab_index": self.tabs.currentIndex(),
            })
        except Exception as e:
            logger.warning(f"保存 UI 状态失败: {e}")

    def _save_tab_indices(self):
        """Tab 切换时保存当前左右页签索引"""
        from ..core.config import update_ui_page_state
        try:
            update_ui_page_state("main_page", {
                "left_tab_index": self._left_tabs.currentIndex(),
                "right_tab_index": self.tabs.currentIndex(),
            })
        except Exception as e:
            logger.warning(f"保存 Tab 页签失败: {e}")

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
        """将当前参数面板的值写入 _displayed_script_id 对应的配置项

        仅对 scope=daily 的脚本生效；专用脚本的参数由专属页面管理，
        日常页禁止读写。
        """
        sid = getattr(self, '_displayed_script_id', None)
        if not sid or not self._param_panel or not self._param_panel.isVisible():
            return
        # 找到对应配置项，临时用 _collect_flow_params 的逻辑从面板搜集值
        target_cfg = next((c for c in self._workflow_configs if c["id"] == sid), None)
        if not target_cfg:
            return
        # ⚠️ 专用脚本的参数由专属页面管理，日常页禁止读写
        if target_cfg.get("scope", "daily") != "daily":
            return
        if not target_cfg.get("parameters"):
            return
        params = {}
        from PyQt6.QtWidgets import QCheckBox, QComboBox, QSpinBox, QWidget
        for param_def in target_cfg.get("parameters", []):
            name = param_def["name"]
            # checkgroup：从容器内收集各复选框状态为 dict
            if param_def.get("type") == "checkgroup":
                container = self._param_panel.findChild(QWidget, name)
                if container is not None:
                    group = {}
                    for chk in container.findChildren(QCheckBox):
                        group[chk.objectName()] = chk.isChecked()
                    params[name] = group
                continue
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
        from ..core.config.wf_configs import update_wf_config
        update_wf_config(sid, params)

    def _save_daily_config(self):
        """保存日常页脚本选择；参数由 _save_displayed_params 按脚本字段级落盘

        ⚠️ 警告：禁止在此处添加遍历清理其他工作流 wf_configs 的逻辑。
        各工作流的配置由其专属页面自行管理，日常页只负责自己的 workflow_id。
        擅自清理不归自己管理的配置会破坏其他工作流的数据完整性。
        """
        from ..core.config import get_session_store

        flow_cfg = self._get_selected_flow_config()
        if not flow_cfg:
            return

        # workflow_id 仍存 daily 节点（UI 状态，非工作流配置）
        try:
            get_session_store().update_node("daily", {"workflow_id": flow_cfg["id"]})
        except Exception as e:
            logger.warning(f"保存日常配置失败: {e}")

    def _restore_daily_config(self):
        """启动时恢复日常页脚本选择与参数"""
        from ..core.config import get_session_store
        from ..core.config.wf_configs import get_wf_config

        # 加载 combo 时 block 了信号，参数面板始终为空，必须手动构建
        daily = get_session_store().get_node("daily", {})
        if not isinstance(daily, dict):
            daily = {}
        workflow_id = daily.get("workflow_id")

        # 从统一存储读取各脚本参数；仅对 scope=daily 的脚本生效
        # 专用脚本的参数由专属页面管理，日常页禁止读写
        for cfg in self._workflow_configs:
            if cfg.get("scope", "daily") != "daily":
                continue
            if not cfg.get("parameters"):
                continue
            saved = get_wf_config(cfg["id"])
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
        """重建参数面板

        仅对 scope=daily 的脚本绘制参数面板；专用脚本不画面板，
        其参数由专属配置页面管理。
        """
        while self._param_layout.rowCount() > 0:
            self._param_layout.removeRow(0)
        flow_cfg = self._get_selected_flow_config()
        note = str(flow_cfg.get("note") or "").strip() if flow_cfg else ""
        self._workflow_note_label.setText(f"{tr('说明')}：{note}" if note else "")
        self._workflow_note_label.setVisible(bool(note))
        # ⚠️ 专用脚本不画参数面板
        if flow_cfg and flow_cfg.get("scope", "daily") != "daily":
            self._param_panel.setVisible(False)
            return
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
                spin.setRange(param_def.get("min", 0), param_def.get("max", 999999))
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
            elif param_type == "checkgroup":
                # 分组复选框：值为 dict {key: bool}，使用 FlowLayout 自动换行
                container = _FlowContainer()
                container.setObjectName(name)
                flow = FlowLayout(container, spacing=6)
                flow.setContentsMargins(0, 0, 0, 0)
                container.set_flow_layout(flow)
                if isinstance(default, dict):
                    saved_dict = default
                else:
                    saved_dict = {}
                for opt in options:
                    if isinstance(opt, dict):
                        opt_key = opt["value"]
                        opt_label = opt.get("label", opt_key)
                    else:
                        opt_key = str(opt)
                        opt_label = str(opt)
                    chk = QCheckBox(opt_label)
                    chk.setObjectName(opt_key)
                    chk.setChecked(bool(saved_dict.get(opt_key, True)))
                    flow.addWidget(chk)
                self._param_layout.addRow(label + ":", container)
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
