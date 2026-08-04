"""布局管理混入类 - 布局 CRUD、下拉框、UI 状态"""

from loguru import logger
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ....core.layout_manager import copy_screenshots, delete_screenshots
from ....core.scene_registry import Layout


class LayoutOpsMixin:
    """布局管理混入类

    依赖主类提供:
        _manager, _current_layout, _tabs, _layout_combo, _status_bar,
        _btn_save, _btn_save_as, _btn_delete, _dirty_scenes,
        _set_dirty(), _mark_all_scenes_clean(), _get_dirty_scene_names(),
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
        """统一刷新所有 UI 状态：下拉框、按钮可用性、继承标识"""
        self._refresh_combo()
        active = self._manager.get_active_layout_name()
        has_layout = self._current_layout is not None
        self._btn_save.setEnabled(has_layout)
        self._btn_save_as.setEnabled(has_layout)
        is_active = has_layout and self._current_layout.name == active
        self._btn_delete.setEnabled(has_layout and not is_active)

        # 更新继承标识
        if hasattr(self, "_inherit_label"):
            if has_layout and self._manager.is_alias_layout(self._current_layout.name):
                # 获取父布局名称
                from ....core.config.resolver import get_resolver
                resolver = get_resolver()
                merged = resolver.load_merged("layouts.yaml")
                entry = merged.get("layouts", {}).get(self._current_layout.name) or {}
                parent = entry.get("extends", "")
                if parent:
                    self._inherit_label.setText(f"布局继承自：{parent}")
                    self._inherit_label.show()
                else:
                    self._inherit_label.hide()
            else:
                self._inherit_label.hide()

    def _confirm_discard_changes(self, action: str) -> bool:
        """存在未保存修改时弹窗确认

        Returns:
            True 表示可以继续（已保存或用户选择放弃），False 表示取消操作
        """
        if not self._dirty_scenes:
            return True
        dirty_names = self._get_dirty_scene_names()
        msg = f"当前布局存在未保存的修改，{action}将丢失这些修改。\n是否先保存？"
        if dirty_names:
            msg += f"\n\n当前有如下场景发生变更：{dirty_names}"
        reply = QMessageBox.question(
            self, "未保存的修改", msg,
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Save:
            self._on_save_layout()
            return not self._dirty_scenes  # 保存成功后 dirty_scenes 已清空
        return reply == QMessageBox.StandardButton.Discard

    def _on_combo_changed(self, index: int):
        """下拉框切换时加载对应布局到画布（不激活），切换前检查未保存修改"""
        name = self._layout_combo.currentText()
        if not name:
            return
        if (self._current_layout is not None
                and name != self._current_layout.name
                and not self._confirm_discard_changes(f"切换到布局「{name}」")):
            # 取消：回退下拉框到当前布局，不触发重入
            self._layout_combo.blockSignals(True)
            idx = self._layout_combo.findText(self._current_layout.name)
            if idx >= 0:
                self._layout_combo.setCurrentIndex(idx)
            self._layout_combo.blockSignals(False)
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
        """新建空布局并切换到画布（不自动激活），先检查未保存修改"""
        if not self._confirm_discard_changes("新建布局"):
            return
        name, ok = QInputDialog.getText(self, "新建布局", "请输入布局名称：")
        if not ok or not name:
            return
        name = name.strip()
        if not name:
            return
        if not self._validate_layout_name(name):
            return
        prev_active = self._manager.get_active_layout_name()
        try:
            layout = self._manager.new_layout(name)
        except ValueError as e:
            QMessageBox.warning(self, "新建失败", str(e))
            return
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
        """从所有 Tab 收集数据，增量写入变更的场景文件"""
        if self._current_layout is None:
            self._status_bar.showMessage("没有已加载的布局")
            return
        name = self._current_layout.name
        # 画布配置从当前激活 Tab 获取（用户编辑的是当前 Tab 的画布）
        current_tab = self._current_scene_tab() or next(iter(self._tabs.values()))
        self._current_layout.set_canvas(current_tab.get_canvas_config())
        # 从所有 Tab 收集数据到 Layout 对象（内存操作，始终全量）
        for scene_key, tab in self._tabs.items():
            self._current_layout.set_scene_regions(scene_key, tab.get_regions())
            self._current_layout.set_scene_points(scene_key, tab.get_points())
            self._current_layout.set_scene_arrows(scene_key, tab.get_arrows())
            self._current_layout.set_scene_panels(scene_key, tab.get_panels())
        # 增量写盘：只写变更的场景文件
        changed = set(self._dirty_scenes) if self._dirty_scenes else None
        self._manager.save_layout(self._current_layout, changed_scenes=changed)
        self._update_ui_state()
        total_r = sum(len(tab.get_regions()) for tab in self._tabs.values())
        total_p = sum(len(tab.get_points()) for tab in self._tabs.values())
        total_a = sum(len(tab.get_arrows()) for tab in self._tabs.values())
        total_pn = sum(len(tab.get_panels()) for tab in self._tabs.values())
        saved_info = f"{len(changed)} 个场景" if changed else "全部"
        self._status_bar.showMessage(
            f"已保存布局「{name}」（{saved_info}），"
            f"共 {total_r} 个区域 / {total_p} 个坐标 / {total_a} 个方向 / {total_pn} 个面板"
        )
        self._mark_all_scenes_clean()
        logger.info(
            f"布局已保存: {name} ({saved_info}), "
            f"{total_r} 区域 / {total_p} 坐标 / {total_a} 方向 / {total_pn} 面板"
        )

    def _on_save_as_layout(self):
        """另存为：输入新名称，可选继承当前布局（创建别名）"""
        if self._current_layout is None:
            self._status_bar.showMessage("没有已加载的布局")
            return
        # 别名布局禁止另存为
        if self._manager.is_alias_layout(self._current_layout.name):
            QMessageBox.warning(
                self, "另存为失败",
                "别名布局禁止另存为，请使用原布局另存或者新建布局。",
            )
            return

        # 自定义对话框：名称 + 继承复选框
        dialog = QDialog(self)
        dialog.setWindowTitle("另存为")
        layout = QVBoxLayout(dialog)

        name_input = QLineEdit()
        name_input.setPlaceholderText("请输入布局名称")
        layout.addWidget(name_input)

        inherit_checkbox = QCheckBox(f"继承自当前布局「{self._current_layout.name}」")
        inherit_checkbox.setToolTip(
            "勾选后创建别名布局：仅保存画布配置，场景数据继承自根布局。\n"
            "取消勾选则创建独立副本（包含所有场景数据）。"
        )
        layout.addWidget(inherit_checkbox)

        button_layout = QHBoxLayout()
        ok_button = QPushButton("确定")
        cancel_button = QPushButton("取消")
        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        ok_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        name = name_input.text().strip()
        if not name:
            return
        if not self._validate_layout_name(name):
            return

        inherit = inherit_checkbox.isChecked()
        existing = self._manager.list_layouts()

        if name in existing:
            # 别名布局不可被另存为覆盖（会把场景写入根布局目录，破坏继承语义）
            if self._manager.is_alias_layout(name):
                QMessageBox.warning(
                    self, "另存为失败",
                    f"布局「{name}」是别名布局（继承自根布局），不可被另存为覆盖。\n"
                    f"请使用其他名称。",
                )
                return
            reply = QMessageBox.question(
                self, "确认覆盖",
                f"布局「{name}」已存在，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        if inherit:
            # 创建别名布局
            current_tab = self._current_scene_tab() or next(iter(self._tabs.values()))
            canvas = current_tab.get_canvas_config()
            extends_name = self._current_layout.name
            # 如果当前是别名，继承目标必须是根布局
            if self._manager.is_alias_layout(extends_name):
                QMessageBox.warning(
                    self, "继承失败",
                    f"当前布局「{extends_name}」是别名布局，不能作为继承目标。\n"
                    f"请切换到根布局后再试。",
                )
                return
            new_layout = self._manager.create_alias_layout(name, extends_name, canvas)
            if new_layout is None:
                QMessageBox.warning(self, "创建失败", "别名布局创建失败，请检查日志。")
                return
            self._current_layout = new_layout
            self._refresh_combo()
            idx = self._layout_combo.findText(name)
            if idx >= 0:
                self._layout_combo.setCurrentIndex(idx)
            self._update_ui_state()
            self._status_bar.showMessage(f"已创建别名布局「{name}」（继承自「{extends_name}」）")
            logger.info(f"别名布局已创建: {name} (extends {extends_name})")
        else:
            # 正常另存为：独立副本
            temp = Layout(name="")
            current_tab = self._current_scene_tab() or next(iter(self._tabs.values()))
            temp.set_canvas(current_tab.get_canvas_config())
            for scene_key, tab in self._tabs.items():
                temp.set_scene_regions(scene_key, tab.get_regions())
                temp.set_scene_points(scene_key, tab.get_points())
                temp.set_scene_arrows(scene_key, tab.get_arrows())
                temp.set_scene_panels(scene_key, tab.get_panels())

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
            self._mark_all_scenes_clean()
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
            self._status_bar.showMessage(
                f"删除失败：布局「{name}」不存在或被别名布局引用")
