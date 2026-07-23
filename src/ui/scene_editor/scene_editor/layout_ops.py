"""布局管理混入类 - 布局 CRUD、下拉框、UI 状态"""

from PyQt6.QtWidgets import QInputDialog, QMessageBox
from loguru import logger

from ....core.scene_registry import Layout
from ....core.layout_manager import copy_screenshots, delete_screenshots


class LayoutOpsMixin:
    """布局管理混入类

    依赖主类提供:
        _manager, _current_layout, _tabs, _layout_combo, _status_bar,
        _btn_save, _btn_save_as, _btn_delete, _dirty, _set_dirty(),
        _apply_layout_to_tabs(), _clear_all_tabs(), _update_ui_state()
    """

    # ─── 名称校验 ─────────────────────────────────────────

    def _validate_layout_name(self, name: str) -> bool:
        """校验布局名称是否合法（不含文件系统禁用字符）"""
        invalid_chars = r'\/:*?"<>|'
        for ch in invalid_chars:
            if ch in name:
                QMessageBox.warning(
                    self, "名称不合法",
                    f"布局名称不能包含字符: {ch}\n"
                    f"禁用字符: \\ / : * ? \" < > |",
                )
                return False
        if name.startswith(' ') or name.startswith('.'):
            QMessageBox.warning(
                self, "名称不合法",
                "布局名称不能以空格或点开头",
            )
            return False
        return True

    # ─── 下拉框 + UI 状态 ─────────────────────────────────

    def _refresh_combo(self):
        """刷新下拉框，保持当前选中"""
        current = self._layout_combo.currentText()
        self._layout_combo.blockSignals(True)
        self._layout_combo.clear()
        self._layout_combo.addItems(self._manager.list_layouts())
        idx = self._layout_combo.findText(current)
        if idx >= 0:
            self._layout_combo.setCurrentIndex(idx)
        self._layout_combo.blockSignals(False)

    def _update_ui_state(self):
        """统一刷新所有 UI 状态：下拉框、按钮可用性"""
        self._refresh_combo()
        active = self._manager.get_active_layout_name()
        has_layout = self._current_layout is not None
        self._btn_save.setEnabled(has_layout)
        self._btn_save_as.setEnabled(has_layout)
        is_active = has_layout and self._current_layout.name == active
        self._btn_delete.setEnabled(has_layout and not is_active)

    def _on_combo_changed(self, index: int):
        """下拉框切换时加载对应布局到画布（不激活）"""
        name = self._layout_combo.currentText()
        if not name:
            return
        layout = self._manager.load_layout(name)
        if layout is None:
            return
        self._current_layout = layout
        self._apply_layout_to_tabs()
        self._update_ui_state()
        self._status_bar.showMessage(f"已加载布局「{name}」到画布")

    def _auto_load_active(self):
        """启动时自动加载激活布局"""
        self._refresh_combo()
        name = self._manager.get_active_layout_name()
        if name:
            idx = self._layout_combo.findText(name)
            if idx >= 0:
                self._layout_combo.setCurrentIndex(idx)
            layout = self._manager.load_layout(name)
            if layout:
                self._current_layout = layout
                self._apply_layout_to_tabs()
        self._update_ui_state()

    # ─── 布局 CRUD ────────────────────────────────────────

    def _on_new_layout(self):
        """新建空布局并切换到画布（不自动激活）"""
        name, ok = QInputDialog.getText(self, "新建布局", "请输入布局名称：")
        if not ok or not name:
            return
        name = name.strip()
        if not name:
            return
        if not self._validate_layout_name(name):
            return
        prev_active = self._manager.get_active_layout_name()
        layout = self._manager.new_layout(name)
        if prev_active and prev_active != name:
            self._manager.set_active_layout(prev_active)
        self._current_layout = layout
        self._apply_layout_to_tabs()
        self._refresh_combo()
        idx = self._layout_combo.findText(name)
        if idx >= 0:
            self._layout_combo.setCurrentIndex(idx)
        self._update_ui_state()
        self._status_bar.showMessage(f"已新建布局「{name}」")

    def _on_save_layout(self):
        """从所有 Tab 收集 regions + points + arrows + panels + canvas，全量写入当前布局文件"""
        if self._current_layout is None:
            self._status_bar.showMessage("没有已加载的布局")
            return
        name = self._current_layout.name
        current_tab = next(iter(self._tabs.values()))
        self._current_layout.set_canvas(current_tab.get_canvas_config())
        for scene_key, tab in self._tabs.items():
            self._current_layout.set_scene_regions(scene_key, tab.get_regions())
            self._current_layout.set_scene_points(scene_key, tab.get_points())
            self._current_layout.set_scene_arrows(scene_key, tab.get_arrows())
            self._current_layout.set_scene_panels(scene_key, tab.get_panels())
        self._manager.save_layout(self._current_layout)
        self._update_ui_state()
        total_r = sum(len(tab.get_regions()) for tab in self._tabs.values())
        total_p = sum(len(tab.get_points()) for tab in self._tabs.values())
        total_a = sum(len(tab.get_arrows()) for tab in self._tabs.values())
        total_pn = sum(len(tab.get_panels()) for tab in self._tabs.values())
        self._status_bar.showMessage(
            f"已保存布局「{name}」，共 {total_r} 个区域 / {total_p} 个坐标 / {total_a} 个方向 / {total_pn} 个面板"
        )
        self._set_dirty(False)
        logger.info(
            f"布局已保存: {name}, {total_r} 区域 / {total_p} 坐标 / {total_a} 方向 / {total_pn} 面板"
        )

    def _on_save_as_layout(self):
        """另存为：输入新名称，若已存在则提示确认覆盖"""
        if self._current_layout is None:
            self._status_bar.showMessage("没有已加载的布局")
            return
        temp = Layout(name="")
        current_tab = next(iter(self._tabs.values()))
        temp.set_canvas(current_tab.get_canvas_config())
        for scene_key, tab in self._tabs.items():
            temp.set_scene_regions(scene_key, tab.get_regions())
            temp.set_scene_points(scene_key, tab.get_points())
            temp.set_scene_arrows(scene_key, tab.get_arrows())
            temp.set_scene_panels(scene_key, tab.get_panels())

        existing = self._manager.list_layouts()
        name, ok = QInputDialog.getText(self, "另存为", "请输入布局名称：")
        if not ok or not name:
            return
        name = name.strip()
        if not name:
            return
        if not self._validate_layout_name(name):
            return

        if name in existing:
            reply = QMessageBox.question(
                self, "确认覆盖",
                f"布局「{name}」已存在，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        temp.name = name
        self._manager.save_layout(temp)
        copy_screenshots(self._current_layout.name, name)
        self._current_layout = temp
        self._refresh_combo()
        idx = self._layout_combo.findText(name)
        if idx >= 0:
            self._layout_combo.setCurrentIndex(idx)
        self._update_ui_state()
        total = sum(len(r) for r in temp.scenes.values())
        self._status_bar.showMessage(f"已另存为布局「{name}」，共 {total} 个区域")
        self._set_dirty(False)
        logger.info(f"布局已另存为: {name}, {total} 个区域")

    def _on_delete_layout(self):
        """删除当前下拉框选中的布局（激活的不可删除）"""
        if self._current_layout is None:
            return
        active = self._manager.get_active_layout_name()
        name = self._current_layout.name
        if name == active:
            self._status_bar.showMessage("激活布局不可删除")
            return
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除布局「{name}」吗？此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self._manager.delete_layout(name):
            delete_screenshots(name)
            self._current_layout = None
            self._clear_all_tabs()
            if active:
                layout = self._manager.load_layout(active)
                if layout:
                    self._current_layout = layout
                    self._apply_layout_to_tabs()
            self._layout_combo.blockSignals(True)
            self._refresh_combo()
            idx = self._layout_combo.findText(active) if active else -1
            if idx >= 0:
                self._layout_combo.setCurrentIndex(idx)
            self._layout_combo.blockSignals(False)
            self._update_ui_state()
            self._status_bar.showMessage(f"已删除布局「{name}」，已切换到默认布局")
        else:
            self._status_bar.showMessage(f"删除失败：布局「{name}」不存在")
