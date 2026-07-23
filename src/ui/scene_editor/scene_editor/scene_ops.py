"""场景管理混入类 - 分组/场景 CRUD、右键菜单、Tab 排序"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
    QMenu, QMessageBox, QTabWidget,
)
from loguru import logger

from ....core.scene_registry import (
    get_scene_name, get_registry, reload_scene_registry,
    get_group_name,
)
from ....constants import SCENES_CONFIG_PATH
from .scene_tab import SceneTab


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
            scene_tab_widget.currentChanged.connect(self._update_info_label)
            self._group_tabs[group_key] = scene_tab_widget
            self._group_tab_widget.addTab(scene_tab_widget, group_name)
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
            self._tabs[scene_key] = tab
            scene_tab_widget.addTab(tab, scene_name)
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

    # ─── 场景 CRUD ────────────────────────────────────────

    def _on_new_scene(self):
        """新建场景：弹窗输入 key 和 name，创建到当前分组"""
        current_group = self._current_group_key()
        dialog = QDialog(self)
        dialog.setWindowTitle("新建场景")
        form = QFormLayout(dialog)
        key_edit = QLineEdit()
        key_edit.setPlaceholderText("英文，如 my_scene")
        form.addRow("场景 Key:", key_edit)
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("中文名称")
        form.addRow("场景名称:", name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)

        # 实时校验：非空 + key 格式
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        def _validate():
            k = key_edit.text().strip()
            n = name_edit.text().strip()
            ok_btn.setEnabled(bool(k and n and k.replace("_", "").isalnum()))
        key_edit.textChanged.connect(_validate)
        name_edit.textChanged.connect(_validate)
        _validate()

        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key = key_edit.text().strip()
        name = name_edit.text().strip()
        registry = get_registry()
        try:
            registry.create_scene(key, name, group_key=current_group)
        except ValueError as e:
            QMessageBox.warning(self, "创建失败", str(e))
            return
        registry.save_group_config(SCENES_CONFIG_PATH)
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        self._status_bar.showMessage(f"已创建场景: {name}")

    # ─── 分组管理 ────────────────────────────────────────

    def _on_new_group(self):
        """创建分组：弹窗输入 key 和 name"""
        dialog = QDialog(self)
        dialog.setWindowTitle("新建分组")
        form = QFormLayout(dialog)
        key_edit = QLineEdit()
        key_edit.setPlaceholderText("英文，如 my_group")
        form.addRow("分组 Key:", key_edit)
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("中文名称")
        form.addRow("分组名称:", name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
        # 实时校验
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        def _validate():
            k = key_edit.text().strip()
            n = name_edit.text().strip()
            ok_btn.setEnabled(bool(k and n and k.replace("_", "").isalnum()))
        key_edit.textChanged.connect(_validate)
        name_edit.textChanged.connect(_validate)
        _validate()
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key = key_edit.text().strip()
        name = name_edit.text().strip()
        registry = get_registry()
        try:
            registry.create_group(key, name)
        except ValueError as e:
            QMessageBox.warning(self, "创建失败", str(e))
            return
        registry.save_group_config(SCENES_CONFIG_PATH)
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        self._status_bar.showMessage(f"已创建分组: {name}")

    def _do_rename_group(self, group_key: str):
        """重命名分组（key 不可变）"""
        old_name = get_group_name(group_key)
        dialog = QDialog(self)
        dialog.setWindowTitle("重命名分组")
        form = QFormLayout(dialog)
        key_label = QLineEdit(group_key)
        key_label.setReadOnly(True)
        form.addRow("分组 Key:", key_label)
        name_edit = QLineEdit(old_name)
        form.addRow("分组名称:", name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        def _validate():
            ok_btn.setEnabled(bool(name_edit.text().strip()))
        name_edit.textChanged.connect(_validate)
        _validate()
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_name = name_edit.text().strip()
        registry = get_registry()
        try:
            registry.rename_group(group_key, new_name)
        except ValueError as e:
            QMessageBox.warning(self, "重命名失败", str(e))
            return
        registry.save_group_config(SCENES_CONFIG_PATH)
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        self._status_bar.showMessage(f"已重命名分组: {new_name}")

    def _do_delete_group(self, group_key: str):
        """删除空分组"""
        group_name = get_group_name(group_key)
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除分组「{group_name}」({group_key}) 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        registry = get_registry()
        try:
            registry.delete_group(group_key)
        except ValueError as e:
            QMessageBox.warning(self, "删除失败", str(e))
            return
        registry.save_group_config(SCENES_CONFIG_PATH)
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        self._status_bar.showMessage(f"已删除分组: {group_name}")

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
        menu = QMenu(self)
        rename_action = menu.addAction("重命名分组")
        delete_action = menu.addAction("删除分组")
        # 非空分组不允许删除
        if get_registry().get_group_scenes(group_key):
            delete_action.setEnabled(False)
            delete_action.setToolTip("分组非空，无法删除")
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
        registry.save_group_config(SCENES_CONFIG_PATH)
        reload_scene_registry()
        logger.info(f"分组顺序已更新: {new_order}")

    def _on_group_tab_changed(self, index: int):
        """分组 Tab 切换时，应用布局数据"""
        self._apply_layout_to_tabs()

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
        menu = QMenu(self)
        rename_action = menu.addAction("重命名")
        delete_action = menu.addAction("删除")
        # 更改分组子菜单
        move_menu = menu.addMenu("更改分组")
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
        registry = get_registry()
        try:
            registry.move_scene_to_group(scene_key, target_group)
        except ValueError as e:
            QMessageBox.warning(self, "移动失败", str(e))
            return
        registry.save_group_config(SCENES_CONFIG_PATH)
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        target_name = get_group_name(target_group)
        scene_name = get_scene_name(scene_key)
        self._status_bar.showMessage(f"已移动场景「{scene_name}」到分组「{target_name}」")

    def _do_rename_scene(self, scene_key: str):
        """重命名场景（只允许修改名称，key 不可变）"""
        old_name = get_scene_name(scene_key)
        dialog = QDialog(self)
        dialog.setWindowTitle("重命名场景")
        form = QFormLayout(dialog)
        key_label = QLineEdit(scene_key)
        key_label.setReadOnly(True)
        form.addRow("场景 Key:", key_label)
        name_edit = QLineEdit(old_name)
        form.addRow("场景名称:", name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        def _validate():
            ok_btn.setEnabled(bool(name_edit.text().strip()))
        name_edit.textChanged.connect(_validate)
        _validate()
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_name = name_edit.text().strip()
        registry = get_registry()
        try:
            registry.rename_scene(scene_key, scene_key, new_name)
        except ValueError as e:
            QMessageBox.warning(self, "重命名失败", str(e))
            return
        registry.save_group_config(SCENES_CONFIG_PATH)
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
        self._status_bar.showMessage(f"已重命名场景: {new_name}")

    def _do_delete_scene(self, scene_key: str):
        """删除场景（二次确认）"""
        scene_name = get_scene_name(scene_key)
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除场景「{scene_name}」({scene_key}) 吗？\n"
            f"这将删除场景定义文件，但不会影响布局数据。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        registry = get_registry()
        try:
            registry.delete_scene(scene_key)
        except ValueError as e:
            QMessageBox.warning(self, "删除失败", str(e))
            return
        registry.save_group_config(SCENES_CONFIG_PATH)
        reload_scene_registry()
        self._rebuild_group_tabs()
        self._apply_layout_to_tabs()
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
        registry.save_scene_order(all_order, SCENES_CONFIG_PATH)
        reload_scene_registry()
        logger.info(f"分组 {group_key} 场景顺序已更新: {new_order}")
