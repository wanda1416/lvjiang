"""菜单栏混入类 - 菜单构建、主题切换按钮与各对话框入口

对话框一律懒加载（import 写在方法体内）：主窗口启动时没必要把场景编辑器、
图库管理、脚本编辑器这些重量级模块全部拉起来。

依赖主类提供：
    _layout_manager / _user_manager / _user_config / _hotkey_listener、
    _refresh_capture / _refresh_layout_combo / _refresh_user_combo /
    _load_workflow_configs / _refresh_run_button / _refresh_pause_button /
    _main_global_hotkey_bindings / _open_batch_config、
    preview_container / btn_hide_window
"""

import sys
from typing import cast

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMessageBox, QToolButton, QWidget

from lvjiang.apps import get_registry

from ...core.config import load_user_config
from ...i18n import tr
from ..theme import get_theme_manager


class MenuOpsMixin:
    """菜单栏与对话框入口混入类"""

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
        from ...core.config import save_settings
        save_settings({"theme": theme})

    # ─── 对话框 ──────────────────────────────────────────────

    def _open_ocr_dialog(self):
        from ..ocr import OCRDialog
        dialog = OCRDialog(self, refresh_callback=self._refresh_capture)
        dialog.exec()

    def _open_script_record(self):
        """仅通过用户菜单操作打开脚本录制对话框。"""
        from ..scripts import ScriptRecordDialog
        dialog = ScriptRecordDialog(self)
        try:
            dialog.exec()
        finally:
            dialog.stop_f12_hotkey()

    def _open_script_editor(self):
        """打开脚本编辑对话框；有新建/保存/删除时刷新日常页脚本下拉。"""
        from ..scripts import ScriptEditorDialog
        dialog = ScriptEditorDialog(self)
        dialog.exec()
        if dialog.changed:
            self._load_workflow_configs()

    def _open_script_config(self):
        """打开脚本配置对话框；保存后刷新日常页脚本下拉。"""
        from ..scripts import ScriptConfigDialog
        dialog = ScriptConfigDialog(self)
        if dialog.exec():
            self._load_workflow_configs()

    def _open_scene_editor(self):
        from ..scene_editor import SceneEditorDialog
        dialog = SceneEditorDialog(
            layout_manager=self._layout_manager,
            refresh_callback=self._refresh_capture,
            parent=self,
        )
        dialog.exec()
        self._refresh_layout_combo()

    def _open_reference_manager(self):
        from ..reference import ReferenceManagerDialog
        dialog = ReferenceManagerDialog(parent=self, screenshot_callback=self._refresh_capture)
        dialog.exec()
        if dialog.data_changed:
            from ...workflows.base import BaseWorkflow
            if BaseWorkflow._shared_material_recognizer is not None:
                BaseWorkflow._shared_material_recognizer.reload()
                self.statusBar().showMessage(tr("图库已刷新"), 3000)

    def _show_about(self):
        from ..notices.about_dialog import AboutDialog
        dialog = AboutDialog(self)
        dialog.exec()

    def _open_announcements(self):
        """打开公告中心：先显示缓存，并在窗口内异步获取最新内容。"""
        from ...core.announcement import load_cached_manifest, mark_notice_version
        from ..notices.announcement_dialog import AnnouncementDialog

        dialog = AnnouncementDialog(load_cached_manifest(), parent=self)
        dialog.refresh()
        dialog.exec()
        # 帮助入口中用户已经实际看过当前窗口内容，关闭后记录其版本。
        if dialog.manifest is not None:
            mark_notice_version(dialog.manifest.notice_version)

    def _check_update(self):
        """直接检查更新（帮助菜单 → 检查更新）"""
        from PyQt6.QtWidgets import QMessageBox

        from ...core.update import UpdateChecker, get_version, is_newer_version
        from ..notices.update_dialog import UpdateDialog

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
            QMessageBox.warning(
                cast(QWidget, self), tr("检查更新失败"), error_msg)

        checker.finished.connect(on_finished)
        checker.error.connect(on_error)
        checker.start()
        self._update_checker = checker  # 防止被 GC

    def _open_docs(self):
        """打开 GitHub 文档"""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        from ...core.update import GITHUB_REPO
        QDesktopServices.openUrl(QUrl(f"https://github.com/{GITHUB_REPO}/blob/master/docs/60-userguide/README.md"))

    def _open_feedback(self):
        """打开反馈规范与反馈渠道对话框。"""
        from ..notices.feedback_dialog import FeedbackDialog
        dialog = FeedbackDialog(self)
        dialog.exec()

    def _open_user_manager(self):
        from ..user_manager_dialog import UserManagerDialog
        dialog = UserManagerDialog(self._user_manager, self)
        dialog.exec()
        self._refresh_user_combo()

    def _open_settings_manager(self):
        from ..settings_dialog import SettingsDialog
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
        from ...core.config import HotkeyConfig
        from ...core.platforms import start_global_hotkeys

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
                cast(QWidget, self), tr("热键设置"),
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
