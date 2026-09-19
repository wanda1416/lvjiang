"""装备页的「批量复制模拟装备到其他用户」模式。

作为 mix-in 挂在 ``EquipStatusTab`` 上：本模式只在装备页存在，但它的
状态与交互（目标用户菜单、卡片勾选、确认按钮）自成一段，与网格/筛选的
主职责分开维护。宿主需要提供：

- ``_batch_copy_widget`` / ``_filter_widget`` / ``_slot_container`` /
  ``_slot_separator``：切换模式时显隐的容器
- ``_copy_targets_menu`` / ``_btn_copy_targets`` / ``_btn_confirm_batch_copy`` /
  ``_btn_batch_select_all`` / ``_batch_selected_label``：模式内控件
- ``_batch_selected_fps`` / ``_batch_target_users`` / ``_batch_copy_mode``：状态
- ``_collect_filtered_cards()`` / ``_rebuild_grid()`` / ``_update_source_actions()``
"""
from __future__ import annotations

from typing import TYPE_CHECKING, cast

from loguru import logger
from PyQt6.QtWidgets import QMessageBox, QWidget

from ......i18n import tr


class BatchCopyMixin:
    if TYPE_CHECKING:
        _batch_selected_fps: set[str]
        _batch_target_users: set[str]

    def _as_widget(self) -> QWidget:
        return cast(QWidget, self)

    def _enter_batch_copy_mode(self) -> None:
        if (self._source_filter.currentData() or "all") != "mock":
            return
        self._batch_copy_mode = True
        self._batch_selected_fps.clear()
        self._batch_target_users.clear()
        self._filter_widget.setVisible(False)
        self._batch_copy_widget.setVisible(True)
        self._slot_container.setVisible(False)
        self._slot_separator.setVisible(False)
        self._rebuild_copy_targets_menu()
        self._update_batch_copy_controls()
        self._rebuild_grid()

    def _exit_batch_copy_mode(self, *, rebuild: bool = True) -> None:
        if not hasattr(self, "_batch_copy_widget"):
            return
        self._batch_copy_mode = False
        self._batch_selected_fps.clear()
        self._batch_target_users.clear()
        self._batch_copy_widget.setVisible(False)
        self._filter_widget.setVisible(True)
        self._slot_container.setVisible(True)
        self._slot_separator.setVisible(True)
        self._update_source_actions()
        if rebuild:
            self._rebuild_grid()

    def _rebuild_copy_targets_menu(self) -> None:
        self._copy_targets_menu.clear()
        current = self._host.active_user_name()
        for username in self._host.user_manager.list_users():
            if username == current:
                continue
            action = self._copy_targets_menu.addAction(username)
            action.setCheckable(True)
            action.toggled.connect(
                lambda checked, name=username: self._toggle_copy_target(
                    name, checked))

    def _toggle_copy_target(self, username: str, checked: bool) -> None:
        if checked:
            self._batch_target_users.add(username)
        else:
            self._batch_target_users.discard(username)
        self._update_batch_copy_controls()

    def _toggle_batch_card(self, fp: str, checked: bool) -> None:
        if checked:
            self._batch_selected_fps.add(fp)
        else:
            self._batch_selected_fps.discard(fp)
        self._update_batch_copy_controls()

    def _select_all_batch_cards(self) -> None:
        visible = {
            str(equip.get("_fp") or "")
            for equip, _part, _group, is_mock, _referenced
            in self._collect_filtered_cards()
            if is_mock and equip.get("_fp")
        }
        self._batch_selected_fps = (
            set() if visible and visible <= self._batch_selected_fps else visible)
        self._rebuild_grid()
        self._update_batch_copy_controls()

    def _update_batch_copy_controls(self) -> None:
        count = len(self._batch_selected_fps)
        target_count = len(self._batch_target_users)
        visible_fps = {
            str(equip.get("_fp") or "")
            for equip, _part, _group, is_mock, _referenced
            in self._collect_filtered_cards()
            if is_mock and equip.get("_fp")
        }
        self._batch_selected_label.setText(
            tr("已选择 {count} 件").format(count=count))
        self._btn_batch_select_all.setText(
            tr("取消全选")
            if visible_fps and visible_fps <= self._batch_selected_fps
            else tr("全选"))
        target_names = "、".join(sorted(self._batch_target_users))
        self._btn_copy_targets.setText(
            f"{tr('复制到')}：{target_names}" if target_names else tr("复制到…"))
        self._btn_copy_targets.setToolTip(target_names)
        self._btn_confirm_batch_copy.setEnabled(bool(count and target_count))

    def _copy_selected_mocks(self) -> None:
        if not self._batch_selected_fps or not self._batch_target_users:
            return
        source = self._host.active_user_name()
        reply = QMessageBox.question(
            self._as_widget(),
            tr("确认复制"),
            tr("确定将 {items} 件模拟装备复制给 {users} 个用户吗？").format(
                items=len(self._batch_selected_fps),
                users=len(self._batch_target_users),
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            from ....core.loadout import copy_mock_items_to_users
            results = copy_mock_items_to_users(
                source,
                sorted(self._batch_target_users),
                set(self._batch_selected_fps),
            )
        except Exception as exc:
            logger.exception("批量复制模拟装备失败")
            QMessageBox.critical(self._as_widget(), tr("复制失败"), str(exc))
            return
        lines = []
        for result in results:
            if result.error:
                lines.append(tr("{user}：复制失败：{error}").format(
                    user=result.target_username, error=result.error))
            else:
                lines.append(tr(
                    "{user}：新增 {copied}，已存在 {existing}，冲突 {conflicts}"
                ).format(
                    user=result.target_username,
                    copied=result.copied,
                    existing=result.existing,
                    conflicts=result.conflicts,
                ))
        QMessageBox.information(
            self._as_widget(), tr("复制完成"), "\n".join(lines))
        self._exit_batch_copy_mode()
