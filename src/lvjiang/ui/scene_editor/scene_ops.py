"""场景管理混入类 - 分组/场景 CRUD、右键菜单、Tab 排序"""

import re

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTabWidget,
)

from ...core.config.resolver import get_resolver
from ...core.layout_manager import (
    delete_scene_across_all_layouts,
    rename_scene_across_all_layouts,
)
from ...core.scene_registry import (
    get_group_name,
    get_registry,
    get_scene_name,
    reload_scene_registry,
)
from ...i18n import tr
from .scene_tab import SceneTab

# key 格式校验：小写字母开头，仅含小写字母/数字/下划线
_RE_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


class SceneOpsMixin:
    """场景/分组管理混入类

    依赖主类提供:
        _group_tab_widget, _group_tabs, _tabs,
        _current_layout, _status_bar,
        _apply_layout_to_tabs(), _set_dirty()
    """

    # ─── Tab 重建 ────────────────────────────────────────

    def _rebuild_group_tabs(self):
        """重建分组 Tab（一级 Tab）"""
        self._group_tab_widget.blockSignals(True)
        self._group_tab_widget.clear()
        self._group_tabs.clear()
        self._tabs.clear()
        registry = get_registry()
        for group_key, group_name in registry.get_groups():
            scene_tab_widget = QTabWidget()
            scene_tab_widget.setMovable(True)
            scene_tab_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            scene_tab_widget.customContextMenuRequested.connect(
                lambda pos, gk=group_key: self._on_scene_tab_context_menu(pos, gk)
            )
            scene_tab_widget.tabBar().tabMoved.connect(
                lambda from_idx, to_idx, gk=group_key: self._on_scene_tab_moved(from_idx, to_idx, gk)
            )
            scene_tab_widget.currentChanged.connect(self._on_scene_tab_changed)
            self._group_tabs[group_key] = scene_tab_widget
            idx = self._group_tab_widget.addTab(scene_tab_widget, group_name)
            self._group_tab_widget.setTabToolTip(idx, group_key)
            # 构建该分组下的场景 Tab
            self._rebuild_scene_tabs(group_key)
        self._group_tab_widget.blockSignals(False)

    def _rebuild_scene_tabs(self, group_key: str):
        """重建指定分组下的场景 Tab（二级 Tab）"""
        scene_tab_widget = self._group_tabs.get(group_key)
        if scene_tab_widget is None:
            return
        scene_tab_widget.blockSignals(True)
        scene_tab_widget.clear()
        registry = get_registry()
        for scene_key in registry.get_group_scenes(group_key):
            scene_name = get_scene_name(scene_key)
            tab = SceneTab(scene_key)
            tab.on_item_migrated = self._on_item_migrated
            # 恢复 Tab 内部分割器尺寸（延迟应用）
            if hasattr(self, '_pending_tab_split'):
                ps = self._pending_tab_split
                if isinstance(ps, list) and len(ps) == 2 and all(s > 0 for s in ps):
                    tab._splitter.setSizes([int(s) for s in ps])
            self._tabs[scene_key] = tab
            idx = scene_tab_widget.addTab(tab, scene_name)
            scene_tab_widget.setTabToolTip(idx, scene_key)
        scene_tab_widget.blockSignals(False)

    # ─── 当前场景辅助 ────────────────────────────────────

    def _get_current_scene_key(self) -> str:
        """获取当前激活的场景 key"""
        idx = self._group_tab_widget.currentIndex()
        groups = get_registry().get_groups()
        if not (0 <= idx < len(groups)):
            return ""
        group_key = groups[idx][0]
        scene_tab_widget = self._group_tabs.get(group_key)
        if scene_tab_widget is None:
            return ""
        scene_idx = scene_tab_widget.currentIndex()
        widget = scene_tab_widget.widget(scene_idx) if scene_idx >= 0 else None
        return widget.scene_key if isinstance(widget, SceneTab) else ""

    def _current_group_key(self) -> str:
        """获取当前激活的分组 key"""
        idx = self._group_tab_widget.currentIndex()
        groups = get_registry().get_groups()
        if 0 <= idx < len(groups):
            return groups[idx][0]
        return groups[0][0] if groups else ""

    def _select_scene(self, scene_key: str):
        """重建 Tab 后将选中态定位到指定场景（一级分组 + 二级场景）

        Tab 顺序与 registry.get_groups() / get_group_scenes() 一致，据此换算索引。
        """
        if not scene_key:
            return
        registry = get_registry()
        group_key = registry.get_scene_group(scene_key)
        if not group_key:
            return
        groups = registry.get_groups()
        group_idx = next(
            (i for i, (gk, _) in enumerate(groups) if gk == group_key), -1
        )
        if group_idx < 0:
            return
        self._group_tab_widget.setCurrentIndex(group_idx)
        scene_tab_widget = self._group_tabs.get(group_key)
        if scene_tab_widget is None:
            return
        scene_keys = registry.get_group_scenes(group_key)
        if scene_key in scene_keys:
            scene_tab_widget.setCurrentIndex(scene_keys.index(scene_key))

    # ─── 场景 CRUD ────────────────────────────────────────

    def _confirm_structure_change(self, action: str) -> bool:
        """重建场景 Tab 前处理未保存的布局编辑。"""
        return self._confirm_discard_changes(action)

    def _on_new_scene(self):
        """新建场景：弹窗输入 key 和 name，创建到当前分组"""
        current_group = self._current_group_key()
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("新建场景"))
        form = QFormLayout(dialog)
        key_edit = QLineEdit()
        key_edit.setPlaceholderText(tr("英文，如 my_scene"))
        form.addRow(tr("场景 Key:"), key_edit)
        error_label = QLabel()
        error_label.setStyleSheet("color: #c62828;")
        error_label.hide()
        form.addRow("", error_label)
        name_edit = QLineEdit()
        name_edit.setPlaceholderText(tr("中文名称"))
        form.addRow(tr("场景名称:"), name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)

        # 实时校验：非空 + key 格式 + key 重复
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        registry = get_registry()
        def _validate():
            k = key_edit.text().strip()
            n = name_edit.text().strip()
            if not k:
                ok_btn.setEnabled(False)
                error_label.hide()
                return
            if not _RE_KEY.fullmatch(k):
                ok_btn.setEnabled(False)
                error_label.setText(tr("key 必须以小写字母开头，仅含小写字母/数字/下划线"))
                error_label.show()
                return
            if registry.get_scene(k) is not None:
                ok_btn.setEnabled(False)
                error_label.setText(f"场景 Key 已存在: {k}")
                error_label.show()
                return
            if not n:
                ok_btn.setEnabled(False)
                error_label.hide()
                return
            ok_btn.setEnabled(True)
            error_label.hide()
        key_edit.textChanged.connect(_validate)
        name_edit.textChanged.connect(_validate)
        _validate()

        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key = key_edit.text().strip()
        name = name_edit.text().strip()
        if not self._confirm_structure_change(tr("新建场景")):
            return
        registry = get_registry()
        try:
            registry.create_scene(key, name, group_key=current_group)
        except ValueError as e:
            QMessageBox.warning(self, tr("创建失败"), str(e))
            return
        registry.save_group_config()
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        self._select_scene(key)
        self._status_bar.showMessage(f"已创建场景: {name}")

    # ─── 分组管理 ────────────────────────────────────────

    def _on_new_group(self):
        """创建分组：弹窗输入 key 和 name"""
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("新建分组"))
        form = QFormLayout(dialog)
        key_edit = QLineEdit()
        key_edit.setPlaceholderText(tr("英文，如 my_group"))
        form.addRow(tr("分组 Key:"), key_edit)
        error_label = QLabel()
        error_label.setStyleSheet("color: #c62828;")
        error_label.hide()
        form.addRow("", error_label)
        name_edit = QLineEdit()
        name_edit.setPlaceholderText(tr("中文名称"))
        form.addRow(tr("分组名称:"), name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
        # 实时校验：格式 + key 重复
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        registry = get_registry()
        def _validate():
            k = key_edit.text().strip()
            n = name_edit.text().strip()
            if not k:
                ok_btn.setEnabled(False)
                error_label.hide()
                return
            if not _RE_KEY.fullmatch(k):
                ok_btn.setEnabled(False)
                error_label.setText(tr("key 必须以小写字母开头，仅含小写字母/数字/下划线"))
                error_label.show()
                return
            if k in {gk for gk, _ in registry.get_groups()}:
                ok_btn.setEnabled(False)
                error_label.setText(f"分组 Key 已存在: {k}")
                error_label.show()
                return
            if not n:
                ok_btn.setEnabled(False)
                error_label.hide()
                return
            ok_btn.setEnabled(True)
            error_label.hide()
        key_edit.textChanged.connect(_validate)
        name_edit.textChanged.connect(_validate)
        _validate()
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key = key_edit.text().strip()
        name = name_edit.text().strip()
        if not self._confirm_structure_change(tr("新建分组")):
            return
        registry = get_registry()
        try:
            registry.create_group(key, name)
        except ValueError as e:
            QMessageBox.warning(self, tr("创建失败"), str(e))
            return
        registry.save_group_config()
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        self._status_bar.showMessage(f"已创建分组: {name}")

    def _do_rename_group(self, group_key: str):
        """重命名分组（支持修改 key 和名称）"""
        old_name = get_group_name(group_key)
        dialog = QDialog(self)  # type: ignore[arg-type]
        form = QFormLayout(dialog)
        key_edit = QLineEdit(group_key)
        form.addRow(tr("分组 Key:"), key_edit)
        name_edit = QLineEdit(old_name)
        form.addRow(tr("分组名称:"), name_edit)
        error_label = QLabel("")
        error_label.setStyleSheet("color: red;")
        error_label.hide()
        form.addRow(error_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        registry = get_registry()
        existing_keys = {gk for gk, _ in registry.get_groups()}

        def _validate():
            k = key_edit.text().strip()
            n = name_edit.text().strip()
            if not n:
                error_label.hide()
                ok_btn.setEnabled(False)
                return
            if not _RE_KEY.fullmatch(k):
                error_label.setText(tr("key 必须以小写字母开头，仅含小写字母/数字/下划线"))
                error_label.show()
                ok_btn.setEnabled(False)
                return
            if k != group_key and k in existing_keys:
                error_label.setText(f"key 已存在: {k}")
                error_label.show()
                ok_btn.setEnabled(False)
                return
            error_label.hide()
            ok_btn.setEnabled(True)

        key_edit.textChanged.connect(_validate)
        name_edit.textChanged.connect(_validate)
        _validate()
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_key = key_edit.text().strip()
        new_name = name_edit.text().strip()
        if not self._confirm_structure_change(tr("重命名分组")):
            return
        key_changed = new_key != group_key
        try:
            if key_changed:
                registry.rename_group_key(group_key, new_key, new_name)
            else:
                registry.rename_group(group_key, new_name)
        except ValueError as e:
            QMessageBox.warning(self, tr("重命名失败"), str(e))  # type: ignore[arg-type]
            return
        registry.save_group_config()
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        msg = f"已重命名分组: {new_name}"
        if key_changed:
            msg += f" (key: {group_key} → {new_key})"
        self._status_bar.showMessage(msg)

    def _do_delete_group(self, group_key: str):
        """删除空分组"""
        group_name = get_group_name(group_key)
        reply = QMessageBox.question(
            self, tr("确认删除"),  # type: ignore[arg-type]
            f"确定要删除分组「{group_name}」({group_key}) 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if not self._confirm_structure_change(tr("删除分组")):
            return
        registry = get_registry()
        try:
            registry.delete_group(group_key)
            registry.save_group_config()
            reload_scene_registry()
            self._rebuild_group_tabs()
            # 重建会销毁并重新创建全部 SceneTab。必须重新下发布局数据并
            # 清空截图懒加载状态，否则新 Tab 会被旧的 _loaded_scenes
            # 误判为已经加载过截图。
            self._apply_layout_to_tabs()
            # 选中第一个分组第一个场景，触发截图加载
            registry = get_registry()
            for first_group_key, _ in registry.get_groups():
                first_scenes = registry.get_group_scenes(first_group_key)
                if first_scenes:
                    self._select_scene(first_scenes[0])
                    break
            self._status_bar.showMessage(f"已删除分组: {group_name} ({group_key})")
        except ValueError as e:
            QMessageBox.warning(self, tr("删除失败"), str(e))  # type: ignore[arg-type]

    # ─── 分组 Tab 右键菜单 ────────────────────────────────

    def _on_group_tab_context_menu(self, pos):
        """分组 Tab 右键菜单：重命名 / 删除"""
        tab_index = self._group_tab_widget.tabBar().tabAt(pos)
        if tab_index < 0:
            return
        groups = get_registry().get_groups()
        if tab_index >= len(groups):
            return
        group_key, group_name = groups[tab_index]
        menu = QMenu(self)  # type: ignore[arg-type]
        rename_action = menu.addAction(tr("重命名分组"))
        delete_action = menu.addAction(tr("删除分组"))
        # 非空分组不允许删除
        if len(groups) <= 1:
            delete_action.setEnabled(False)
            delete_action.setToolTip(tr("至少需要保留一个场景分组"))
        elif get_registry().get_group_scenes(group_key):
            delete_action.setEnabled(False)
            delete_action.setToolTip(tr("分组非空，无法删除"))
        action = menu.exec(self._group_tab_widget.mapToGlobal(pos))
        if action == rename_action:
            self._do_rename_group(group_key)
        elif action == delete_action:
            self._do_delete_group(group_key)

    def _on_group_tab_moved(self, from_index: int, to_index: int):
        """分组 Tab 拖拽排序后保存新顺序"""
        new_order = []
        for i in range(self._group_tab_widget.count()):
            widget = self._group_tab_widget.widget(i)
            # 查找对应的 group_key
            for gk, tw in self._group_tabs.items():
                if tw is widget:
                    new_order.append(gk)
                    break
        registry = get_registry()
        registry.reorder_groups(new_order)
        registry.save_group_config()
        reload_scene_registry()
        logger.info(f"分组顺序已更新: {new_order}")

    def _on_group_tab_changed(self, index: int):
        """分组 Tab 切换：按需加载新可见场景的底图 + 刷新尺寸信息

        向量数据已在 _apply_layout_to_tabs 时全量下发，无需重新应用布局（
        重新应用会抹除未保存编辑并重读全部截图），只需懒加载当前底图。
        """
        self._ensure_tab_image(self._get_current_scene_key())
        self._update_info_label()

    # ─── 场景 Tab 右键菜单 ────────────────────────────────

    def _on_scene_tab_context_menu(self, pos, group_key: str):
        """场景 Tab 右键菜单：重命名 / 删除 / 更改分组"""
        scene_tab_widget = self._group_tabs.get(group_key)
        if scene_tab_widget is None:
            return
        tab_index = scene_tab_widget.tabBar().tabAt(pos)
        if tab_index < 0:
            return
        scene_keys = get_registry().get_group_scenes(group_key)
        if tab_index >= len(scene_keys):
            return
        scene_key = scene_keys[tab_index]
        menu = QMenu(self)  # type: ignore[call-overload]
        rename_action = menu.addAction(tr("重命名"))
        delete_action = menu.addAction(tr("删除"))
        resolver = get_resolver()
        protected = (
            not resolver.is_dev_mode()
            and resolver.is_system_entity(f"scenes/{scene_key}.yaml")
        )
        if protected:
            delete_action.setEnabled(False)
            delete_action.setToolTip(tr("系统场景不可删除"))
            rename_action.setToolTip(tr("系统场景只能修改显示名称，不能修改 key"))
        # 更改分组子菜单
        move_menu = menu.addMenu(tr("更改分组"))
        registry = get_registry()
        current_group = registry.get_scene_group(scene_key)
        for gk, gn in registry.get_groups():
            if gk != current_group:
                move_action = move_menu.addAction(gn)
                move_action.setData(gk)
        action = menu.exec(scene_tab_widget.mapToGlobal(pos))
        if action == rename_action:
            self._do_rename_scene(scene_key)
        elif action == delete_action:
            self._do_delete_scene(scene_key)
        elif action and action.data() is not None:
            target_group = action.data()
            if target_group:
                self._do_move_scene_group(scene_key, target_group)

    def _do_move_scene_group(self, scene_key: str, target_group: str):
        """移动场景到其他分组"""
        if not self._confirm_structure_change(tr("移动场景")):
            return
        registry = get_registry()
        try:
            registry.move_scene_to_group(scene_key, target_group)
        except ValueError as e:
            QMessageBox.warning(self, tr("移动失败"), str(e))  # type: ignore[arg-type]
            return
        registry.save_group_config()
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        self._select_scene(scene_key)
        target_name = get_group_name(target_group)
        scene_name = get_scene_name(scene_key)
        self._status_bar.showMessage(f"已移动场景「{scene_name}」到分组「{target_name}」")

    def _do_rename_scene(self, scene_key: str):
        """重命名场景（支持修改 key 和名称）"""
        old_name = get_scene_name(scene_key)
        dialog = QDialog(self)  # type: ignore[arg-type]
        dialog.setWindowTitle(tr("重命名场景"))
        form = QFormLayout(dialog)
        key_edit = QLineEdit(scene_key)
        resolver = get_resolver()
        protected = (
            not resolver.is_dev_mode()
            and resolver.is_system_entity(f"scenes/{scene_key}.yaml")
        )
        if protected:
            key_edit.setEnabled(False)
            key_edit.setToolTip(tr("系统场景不能修改 key"))
        form.addRow(tr("场景 Key:"), key_edit)
        name_edit = QLineEdit(old_name)
        form.addRow(tr("场景名称:"), name_edit)
        error_label = QLabel("")
        error_label.setStyleSheet("color: red;")
        error_label.hide()
        form.addRow(error_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        registry = get_registry()
        existing_keys = set(registry.all_scene_keys())

        def _validate():
            k = key_edit.text().strip()
            n = name_edit.text().strip()
            if not n:
                error_label.hide()
                ok_btn.setEnabled(False)
                return
            if not _RE_KEY.fullmatch(k):
                error_label.setText(tr("key 必须以小写字母开头，仅含小写字母/数字/下划线"))
                error_label.show()
                ok_btn.setEnabled(False)
                return
            if k != scene_key and k in existing_keys:
                error_label.setText(f"key 已存在: {k}")
                error_label.show()
                ok_btn.setEnabled(False)
                return
            error_label.hide()
            ok_btn.setEnabled(True)

        key_edit.textChanged.connect(_validate)
        name_edit.textChanged.connect(_validate)
        _validate()
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_key = key_edit.text().strip()
        new_name = name_edit.text().strip()
        if not self._confirm_structure_change(tr("重命名场景")):
            return
        key_changed = new_key != scene_key
        try:
            registry.rename_scene(scene_key, new_key, new_name)
        except (ValueError, PermissionError) as e:
            QMessageBox.warning(self, tr("重命名失败"), str(e))  # type: ignore[arg-type]
            return
        registry.save_group_config()
        # 同步布局和截图文件
        if key_changed:
            rename_scene_across_all_layouts(scene_key, new_key)
            # 重新加载当前布局以刷新 scene key 映射
            if self._current_layout:
                reloaded = self._manager.load_layout(self._current_layout.name)
                if reloaded:
                    self._current_layout = reloaded
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        self._select_scene(new_key)
        msg = f"已重命名场景: {new_name}"
        if key_changed:
            msg += f" (key: {scene_key} → {new_key})"
        self._status_bar.showMessage(msg)

    def _do_delete_scene(self, scene_key: str):
        """删除场景（二次确认）"""
        scene_name = get_scene_name(scene_key)
        reply = QMessageBox.question(
            self, tr("确认删除"),  # type: ignore[arg-type]
            f"确定要删除场景「{scene_name}」({scene_key}) 吗？\n"
            f"这将同时删除场景定义、所有布局标注和截图，且不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if not self._confirm_structure_change(tr("删除场景")):
            return
        registry = get_registry()
        # 删除前记录同分组相邻场景，供删除后定位
        group_key = registry.get_scene_group(scene_key)
        siblings = registry.get_group_scenes(group_key) if group_key else []
        pos = siblings.index(scene_key) if scene_key in siblings else -1
        try:
            registry.delete_scene(scene_key)
        except (ValueError, PermissionError) as e:
            QMessageBox.warning(self, tr("删除失败"), str(e))  # type: ignore[arg-type]
            return
        registry.save_group_config()
        delete_scene_across_all_layouts(scene_key)
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        # 定位到相邻场景（优先同位置，否则末尾）
        remaining = [s for s in siblings if s != scene_key]
        if remaining and pos >= 0:
            self._select_scene(remaining[min(pos, len(remaining) - 1)])
        self._status_bar.showMessage(f"已删除场景: {scene_name}")

    def _on_scene_tab_moved(self, from_index: int, to_index: int, group_key: str):
        """场景 Tab 拖拽排序后保存新顺序"""
        scene_tab_widget = self._group_tabs.get(group_key)
        if scene_tab_widget is None:
            return
        new_order = []
        for i in range(scene_tab_widget.count()):
            widget = scene_tab_widget.widget(i)
            if isinstance(widget, SceneTab):
                new_order.append(widget.scene_key)
        # 合并所有分组的顺序
        registry = get_registry()
        all_order = []
        for gk, _ in registry.get_groups():
            if gk == group_key:
                all_order.extend(new_order)
            else:
                all_order.extend(registry.get_group_scenes(gk))
        registry.save_scene_order(all_order)
        reload_scene_registry()
        logger.info(f"分组 {group_key} 场景顺序已更新: {new_order}")
