"""场景编辑器 —— 独立任务包（任务场景）加载与退出混入。

提供主场景 ↔ 任务场景双模式切换：
- 点击「加载任务场景」：选择独立任务包，通过 ContextVar 切入该任务包专属的
  ConfigResolver、SceneRegistry 与截图隔离目录（纯切根，不合并全局主场景）。
- 点击「退出任务场景」或关闭窗口：恢复原 ContextVar 作用域，还原主场景布局与 Tab。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
)

from ...core.config.resolver import (
    LAYER_LOCAL,
    reset_scoped_resolver,
    set_scoped_resolver,
)
from ...core.layout.manager import LayoutConfigManager
from ...core.layout.screenshots import (
    reset_scoped_screenshots_dir,
    set_scoped_screenshots_dir,
)
from ...core.scene.registry import (
    reset_scoped_registry,
    set_scoped_registry,
)
from ...i18n import tr
from ...workflows.package import (
    TaskPackage,
    build_package_registry,
    create_package_resolver,
    discover_task_packages,
)
from ..widgets.button_styles import apply_button_style

if TYPE_CHECKING:
    from .dialog import SceneEditorDialog


class _TaskPackagePickerDialog(QDialog):
    """选择要加载到场景管理的独立任务包。"""

    def __init__(self, packages: list[TaskPackage], parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("加载任务场景"))
        self.setMinimumSize(460, 320)
        self._selected: TaskPackage | None = None

        layout = QVBoxLayout(self)
        hint = QLabel(tr("选择包含 manifest.yaml 的独立任务包以查看和编辑其场景与布局："))
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._list = QListWidget()
        for pkg in packages:
            layer_tag = tr("本地") if pkg.layer == LAYER_LOCAL else tr("系统")
            ver = f" v{pkg.version}" if pkg.version else ""
            path_hint = pkg.rel_dir or str(pkg.primary_dir)
            text = f"{pkg.name} ({pkg.id}{ver})  [{layer_tag}]  —  {path_hint}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, pkg)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        self._list.itemDoubleClicked.connect(lambda _item: self._accept_current())
        layout.addWidget(self._list)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_current)
        buttons.rejected.connect(self.reject)
        btn_row.addWidget(buttons)
        layout.addLayout(btn_row)

    def _accept_current(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        self._selected = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def selected_package(self) -> TaskPackage | None:
        return self._selected


class TaskPackageOpsMixin:
    """场景编辑器主场景 / 任务场景双模式切换混入。"""

    _active_task_package: TaskPackage | None
    _task_scope_tokens: tuple[object, object, object] | None
    _saved_main_state: dict | None

    def _init_task_package_state(self: SceneEditorDialog) -> None:
        self._active_task_package = None
        self._task_scope_tokens = None
        self._saved_main_state = None
        self._disabled_parent_for_task_pkg = None

    @property
    def active_task_package(self: SceneEditorDialog) -> TaskPackage | None:
        return self._active_task_package

    def _on_toggle_task_package(self: SceneEditorDialog) -> None:
        """顶栏右上角按钮回调：主场景模式下加载任务场景，任务模式下退出任务场景。"""
        if self._active_task_package is not None:
            self._exit_task_package_mode(restore_ui=True)
            return

        packages = discover_task_packages()
        if not packages:
            QMessageBox.information(
                self,
                tr("未找到独立任务包"),
                tr(
                    "当前 workflows 目录下未发现包含 manifest.yaml 的独立任务包。\n"
                    "请将任务包文件夹放入 config/local/workflows/ 或 config/system/workflows/ 下后重试。"
                ),
            )
            return

        picker = _TaskPackagePickerDialog(packages, self)
        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        pkg = picker.selected_package()
        if pkg is not None:
            self._enter_task_package_mode(pkg)

    def _enter_task_package_mode(self: SceneEditorDialog, pkg: TaskPackage) -> bool:
        """切入指定独立任务包的场景编辑模式。"""
        if not self._confirm_discard_changes(tr("加载任务场景")):
            return False

        # 先完整构造新包对象，失败时完全不影响当前所处模式与作用域
        try:
            pkg_resolver = create_package_resolver(pkg)
            pkg_registry = build_package_registry(pkg, pkg_resolver)
            from ...core.layout.screenshots import SCREENSHOTS_DIR
            pkg_screenshots = SCREENSHOTS_DIR / "_packages" / pkg.id
        except Exception as exc:  # noqa: BLE001
            logger.exception("加载任务场景失败")
            QMessageBox.warning(self, tr("加载任务场景失败"), str(exc))
            return False

        if self._saved_main_state is None:
            self._saved_main_state = {
                "manager": self._manager,
                "layout_key": self._current_layout.key if self._current_layout else "",
                "scene_key": self._get_current_scene_key(),
            }

        # 新包就绪后再释放旧作用域并切入新作用域
        if self._active_task_package is not None:
            self._release_task_scope_tokens()

        res_tok = set_scoped_resolver(pkg_resolver)
        reg_tok = set_scoped_registry(pkg_registry)
        shot_tok = set_scoped_screenshots_dir(pkg_screenshots)
        self._task_scope_tokens = (res_tok, reg_tok, shot_tok)
        self._active_task_package = pkg

        # 冻结父窗口交互：避免任务场景模式占用主线程 ContextVar 期间在主窗口并发操作全局配置
        parent_widget = self.parentWidget()
        if (parent_widget is not None and parent_widget.isEnabled()
                and self._disabled_parent_for_task_pkg is None):
            parent_widget.setEnabled(False)
            self._disabled_parent_for_task_pkg = parent_widget
        self._manager = LayoutConfigManager()

        self._mark_all_scenes_clean()
        self._img_cache.clear()
        self._loaded_scenes.clear()
        self._scene_layout_paths.clear()
        self._current_layout = None

        self._rebuild_group_tabs()
        self._auto_load_active("")
        self._update_info_label()
        self._result_text.clear()

        self._btn_task_package.setText(tr("退出任务场景"))
        self._btn_task_package.setToolTip(tr("退出当前任务场景并返回全局主场景"))
        apply_button_style(self._btn_task_package, variant="danger")
        self.setWindowTitle(f"{tr('场景管理')} [{tr('任务场景')}：{pkg.name}]")
        self._status_bar.showMessage(
            tr("已加载任务场景：{name}").format(name=pkg.name)
        )
        logger.info("场景管理已切入任务场景模式: {} ({})", pkg.name, pkg.id)
        return True

    def _release_task_scope_tokens(self: SceneEditorDialog) -> None:
        tokens = self._task_scope_tokens
        if tokens is not None:
            res_tok, reg_tok, shot_tok = tokens
            self._task_scope_tokens = None
            reset_scoped_screenshots_dir(shot_tok)
            reset_scoped_registry(reg_tok)
            reset_scoped_resolver(res_tok)
        disabled_parent = getattr(self, "_disabled_parent_for_task_pkg", None)
        if disabled_parent is not None:
            self._disabled_parent_for_task_pkg = None
            try:
                disabled_parent.setEnabled(True)
            except RuntimeError:
                pass  # 父窗口已销毁

    def _exit_task_package_mode(
        self: SceneEditorDialog, *, restore_ui: bool = True,
    ) -> bool:
        """退出任务场景模式，恢复主场景作用域与界面状态。"""
        if self._active_task_package is None:
            return True
        if restore_ui and not self._confirm_discard_changes(tr("退出任务场景")):
            return False

        exited_pkg = self._active_task_package
        self._active_task_package = None
        self._release_task_scope_tokens()

        saved = self._saved_main_state
        self._saved_main_state = None
        if saved is not None and saved.get("manager") is not None:
            self._manager = saved["manager"]

        if not restore_ui:
            logger.info("场景管理关闭时已释放任务场景作用域: {}", exited_pkg.id)
            return True

        self._mark_all_scenes_clean()
        self._img_cache.clear()
        self._loaded_scenes.clear()
        self._scene_layout_paths.clear()
        self._current_layout = None

        self._btn_task_package.setText(tr("加载任务场景"))
        self._btn_task_package.setToolTip(tr("加载独立任务包的场景与布局进行查看和调试"))
        apply_button_style(self._btn_task_package, variant="neutral")
        self.setWindowTitle(tr("场景管理"))

        prev_layout = str((saved or {}).get("layout_key") or "")
        prev_scene = str((saved or {}).get("scene_key") or "")
        self._rebuild_group_tabs()
        self._auto_load_active(prev_layout)
        if prev_scene:
            self._select_scene(prev_scene)
        self._update_info_label()
        self._result_text.clear()
        self._status_bar.showMessage(tr("已退出任务场景，恢复主场景"))
        logger.info("场景管理已退出任务场景模式: {} ({})", exited_pkg.name, exited_pkg.id)
        return True
