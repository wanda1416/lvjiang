"""档案总览列管理 Mix-in

ProfileColumnMixin: 列增删、拖拽排序、列宽持久化、表头右键菜单、字段选择器。

列宽按列 key 存在 ``profile.json``（见 :mod:`lvjiang.core.profile.layout`）：
增删列不再需要手工搬宽度数组，用户名列宽只存一份、各分组天然一致。像素宽度
跨机器使用时按度量环境折算后 clamp，折算结果**不回写**文件——只有用户自己
拖动列宽才更新记录，否则两台机器会互相拉扯。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
)

from lvjiang.ui.button_styles import apply_button_style, fit_button_width

from ...core.profile import layout
from ...core.profile.layout import LayoutMetric
from ...core.profile.schema import get_profile_config
from ...core.profile.store import (
    get_groups,
    get_overview_totals,
    insert_overview_columns,
    remove_overview_column,
    reorder_overview_columns,
    replace_overview_column,
    set_group_widths,
    set_overview_totals,
)
from ...i18n import current_language, tr

if TYPE_CHECKING:
    from .tab import ProfileTab

#: 拖动列宽时只更新内存，停下这么久才落盘——拖动会连续触发 sectionResized。
_WIDTH_SAVE_DELAY_MS = 400

#: 表头文字之外的留白（左右边距 + 排序/边框余量）。
_HEADER_PADDING = 24


def metric_for_table(table: QTableWidget) -> LayoutMetric:
    """当前控件的度量环境：解释记录宽度、写回宽度时都带上它。"""
    font = table.font()
    pixel_size = font.pixelSize()
    if pixel_size <= 0:
        point_size = font.pointSizeF()
        pixel_size = round(point_size * table.logicalDpiY() / 72.0) if point_size > 0 else 0
    return LayoutMetric(
        ui_scale=round(float(table.devicePixelRatioF() or 1.0), 3),
        font_px=max(0, int(pixel_size)),
        lang=current_language(),
    )


def header_min_width(table: QTableWidget, column: int) -> int:
    """够显示该列表头的最小宽度；折算后的列宽不得小于它。"""
    header = table.horizontalHeader()
    item = table.horizontalHeaderItem(column)
    text = item.text() if item is not None else ""
    font = header.font() if header is not None else table.font()
    return QFontMetrics(font).horizontalAdvance(text) + _HEADER_PADDING


class ProfileColumnMixin:
    """档案总览列管理 Mix-in。

    依赖主类的 ``self._tables`` / ``self._loading`` / ``self._restoring_widths``
    / ``self._reordering`` 属性。
    """

    # ─── 列管理 ──────────────────────────────────────────────

    def _visible_columns(self: ProfileTab, group_name: str) -> list[str]:  # type: ignore[misc]
        """返回表格当前实际展示的列，而非可能含失效 key 的完整配置。"""
        return list(self._visible_column_keys.get(group_name, []))

    def _configured_index(self: ProfileTab, group_name: str, visible_index: int) -> int | None:  # type: ignore[misc]
        """把 UI 数据列下标转换为持久化 columns 下标。"""
        visible = self._visible_columns(group_name)
        if not (0 <= visible_index < len(visible)):
            return None
        columns = get_groups().get(group_name, {}).get("columns", [])
        try:
            return columns.index(visible[visible_index])
        except ValueError:
            return None

    def _on_columns_reordered(self: ProfileTab, group_name: str, table: QTableWidget):  # type: ignore[misc]
        """拖拽列头后持久化新顺序"""
        if self._reordering or self._loading:
            return
        self._reordering = True
        try:
            h_header = table.horizontalHeader()
            assert h_header is not None
            groups = get_groups()
            group_data = groups.get(group_name, {"columns": []})
            column_keys = list(group_data.get("columns", []))
            visible_keys = self._visible_columns(group_name)
            # 读取新的视觉顺序，跳过第 0 列（用户名）
            new_order = []
            for visual_idx in range(h_header.count()):
                logical_idx = h_header.logicalIndex(visual_idx)
                # logical_idx 0 是用户名，数据列从 1 开始
                data_idx = logical_idx - 1
                if 0 <= data_idx < len(visible_keys):
                    new_order.append(visible_keys[data_idx])

            if visible_keys != new_order:
                # 只重排可见列；当前 schema 中不可见的列保持在原有槽位，
                # 防止一次 UI 操作顺带删除尚未加载的配置。
                reordered = iter(new_order)
                visible_set = set(visible_keys)
                merged_order = [
                    next(reordered) if key in visible_set else key
                    for key in column_keys
                ]
                reorder_overview_columns(group_name, merged_order)
                self._refresh_group(group_name, table)
        finally:
            self._reordering = False

    # ─── 列宽 ────────────────────────────────────────────────

    def _on_column_resized(self: ProfileTab, group_name: str, table: QTableWidget):  # type: ignore[misc]
        """列宽被拖动：按列 key 记录，用户名列宽对所有分组生效。"""
        if self._restoring_widths or self._loading:
            return
        h_header = table.horizontalHeader()
        assert h_header is not None
        if h_header.count() == 0:
            return
        widths: dict[str, int] = {}
        for index, key in enumerate(self._visible_columns(group_name), start=1):
            if index < h_header.count():
                widths[key] = int(h_header.sectionSize(index))
        self._schedule_width_save(group_name, int(h_header.sectionSize(0)), widths)

    def _schedule_width_save(  # type: ignore[misc]
        self: ProfileTab, group_name: str, name_width: int, widths: dict[str, int],
    ) -> None:
        """拖动过程中只攒内存，停手后一次落盘。"""
        pending = getattr(self, "_pending_widths", None)
        if pending is None:
            pending = {}
            self._pending_widths = pending
        pending[group_name] = (name_width, dict(widths))
        timer = getattr(self, "_width_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(_WIDTH_SAVE_DELAY_MS)
            timer.timeout.connect(self._flush_pending_widths)
            self._width_timer = timer
        timer.start()

    def _flush_pending_widths(self: ProfileTab) -> None:  # type: ignore[misc]
        """把攒下的列宽写进结构文件（同一分组以最后一次为准）。"""
        pending = getattr(self, "_pending_widths", {})
        self._pending_widths = {}
        for group_name, (name_width, widths) in pending.items():
            set_group_widths(group_name, name_width, widths)

    def _restore_column_widths(self: ProfileTab, group_name: str, table: QTableWidget):  # type: ignore[misc]
        """按列 key 恢复列宽：记录值折算到当前度量环境后 clamp。"""
        doc = layout.load()
        current = metric_for_table(table)
        layout.set_environment(current)
        group = doc.overview.groups.get(group_name)
        h_header = table.horizontalHeader()
        assert h_header is not None
        visible = self._visible_columns(group_name)
        self._restoring_widths = True
        try:
            header_width = layout.scaled_width(
                group.name_column_width if group else 0,
                stored=doc.metric,
                current=current,
                minimum=header_min_width(table, 0),
            )
            h_header.resizeSection(0, header_width)
            for index, key in enumerate(visible, start=1):
                if index >= h_header.count():
                    break
                raw = group.column_widths.get(key, 0) if group else 0
                h_header.resizeSection(
                    index,
                    layout.scaled_width(
                        raw,
                        stored=doc.metric,
                        current=current,
                        minimum=header_min_width(table, index),
                    ),
                )
        finally:
            self._restoring_widths = False

    def _on_header_context_menu(self: ProfileTab, pos, group_name: str):  # type: ignore[misc]
        """表头右键菜单（分组上下文）"""
        from PyQt6.QtWidgets import QMenu

        table = self._tables.get(group_name)
        if not table:
            return

        h_header = table.horizontalHeader()
        assert h_header is not None
        logical_index = h_header.logicalIndexAt(pos)
        if logical_index < 0:
            return
        menu = QMenu(self)

        if logical_index == 0:
            menu.addAction(tr("右侧新增列"), lambda: self._add_column(group_name, -1))
            # 合计行开关按分组存放：每个分组的表格自己决定要不要这一行。
            menu.addSeparator()
            totals_action = menu.addAction(tr("展示总和行"))
            assert totals_action is not None
            totals_action.setCheckable(True)
            totals_action.setChecked(get_overview_totals(group_name))
            totals_action.triggered.connect(
                lambda checked: self._set_group_totals(group_name, checked))
        else:
            # 数据列索引需要减 1（跳过用户名列）
            data_index = logical_index - 1
            menu.addAction(
                tr("编辑当前列"),
                lambda: self._edit_column_definition(group_name, data_index),
            )
            visible_keys = self._visible_columns(group_name)
            if data_index < len(visible_keys):
                key = visible_keys[data_index]
                config = get_profile_config()
                key_def = config.get_key(key)
                model_type = config.get_model_type(key)
                if key_def is not None and model_type:
                    menu.addAction(
                        tr("查看历史记录"),
                        lambda: self._show_history_dialog(
                            None, model_type, key, key_def.label),
                    )
            menu.addSeparator()
            menu.addAction(tr("右侧新增列"), lambda: self._add_column(group_name, data_index))
            menu.addAction(tr("删除当前列"), lambda: self._remove_column(group_name, data_index))

        menu.exec(h_header.mapToGlobal(pos))

    def _set_group_totals(self: ProfileTab, group_name: str, enabled: bool) -> None:  # type: ignore[misc]
        """切换该分组的合计行并立即重绘；只影响这一个分组。"""
        set_overview_totals(group_name, enabled)
        table = self._tables.get(group_name)
        if table is not None:
            self._refresh_group(group_name, table)

    def _edit_column_definition(self: ProfileTab, group_name: str, data_index: int) -> None:  # type: ignore[misc]
        """直接编辑当前列对应的 key 定义并立即保存。"""
        visible_keys = self._visible_columns(group_name)
        if not (0 <= data_index < len(visible_keys)):
            return

        key = visible_keys[data_index]
        config = get_profile_config()
        key_def = config.get_key(key)
        model_type = config.get_model_type(key)
        if key_def is None or not model_type:
            return

        from .settings_dialog import ProfileDefinitionDialog

        edited = ProfileDefinitionDialog.open_key_editor(
            self,
            model_type,
            key_def,
            {item.key for item in config.get_all_keys()},
        )
        if edited is None:
            return

        # 保存由共享编辑器完成；重命名时也已更新总览列引用。
        self.refresh()

    def _on_header_double_clicked(self: ProfileTab, logical_index: int, group_name: str):  # type: ignore[misc]
        """表头双击：选择字段"""
        # 第 0 列是用户名，不可编辑
        if logical_index == 0:
            return

        config = get_profile_config()
        all_keys = config.get_all_keys()

        if not all_keys:
            QMessageBox.information(self, tr("提示"), tr("没有可用的数据模型 key，请先在数据模型定义中添加"))
            return

        # 数据列索引需要减 1（跳过用户名列）
        data_index = logical_index - 1
        visible_keys = self._visible_columns(group_name)
        current_key = visible_keys[data_index] if data_index < len(visible_keys) else ""

        dialog = QDialog(self)
        dialog.setWindowTitle(tr("选择字段"))
        dialog.setMinimumWidth(250)
        layout_box = QVBoxLayout(dialog)

        selected = [current_key]  # 可变容器，供级联菜单回调
        btn = self._create_key_picker(config, all_keys, current_key, selected)
        layout_box.addWidget(btn)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_ok = QPushButton(tr("确定"))
        btn_ok.clicked.connect(dialog.accept)
        btn_row.addWidget(btn_ok)
        btn_cancel = QPushButton(tr("取消"))
        btn_cancel.clicked.connect(dialog.reject)
        btn_row.addWidget(btn_cancel)
        apply_button_style(btn_ok)
        apply_button_style(btn_cancel, variant="neutral")
        fit_button_width(btn_ok, btn_cancel)
        layout_box.addLayout(btn_row)

        if dialog.exec():
            if selected[0]:
                self._set_column_field(group_name, data_index, selected[0])

    def _add_column(self: ProfileTab, group_name: str, after_index: int):  # type: ignore[misc]
        """在原列右侧一次添加多个字段，取消时不修改任何配置。"""
        from .multi_key_dialog import ProfileKeyMultiSelectDialog

        config = get_profile_config()
        groups = get_groups()
        group_existed = group_name in groups
        used_keys = set(groups.get(group_name, {}).get("columns", []))
        all_keys = [kd for kd in config.get_all_keys() if kd.key not in used_keys]
        if not all_keys:
            QMessageBox.information(self, tr("提示"), tr("当前分组没有可添加的字段"))
            return
        visible = self._visible_columns(group_name)
        after_key = visible[after_index] if 0 <= after_index < len(visible) else None
        dialog = ProfileKeyMultiSelectDialog(config, all_keys, self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            selected = dialog.selected_keys()
        finally:
            dialog.deleteLater()
        if not selected:
            return
        # 编辑器只合并本次新增项，不能覆盖窗口打开期间其他进程更新的整组配置。
        if group_existed and group_name not in get_groups():
            QMessageBox.information(self, tr("提示"), tr("原分组已被移除，请重新选择"))
            return
        try:
            insert_overview_columns(group_name, after_key, selected)
        except ValueError:
            QMessageBox.information(self, tr("提示"), tr("原列已被移除，请重新选择插入位置"))
            return
        # 新列没有宽度记录，重建时按表头实测给默认宽度，无需搬动他人列宽。
        self._refresh_group(group_name, self._tables[group_name])

    def _remove_column(self: ProfileTab, group_name: str, logical_index: int):  # type: ignore[misc]
        """从指定分组中删除指定列"""
        groups = get_groups()
        group_data = groups.get(group_name, {"columns": []})
        column_keys = list(group_data.get("columns", []))
        configured_index = self._configured_index(group_name, logical_index)
        if configured_index is not None:
            removed_key = column_keys[configured_index]
            remove_overview_column(group_name, removed_key)
            self._refresh_group(group_name, self._tables[group_name])

    def _create_key_picker(  # type: ignore[misc]
        self: ProfileTab,
        config, all_keys: list, current_key: str, selected: list,
    ) -> QPushButton:
        """创建级联菜单 key 选择按钮（类型 → 分组 → 定义）。

        实现见 `ui/profile/key_picker.py`：用于替换当前列，
        批量配置的「指定排序」仍复用同一单选控件。

        selected: 可变容器 [key]，选中后更新 selected[0]。
        """
        from .key_picker import create_profile_key_picker

        def _remember(key: str) -> None:
            selected[0] = key

        return create_profile_key_picker(
            config, all_keys, current_key, _remember,
            placeholder=tr("（请选择）"))

    def _set_column_field(self: ProfileTab, group_name: str, logical_index: int, field_key: str):  # type: ignore[misc]
        """设置指定分组的指定列字段"""
        groups = get_groups()
        group_data = groups.get(group_name, {"columns": []})
        column_keys = list(group_data.get("columns", []))
        configured_index = self._configured_index(group_name, logical_index)
        if configured_index is None:
            return

        if field_key in column_keys and column_keys.index(field_key) != configured_index:
            QMessageBox.warning(self, tr("重复"), tr("Key '{key}' 已在该分组中显示").format(key=field_key))
            return

        replace_overview_column(
            group_name, column_keys[configured_index], field_key)
        self._refresh_group(group_name, self._tables[group_name])
