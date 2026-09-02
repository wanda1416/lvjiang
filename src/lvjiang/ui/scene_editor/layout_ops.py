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

from ...core.config.resolver import (
    LAYER_LOCAL,
    LAYER_SYSTEM,
    EntityOrigin,
    get_resolver,
)
from ...core.key_validation import validate_layout_activation_keys
from ...core.layout_manager import (
    copy_screenshots,
    delete_screenshots,
    scene_layout_rels,
)
from ...core.layout_models import Layout
from ...core.scene_registry import get_registry
from ...i18n import tr
from ..button_styles import apply_button_style, fit_button_width


class LayoutOpsMixin:
    """布局管理混入类

    依赖主类提供:
        _manager, _current_layout, _tabs, _layout_combo, _status_bar,
        _btn_save, _btn_save_as, _btn_delete, _dirty_scenes,
        _set_dirty(), _mark_all_scenes_clean(), _get_dirty_scene_names(),
        _apply_layout_to_tabs(), _clear_all_tabs(), _update_ui_state(),
        _refresh_loaded_subscene_contents()
    """

    # ─── 名称校验 ─────────────────────────────────────────

    def _validate_layout_name(self, name: str) -> bool:
        """校验布局名称是否合法（不含文件系统禁用字符）"""
        invalid_chars = r'\/:*?"<>|'
        for ch in invalid_chars:
            if ch in name:
                QMessageBox.warning(
                    self, tr("名称不合法"),  # type: ignore[arg-type]
                    f"布局名称不能包含字符: {ch}\n"
                    f"禁用字符: \\ / : * ? \" < > |",
                )
                return False
        if name.startswith(' ') or name.startswith('.'):
            QMessageBox.warning(
                self, tr("名称不合法"),  # type: ignore[arg-type]
                tr("布局名称不能以空格或点开头"),
            )
            return False
        return True

    def _validate_layout_keys_for_save(self, layout: Layout) -> bool:
        """保存前向用户展示具体的非法按键绑定。"""
        try:
            validate_layout_activation_keys(layout)
        except ValueError as exc:
            QMessageBox.warning(
                self, tr("保存失败"), str(exc),  # type: ignore[arg-type]
            )
            self._status_bar.showMessage(str(exc))
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
        is_system = bool(
            has_layout
            and not get_resolver().is_dev_mode()
            and self._manager.is_system_layout(self._current_layout.name)
        )
        can_delete = has_layout and not is_active and not is_system
        self._btn_delete.setEnabled(can_delete)
        if is_system:
            self._btn_delete.setToolTip(tr("系统布局不可删除；不使用时切换到其他布局即可"))
        elif is_active:
            self._btn_delete.setToolTip(tr("当前激活布局不可删除"))
        else:
            self._btn_delete.setToolTip("")

        # 更新继承标识
        if hasattr(self, "_inherit_label"):
            if has_layout and self._manager.is_alias_layout(self._current_layout.name):
                # 获取父布局名称
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

    def _system_save_overrides(
            self, layout_name: str,
            written: set[str]) -> dict[str, EntityOrigin]:
        """返回开发模式写入 system 后仍在生效的更高优先级副本。

        同时覆盖 local 与 remote 两种情况，并经批量路径入口只解析一次
        ``layouts.yaml``。用户模式写入 local，本身就是最高优先级，无需提示。
        """
        resolver = get_resolver()
        if not resolver.is_dev_mode() or not written:
            return {}
        paths = scene_layout_rels(layout_name, sorted(written))
        result = {}
        for scene_key, rel_path in paths.items():
            origin = resolver.describe_entity(rel_path)
            if origin.layer and origin.layer != LAYER_SYSTEM:
                result[scene_key] = origin
        return result

    def _sync_loaded_tabs_to_current_layout(self) -> None:
        """把已创建编辑器里的最新数据合并回完整 Layout 快照。"""
        if self._current_layout is None:
            return
        current_tab = self._current_scene_tab() or next(
            iter(self._tabs.values()), None)
        if current_tab is not None:
            scene = get_registry().get_scene(current_tab.scene_key)
            if scene and scene.is_subscene:
                self._current_layout.set_scene_crop_canvas(
                    current_tab.scene_key, current_tab.get_canvas_config())
            else:
                self._current_layout.set_canvas(current_tab.get_canvas_config())
        for scene_key, tab in self._tabs.items():
            self._current_layout.set_scene_regions(scene_key, tab.get_regions())
            self._current_layout.set_scene_points(scene_key, tab.get_points())
            self._current_layout.set_scene_arrows(scene_key, tab.get_arrows())
            self._current_layout.set_scene_panels(scene_key, tab.get_panels())
            self._current_layout.set_scene_subscene_refs(
                scene_key, tab.get_subscene_refs())
            scene = get_registry().get_scene(scene_key)
            if scene and scene.is_subscene:
                self._current_layout.set_scene_crop_canvas(
                    scene_key, tab.get_canvas_config())
        # 父场景画布持有子场景实体的只读投影。Layout 快照更新后必须同步
        # 重建，否则已加载的父页会一直绘制首次进入时的旧坐标。
        self._refresh_loaded_subscene_contents()

    def _clone_current_layout(self, name: str) -> Layout | None:
        """克隆当前完整布局；未访问场景继续来自内存中的 Layout 快照。"""
        if self._current_layout is None:
            return None
        self._sync_loaded_tabs_to_current_layout()
        clone = Layout.from_dict(name, self._current_layout.to_dict())
        clone.desc = self._current_layout.desc
        clone.name = name
        return clone

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
            self, tr("未保存的修改"), msg,  # type: ignore[arg-type]
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
                # 下面会显式加载一次；这里若放行 currentIndexChanged，非首项
                # 的激活布局会先经 _on_combo_changed 加载一遍，再重复加载。
                self._layout_combo.blockSignals(True)
                self._layout_combo.setCurrentIndex(idx)
                self._layout_combo.blockSignals(False)
            layout = self._manager.load_layout(name)
            if layout:
                self._current_layout = layout
                self._apply_layout_to_tabs()
        self._update_ui_state()

    # ─── 布局 CRUD ────────────────────────────────────────

    def _on_new_layout(self):
        """新建空布局并切换到画布（不自动激活），先检查未保存修改"""
        if not self._confirm_discard_changes(tr("新建布局")):
            return
        name, ok = QInputDialog.getText(self, tr("新建布局"), tr("请输入布局名称："))
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
            QMessageBox.warning(self, tr("新建失败"), str(e))
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
            self._status_bar.showMessage(tr("没有已加载的布局"))
            return
        name = self._current_layout.name
        self._sync_loaded_tabs_to_current_layout()
        if not self._validate_layout_keys_for_save(self._current_layout):
            return
        # 增量写盘：只写变更的场景文件。
        #
        # 恒传集合，**不能在没有脏场景时传 None** —— save_layout 的 None 是
        # 「全量写盘」语义（供新建/另存为用）。保存按钮不受 dirty 状态控制，
        # 于是"什么都没改、随手点一下保存"会把该布局的全部场景写一遍；
        # 用户模式下那就是给每个场景生成 local 影子，而实体文件是整文件
        # 影子（local 有就完全顶掉系统与在线下发，不合并），等于一次点击
        # 把整个布局永久冻住。
        changed = set(self._data_dirty_scenes)
        layout_versions = {
            scene_key: tab.pending_layout_version
            for scene_key, tab in self._tabs.items()
            if tab.pending_layout_version is not None
        }
        scene_versions = {
            scene_key: tab.pending_scene_version
            for scene_key, tab in self._tabs.items()
            if tab.pending_scene_version is not None
        }
        if not self._manager.save_layout(
                self._current_layout,
                changed_scenes=changed,
                content_versions=layout_versions):
            self._status_bar.showMessage(f"保存布局「{name}」失败，请检查日志")
            return
        registry = get_registry()
        for scene_key, version in scene_versions.items():
            registry.save_scene_content_version(scene_key, version)
        self._update_ui_state()
        total_r = sum(len(items) for items in self._current_layout.regions.values())
        total_p = sum(len(items) for items in self._current_layout.points.values())
        total_a = sum(len(items) for items in self._current_layout.arrows.values())
        total_pn = sum(len(items) for items in self._current_layout.panels.values())
        # changed 恒为集合；空集表示没有场景需要写盘（画布/描述等布局级
        # 改动仍会经 layouts.yaml 落盘），不再是"全部"的意思。
        saved_info = f"{len(changed)} 个场景" if changed else tr("无场景改动")
        version_count = len(layout_versions) + len(scene_versions)
        if version_count:
            saved_info += tr("，提升 {count} 项版本").format(count=version_count)
        overrides = self._system_save_overrides(name, changed)
        if overrides:
            local_scenes = [key for key, origin in overrides.items()
                            if origin.layer == LAYER_LOCAL]
            remote_scenes = [key for key, origin in overrides.items()
                             if origin.layer != LAYER_LOCAL]
            reasons = []
            if local_scenes:
                reasons.append(tr("{scenes} 仍由本地副本生效，请先还原本地覆盖")
                               .format(scenes="、".join(local_scenes)))
            if remote_scenes:
                reasons.append(tr("{scenes} 仍由远程版本生效，请提升版本后再保存")
                               .format(scenes="、".join(remote_scenes)))
            self._status_bar.showMessage(tr(
                "已保存布局「{name}」到系统层，但尚未生效：{reasons}"
            ).format(name=name, reasons="；".join(reasons)))
        else:
            self._status_bar.showMessage(
                f"已保存布局「{name}」（{saved_info}），"
                f"共 {total_r} 个区域 / {total_p} 个坐标 / {total_a} 个方向 / {total_pn} 个面板"
            )
        self._mark_all_scenes_clean()
        # 显式版本提升随保存落盘，标识要跟着刷新。
        for tab in self._tabs.values():
            tab._refresh_version_info()
        logger.info(
            f"布局已保存: {name} ({saved_info}), "
            f"{total_r} 区域 / {total_p} 坐标 / {total_a} 方向 / {total_pn} 面板"
        )

    def _on_save_as_layout(self):
        """另存为：输入新名称，可选继承当前布局（创建别名）"""
        if self._current_layout is None:
            self._status_bar.showMessage(tr("没有已加载的布局"))
            return
        # 别名布局禁止另存为
        if self._manager.is_alias_layout(self._current_layout.name):
            QMessageBox.warning(
                self, tr("另存为失败"),
                tr("别名布局禁止另存为，请使用原布局另存或者新建布局。"),
            )
            return

        # 自定义对话框：名称 + 继承复选框
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("另存为"))
        layout = QVBoxLayout(dialog)

        name_input = QLineEdit()
        name_input.setPlaceholderText(tr("请输入布局名称"))
        layout.addWidget(name_input)

        inherit_checkbox = QCheckBox(f"继承自当前布局「{self._current_layout.name}」")
        inherit_checkbox.setToolTip(
            "勾选后创建别名布局：仅保存画布配置，场景数据继承自根布局。\n"
            "取消勾选则创建独立副本（包含所有场景数据）。"
        )
        layout.addWidget(inherit_checkbox)

        button_layout = QHBoxLayout()
        ok_button = QPushButton(tr("确定"))
        cancel_button = QPushButton(tr("取消"))
        apply_button_style(ok_button)
        apply_button_style(cancel_button, variant="neutral")
        fit_button_width(ok_button, cancel_button)
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
                    self, tr("另存为失败"),
                    f"布局「{name}」是别名布局（继承自根布局），不可被另存为覆盖。\n"
                    f"请使用其他名称。",
                )
                return
            reply = QMessageBox.question(
                self, tr("确认覆盖"),
                f"布局「{name}」已存在，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        if inherit:
            # 创建别名布局
            current_tab = self._current_scene_tab() or next(iter(self._tabs.values()), None)
            canvas = (
                current_tab.get_canvas_config()
                if current_tab is not None
                else self._current_layout.get_canvas()
            )
            extends_name = self._current_layout.name
            # 如果当前是别名，继承目标必须是根布局
            if self._manager.is_alias_layout(extends_name):
                QMessageBox.warning(
                    self, tr("继承失败"),
                    f"当前布局「{extends_name}」是别名布局，不能作为继承目标。\n"
                    f"请切换到根布局后再试。",
                )
                return
            new_layout = self._manager.create_alias_layout(name, extends_name, canvas)
            if new_layout is None:
                QMessageBox.warning(self, tr("创建失败"), tr("别名布局创建失败，请检查日志。"))
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
            # 未访问过的场景没有 SceneTab，但完整数据始终保留在当前 Layout。
            # 从它克隆，不能只复制已创建的控件，否则另存为会静默丢场景。
            temp = self._clone_current_layout(name)
            if temp is None:
                return
            if not self._validate_layout_keys_for_save(temp):
                return
            if not self._manager.save_layout(temp):
                QMessageBox.warning(self, tr("另存为失败"), tr("布局写入失败，请检查日志。"))
                return
            copy_screenshots(self._current_layout.name, name)
            self._current_layout = temp
            self._refresh_combo()
            idx = self._layout_combo.findText(name)
            if idx >= 0:
                self._layout_combo.setCurrentIndex(idx)
            self._update_ui_state()
            total = sum(len(r) for r in temp.regions.values())
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
            self._status_bar.showMessage(tr("激活布局不可删除"))
            return
        reply = QMessageBox.question(
            self, tr("确认删除"),
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
