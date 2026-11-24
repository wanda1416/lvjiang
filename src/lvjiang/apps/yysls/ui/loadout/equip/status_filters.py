
"""装备状态页的筛选持久化、条件判定、槽位交互与卡片网格重建混入类。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import cast

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QMessageBox,
    QStyle,
    QWidget,
)

from ......i18n import tr
from ....core.affix_cap import equip_affix_cap_pcts
from ....core.equip_parser.dingyin_parser import (
    has_normal_dingyin,
)
from ....core.loadout.models import (
    EQUIPMENT_LAST_SEEN_AT,
    EQUIPMENT_UPDATED_AT,
)
from .cards import _CompactEquipCard
from .mock_dialog import MockEquipDialog
from .status_dialogs import _GRID_COLS, _SLOT_LAYOUT

# 状态展示行样式（与角色详情毕业率卡片一致）
_STATUS_NAME_STYLE = "font-size: 13px; color: palette(mid);"
_STATUS_VALUE_STYLE = "font-size: 15px; font-weight: 600;"




class _EquipFilterMixin:
    def prepare_for_user_change(self) -> None:
        """换用户时先退出批量模式，并记下「筛选待重读」。

        装备数据**不在这里**重载。外层 LoadoutPanel 紧接着会 refresh()，把本轮
        唯一一份 ``EquipmentInventory`` 经 ``refresh_from`` 交给本页——这正是
        ``refresh_from``「不再自己重新读盘」的用意。本页再自己读一次只会拿到
        同样的数据，却要多重建两次整张装备网格。
        """
        self._exit_batch_copy_mode(rebuild=False)
        self._user_filters_stale = True

    def _apply_pending_filters(self) -> None:
        """重读当前用户的筛选：必须在 _inv 就位之后、重建网格之前。

        筛选存在各用户自己的仓储里（``_load_user_filter`` 读的是
        ``self._inv._repo``），早一步读到的是上一个用户的筛选，晚一步则要用
        旧筛选先建一次网格再重建。
        """
        if not self._user_filters_stale:
            return
        self._user_filters_stale = False
        self._load_filter_settings()

    def _load_filter_settings(self):
        """按当前用户加载界面筛选状态并设置下拉框"""
        filters = self._load_user_filter()
        # 屏蔽信号，避免初始化时触发 _on_filter_changed
        self._sort_filter.blockSignals(True)
        self._type_filter.blockSignals(True)
        self._level_filter.blockSignals(True)
        self._affix_filter.blockSignals(True)
        self._source_filter.blockSignals(True)
        self._quality_filter.blockSignals(True)
        self._status_filter.blockSignals(True)
        self._scan_time_filter.blockSignals(True)
        try:
            # 排序
            sort_idx = self._sort_filter.findData(filters.get("sort", "default"))
            self._sort_filter.setCurrentIndex(sort_idx if sort_idx >= 0 else 0)
            # 类型筛选 → 联动槽位选中态
            type_data = filters.get("type", "all")
            type_idx = self._type_filter.findData(type_data)
            self._type_filter.setCurrentIndex(type_idx if type_idx >= 0 else 0)
            self._selected_slot = type_data if type_data != "all" else None
            for key, card in self._slot_cards.items():
                card.set_selected(key == self._selected_slot)
            # 等级筛选
            level_idx = self._level_filter.findData(filters.get("level", "all"))
            self._level_filter.setCurrentIndex(level_idx if level_idx >= 0 else 0)
            # 词条筛选
            affix_idx = self._affix_filter.findData(filters.get("affix", "all"))
            self._affix_filter.setCurrentIndex(affix_idx if affix_idx >= 0 else 0)
            # 来源筛选
            source_idx = self._source_filter.findData(filters.get("source", "all"))
            self._source_filter.setCurrentIndex(source_idx if source_idx >= 0 else 0)
            # 品阶筛选
            quality_idx = self._quality_filter.findData(
                filters.get("quality", "all"))
            self._quality_filter.setCurrentIndex(
                quality_idx if quality_idx >= 0 else 0)
            # 备战方案引用状态筛选
            status_idx = self._status_filter.findData(
                filters.get("status", "all"))
            self._status_filter.setCurrentIndex(
                status_idx if status_idx >= 0 else 0)
            scan_time_idx = self._scan_time_filter.findData(
                filters.get("scan_time", "all"))
            self._scan_time_filter.setCurrentIndex(
                scan_time_idx if scan_time_idx >= 0 else 0)
        finally:
            self._sort_filter.blockSignals(False)
            self._type_filter.blockSignals(False)
            self._level_filter.blockSignals(False)
            self._affix_filter.blockSignals(False)
            self._source_filter.blockSignals(False)
            self._quality_filter.blockSignals(False)
            self._status_filter.blockSignals(False)
            self._scan_time_filter.blockSignals(False)
        self._set_advanced_filters_collapsed(
            bool(filters.get("filters_collapsed", False)))
        self._update_source_actions()

    def _save_filter_settings(self):
        """按当前用户保存界面筛选状态"""
        filters = {
            "sort": self._sort_filter.currentData(),
            "type": self._type_filter.currentData(),
            "level": self._level_filter.currentData(),
            "affix": self._affix_filter.currentData(),
            "source": self._source_filter.currentData(),
            "quality": self._quality_filter.currentData(),
            "status": self._status_filter.currentData(),
            "scan_time": self._scan_time_filter.currentData(),
            "filters_collapsed": self._filters_collapsed,
        }
        self._save_user_filter(filters)

    def _load_user_filter(self) -> dict:
        """读取当前用户的筛选状态。"""
        if self._inv is not None:
            return self._inv._repo.get_ui_state("equip_filter")
        return {}

    def _save_user_filter(self, filters: dict) -> None:
        """保存当前用户的界面偏好，不修改任务使用的装备数据。

        偏好写入失败不能打断点击、筛选和网格刷新；以前直接抛异常会把一次
        普通点击变成未捕获崩溃。
        """
        if self._inv is None:
            return
        try:
            self._inv._repo.set_ui_state("equip_filter", filters)
        except Exception as exc:
            logger.warning(f"保存装备筛选偏好失败: {exc}")
            self._notify_pref_save_failed(exc)

    def _notify_pref_save_failed(self, exc: Exception) -> None:
        """界面偏好保存失败只提示一次，避免每次点击都弹窗。"""
        if getattr(self, "_pref_save_warned", False):
            return
        self._pref_save_warned = True
        QMessageBox.warning(
            cast(QWidget, self), tr("筛选条件未保存"),
            tr("筛选已生效，但保存到用户资料失败：{error}\n\n"
               "本次浏览不受影响，下次操作会再尝试保存。").format(error=exc))

    def _on_filter_changed(self):
        """筛选下拉框变化时触发"""
        # 类型下拉框变化 → 联动槽位选中态
        type_data = self._type_filter.currentData()
        new_slot = type_data if type_data != "all" else None
        if new_slot != self._selected_slot:
            self._selected_slot = new_slot
            for key, card in self._slot_cards.items():
                card.set_selected(key == self._selected_slot)
        self._save_filter_settings()
        self._update_source_actions()
        self._rebuild_grid()

    def _toggle_advanced_filters(self) -> None:
        self._set_advanced_filters_collapsed(
            not self._filters_collapsed)
        self._save_filter_settings()

    def _set_advanced_filters_collapsed(self, collapsed: bool) -> None:
        self._filters_collapsed = collapsed
        self._advanced_filter_widget.setVisible(not collapsed)
        action_text = (
            tr("展开筛选条件") if collapsed
            else tr("收起筛选条件"))
        icon_type = (
            QStyle.StandardPixmap.SP_ArrowRight if collapsed
            else QStyle.StandardPixmap.SP_ArrowLeft)
        self._filter_collapse_button.setText("")
        self._filter_collapse_button.setIcon(
            self._filter_collapse_button.style().standardIcon(icon_type))
        self._filter_collapse_button.setToolTip(action_text)
        self._filter_collapse_button.setAccessibleName(action_text)

    def _update_source_actions(self) -> None:
        is_mock = (self._source_filter.currentData() or "all") == "mock"
        self._btn_batch_copy.setVisible(is_mock)
        self._btn_delete_filtered.setVisible(not is_mock)

    def _get_level_threshold(self) -> int:
        """获取等级筛选阈值，0 表示不筛选"""
        level_str = self._level_filter.currentData()
        return int(level_str) if level_str != "all" else 0

    def _get_affix_filter(self) -> str:
        """获取词条筛选类型。"""
        return self._affix_filter.currentData()

    @staticmethod
    def _effective_scan_time(equip: dict) -> datetime | None:
        """读取扫描时间；历史装备缺失独立字段时回退更新时间。"""
        value = equip.get(EQUIPMENT_LAST_SEEN_AT) or equip.get(
            EQUIPMENT_UPDATED_AT)
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _passes_scan_time_filter(
        self,
        equip: dict,
        *,
        is_mock: bool,
        now: datetime | None = None,
    ) -> bool:
        """模拟装备不受扫描时间约束；真实装备按最后观察时间筛选。"""
        if is_mock:
            return True
        mode = str(self._scan_time_filter.currentData() or "all")
        if mode == "all":
            return True
        scanned_at = _EquipFilterMixin._effective_scan_time(equip)
        if scanned_at is None:
            return True
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return scanned_at < current.astimezone(timezone.utc) - (
            _EquipFilterMixin._scan_time_duration(mode))

    @staticmethod
    def _scan_time_duration(mode: str) -> timedelta:
        """把稳定筛选值换算成展示和删除复核共用的时长。"""
        if mode == "1h":
            return timedelta(hours=1)
        if mode == "1d":
            return timedelta(days=1)
        return timedelta(days=int(mode))

    def _scan_time_delete_constraint(self) -> str | None:
        """把界面筛选转换成仓储层的原子复核条件。"""
        mode = str(self._scan_time_filter.currentData() or "all")
        if mode == "all":
            return None
        cutoff = datetime.now(timezone.utc) - (
            _EquipFilterMixin._scan_time_duration(mode))
        return cutoff.isoformat(timespec="milliseconds")

    def _reset_filter_for_mock(self):
        """创建/复制模拟装备后，自动切换筛选以便新装备可见。

        将来源切换为「模拟」，并清除部位、品阶、词条和状态筛选，确保新建
        装备不会被当前筛选条件隐藏。
        """
        self._source_filter.blockSignals(True)
        self._type_filter.blockSignals(True)
        self._quality_filter.blockSignals(True)
        self._affix_filter.blockSignals(True)
        self._status_filter.blockSignals(True)
        try:
            # 来源切换到「模拟」
            mock_idx = self._source_filter.findData("mock")
            if mock_idx >= 0:
                self._source_filter.setCurrentIndex(mock_idx)
            # 部位筛选清除（回到「全部」）
            all_type_idx = self._type_filter.findData("all")
            if all_type_idx >= 0 and self._type_filter.currentData() != "all":
                self._type_filter.setCurrentIndex(all_type_idx)
                self._selected_slot = None
                for _key, card in self._slot_cards.items():
                    card.set_selected(False)
            # 词条筛选清除（回到「全部」）
            all_affix_idx = self._affix_filter.findData("all")
            if all_affix_idx >= 0 and self._affix_filter.currentData() != "all":
                self._affix_filter.setCurrentIndex(all_affix_idx)
            for combo in (self._quality_filter, self._status_filter):
                all_idx = combo.findData("all")
                if all_idx >= 0:
                    combo.setCurrentIndex(all_idx)
        finally:
            self._source_filter.blockSignals(False)
            self._type_filter.blockSignals(False)
            self._quality_filter.blockSignals(False)
            self._affix_filter.blockSignals(False)
            self._status_filter.blockSignals(False)
        self._save_filter_settings()
        self._update_source_actions()
        self._rebuild_grid()

    def _equip_passes_filter(
        self,
        equip: dict,
        *,
        is_referenced: bool = False,
    ) -> bool:
        """检查装备是否通过筛选条件"""
        # 等级筛选
        level_threshold = self._get_level_threshold()
        if level_threshold > 0:
            equip_level = equip.get("level") or 0
            if isinstance(equip_level, str):
                try:
                    equip_level = int(equip_level)
                except (ValueError, TypeError):
                    equip_level = 0
            if equip_level < level_threshold:
                return False

        # 品阶筛选：白装按产品语义兜底为一切非金、非紫装备。
        quality_filter = self._quality_filter.currentData() or "all"
        quality = str(equip.get("quality") or "")
        if quality_filter in ("gold", "purple") and quality != quality_filter:
            return False
        if quality_filter == "other" and quality in ("gold", "purple"):
            return False

        # 词条筛选
        affix_filter = self._get_affix_filter()
        if affix_filter == "dingyin":
            # 只认普通定音，与毕业率候选池同一口径：止戈目前不算用户要的定音。
            if not has_normal_dingyin(equip):
                return False
        elif affix_filter == "full_tuning":
            # 满调律：5 条非定音词条（affix_1 到 affix_5 都有）
            if not all(equip.get(f"affix_{i}", {}).get("name")
                       for i in range(1, 6)):
                return False
        elif affix_filter == "not_full_tuning":
            affix_count = sum(
                bool(equip.get(f"affix_{i}", {}).get("name"))
                for i in range(1, 6)
            )
            if affix_count > 4:
                return False

        # 状态按是否被任意备战方案引用判断。
        status_filter = self._status_filter.currentData() or "all"
        if status_filter == "referenced" and not is_referenced:
            return False
        if status_filter == "unreferenced" and is_referenced:
            return False
        return True

    # ── 槽位点击 ──

    def _on_slot_clicked(self, slot_key: str):
        if self._selected_slot == slot_key:
            # 再次点击同一槽位 → 取消选中
            self._selected_slot = None
        else:
            self._selected_slot = slot_key

        # 更新所有槽位的选中态
        for key, card in self._slot_cards.items():
            card.set_selected(key == self._selected_slot)

        # 联动类型下拉框
        self._type_filter.blockSignals(True)
        try:
            idx = self._type_filter.findData(slot_key if self._selected_slot else "all")
            self._type_filter.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._type_filter.blockSignals(False)

        self._save_filter_settings()
        self._rebuild_grid()

    def _on_slot_unequip(self, slot_key: str):
        """卸载槽位装备：从 equipped 移回 bag_items 或 mock_items"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(cast(QWidget, self), tr("卸载失败"), tr("没有激活的用户"))
            return
        equip = self._equipped.get(slot_key)
        if not equip:
            return
        inv = self._require_inventory()
        if inv is None:
            return
        try:
            inv.unequip(slot_key)
            self._sync_inv(notify=True)
        except Exception as e:
            logger.error(f"卸载装备失败: {e}")
            QMessageBox.critical(cast(QWidget, self), tr("卸载失败"), str(e))

    def _on_slot_edit(self, slot_key: str):
        """编辑槽位模拟装备，或记录扫描装备的受限养成。"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(cast(QWidget, self), tr("编辑失败"), tr("没有激活的用户"))
            return
        equip = self._equipped.get(slot_key)
        if not equip:
            return
        dialog = MockEquipDialog(equip, parent=self, default_school=self._get_current_school())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.get_result()
        if not result:
            return
        inv = self._require_inventory()
        if inv is None:
            return
        try:
            old_fp = equip.get("_fp", "")
            is_mock = bool((equip.get("_extra") or {}).get("is_mock"))
            if is_mock:
                # 携带旧指纹走「先写新、后清旧」链路，避免遗留孤儿装备。
                inv.replace_equipped_mock(slot_key, old_fp, result)
            else:
                # 真实装备变化会改变指纹，仓储层负责迁移全部方案引用。
                inv.update_real_development(old_fp, result)
            self._sync_inv(notify=True)
        except Exception as e:
            logger.error(f"编辑或养成槽位装备失败: {e}")
            QMessageBox.critical(cast(QWidget, self), tr("编辑失败"), str(e))

    # ── 背包网格 ──

    def _collect_filtered_cards(
        self,
    ) -> list[tuple[dict, str, str, bool, bool]]:
        """返回当前筛选后的未穿戴装备，作为展示与批量删除的唯一口径。"""
        filter_type = None
        batch_copy = getattr(self, "_batch_copy_mode", False)
        if self._selected_slot and not batch_copy:
            for _, _, slot_key, _, group_key in _SLOT_LAYOUT:
                if slot_key == self._selected_slot:
                    filter_type = group_key
                    break

        from ....config import get_game_config
        group_to_part = get_game_config().get_group_to_part()
        referenced_fps = (
            self._inv.referenced_plan_fps if self._inv is not None else set())
        # 当前方案已穿戴装备永远不进入下方列表，也永远不进入筛选删除集合。
        equipped_fps = (
            self._inv.active_plan_fps if self._inv is not None else set())
        source_mode = self._source_filter.currentData() or "all"
        cards: list[tuple[dict, str, str, bool, bool]] = []

        def append_grouped_items(
            grouped: dict,
            *,
            is_mock: bool,
        ) -> None:
            for group_key, items in grouped.items():
                if filter_type is not None and group_key != filter_type:
                    continue
                part_label = group_to_part.get(group_key, group_key)
                for fp, equip in items.items():
                    if fp in equipped_fps and not batch_copy:
                        continue
                    is_referenced = fp in referenced_fps
                    if (not batch_copy
                            and not self._equip_passes_filter(
                        equip, is_referenced=is_referenced,
                    )):
                        continue
                    if (not batch_copy
                            and not self._passes_scan_time_filter(
                                equip, is_mock=is_mock)):
                        continue
                    cards.append((
                        equip, part_label, group_key, is_mock, is_referenced,
                    ))

        if source_mode in ("all", "bag") and not batch_copy:
            append_grouped_items(self._bag_items, is_mock=False)
        if source_mode in ("all", "mock"):
            append_grouped_items(self._mock_items, is_mock=True)
        return cards

    def _rebuild_grid(self):
        cols = self._display_params.get("grid_columns", _GRID_COLS)

        # 清空
        while self._grid.count() > 0:
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for c in range(self._grid.columnCount()):
            self._grid.setColumnStretch(c, 0)
        for c in range(cols):
            self._grid.setColumnStretch(c, 1)

        cards = self._collect_filtered_cards()

        # 排序模式
        sort_mode = self._sort_filter.currentData() or "default"

        def _level_cap_sum(
            item: tuple[dict, str, str, bool, bool],
        ) -> tuple[int, float]:
            equip = item[0]
            level = equip.get("level") or 0
            if isinstance(level, str):
                try:
                    level = int(level)
                except (ValueError, TypeError):
                    level = 0
            return level, sum(equip_affix_cap_pcts(equip))

        # 排序 + 武器分组逻辑
        if sort_mode == "level_desc":
            def _sk(item):
                lv, cs = _level_cap_sum(item)
                return (-lv, -cs)
            ordered = sorted(cards, key=_sk)
        elif sort_mode == "level_asc":
            def _sk(item):
                lv, cs = _level_cap_sum(item)
                return (lv, -cs)
            ordered = sorted(cards, key=_sk)
        else:
            # 默认：保持 bag_items → mock_items 原始顺序
            ordered = cards

        # 武器槽位严格分组：同类型武器归为一组，组内保持排序顺序
        if self._selected_slot in ("main_weapon", "sub_weapon"):
            weapon_type_for_slot = self._get_plan_weapon_type(self._selected_slot)
            if weapon_type_for_slot:
                same_slot_cards = [c for c in ordered if c[0].get("type") == weapon_type_for_slot]
                other_cards = [c for c in ordered if c[0].get("type") != weapon_type_for_slot]
            else:
                same_slot_cards = ordered
                other_cards = []
        else:
            same_slot_cards = ordered
            other_cards = []

        # 填充
        pos = 0
        for equip, part_label, group_key, is_mock, is_loadout in same_slot_cards:
            card = _CompactEquipCard(
                self._display_params, allow_dingyin_switch=True)
            card.set_equip(
                equip, part_label, group_key,
                is_mock=is_mock, is_loadout=is_loadout,
            )
            card.equip_requested.connect(self._on_equip_requested)
            card.edit_requested.connect(self._on_edit_requested)
            card.delete_requested.connect(self._on_delete_requested)
            card.copy_requested.connect(self._on_copy_requested)
            card.lock_requested.connect(self._on_lock_requested)
            card.properties_requested.connect(self._on_properties_requested)
            card.dingyin_requested.connect(self._on_bag_dingyin_requested)
            if self._batch_copy_mode:
                fp = str(equip.get("_fp") or "")
                card.set_selection_mode(
                    True, selected=fp in self._batch_selected_fps)
                card.selection_changed.connect(self._toggle_batch_card)
            self._grid.addWidget(card, pos // cols, pos % cols)
            pos += 1

        # 同槽位装备未填满行时，跳到下一行再放其他装备
        if other_cards and pos % cols != 0:
            pos = (pos // cols + 1) * cols

        for equip, part_label, group_key, is_mock, is_loadout in other_cards:
            card = _CompactEquipCard(
                self._display_params, allow_dingyin_switch=True)
            card.set_equip(
                equip, part_label, group_key,
                is_mock=is_mock, is_loadout=is_loadout,
            )
            card.equip_requested.connect(self._on_equip_requested)
            card.edit_requested.connect(self._on_edit_requested)
            card.delete_requested.connect(self._on_delete_requested)
            card.copy_requested.connect(self._on_copy_requested)
            card.lock_requested.connect(self._on_lock_requested)
            card.properties_requested.connect(self._on_properties_requested)
            card.dingyin_requested.connect(self._on_bag_dingyin_requested)
            if self._batch_copy_mode:
                fp = str(equip.get("_fp") or "")
                card.set_selection_mode(
                    True, selected=fp in self._batch_selected_fps)
                card.selection_changed.connect(self._toggle_batch_card)
            self._grid.addWidget(card, pos // cols, pos % cols)
            pos += 1

        if not cards:
            placeholder = QLabel(tr("暂无数据"))
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(
                "color: palette(mid); font-size: 14px; padding: 40px;")
            self._grid.addWidget(placeholder, 0, 0, 1, cols)

    # ── 数据刷新 ──

