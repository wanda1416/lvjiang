"""档案总览列管理 Mix-in

ProfileColumnMixin: 列增删、拖拽排序、列宽持久化、表头右键菜单、字段选择器。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
)

from lvjiang.core.config import get_session_store
from lvjiang.ui.button_styles import apply_button_style, fit_button_width

from ...core.profile.models import ALL_MODELS, MODEL_LABELS
from ...core.profile.schema import get_profile_config
from ...core.profile.store import (
    get_groups,
    insert_overview_column,
    remove_overview_column,
    reorder_overview_columns,
    replace_overview_column,
)
from ...i18n import tr

if TYPE_CHECKING:
    from .tab import ProfileTab

# 列宽配置存储在 ui_state 下
_COLUMN_WIDTHS_KEY = "profile_overview_column_widths"


def _get_column_widths() -> dict:
    """获取各分组列宽配置 {group_name: [width, ...]}"""
    ui_state = get_session_store().get_node("ui_state", {})
    if isinstance(ui_state, dict):
        return ui_state.get(_COLUMN_WIDTHS_KEY, {})
    return {}


def _save_column_widths(widths: dict) -> None:
    """保存各分组列宽到 ui_state"""
    get_session_store().update_node("ui_state", {_COLUMN_WIDTHS_KEY: widths})


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

    def _on_column_resized(self: ProfileTab, group_name: str, table: QTableWidget):  # type: ignore[misc]
        """列宽拖拽调整后持久化，并同步用户名列宽到所有分组"""
        if self._restoring_widths or self._loading:
            return
        h_header = table.horizontalHeader()
        assert h_header is not None
        widths = [h_header.sectionSize(i) for i in range(h_header.count())]
        all_widths = _get_column_widths()
        all_widths[group_name] = widths

        # 同步用户名列宽（第 0 列）到所有其他分组，并更新持久化数据
        name_col_width = widths[0] if widths else 0
        if name_col_width > 0:
            for other_group, other_table in self._tables.items():
                if other_group == group_name:
                    continue
                other_header = other_table.horizontalHeader()
                if other_header is not None and other_header.count() > 0:
                    self._restoring_widths = True
                    other_header.resizeSection(0, name_col_width)
                    self._restoring_widths = False
                    # 同步更新持久化的列宽，如果没有记录则创建
                    other_widths = all_widths.get(other_group)
                    col_count = other_header.count()
                    if not other_widths or len(other_widths) != col_count:
                        # 创建默认列宽记录
                        other_widths = [other_header.sectionSize(i) for i in range(col_count)]
                        all_widths[other_group] = other_widths
                    other_widths[0] = name_col_width

        _save_column_widths(all_widths)

    def _restore_column_widths(self: ProfileTab, group_name: str, table: QTableWidget):  # type: ignore[misc]
        """恢复指定分组的列宽配置"""
        all_widths = _get_column_widths()
        widths = all_widths.get(group_name)
        if not widths:
            return
        h_header = table.horizontalHeader()
        assert h_header is not None
        col_count = h_header.count()
        if len(widths) != col_count:
            default_w = h_header.defaultSectionSize()
            widths = [*widths[:col_count], *([default_w] * max(0, col_count - len(widths)))]
            all_widths[group_name] = widths
            _save_column_widths(all_widths)
        self._restoring_widths = True
        for idx, w in enumerate(widths):
            h_header.resizeSection(idx, w)
        self._restoring_widths = False

    def _insert_column_width(self: ProfileTab, group_name: str, data_insert_idx: int, table: QTableWidget) -> None:  # type: ignore[misc]
        """新增数据列时同步列宽数组；第 0 列为用户名。"""
        h_header = table.horizontalHeader()
        assert h_header is not None
        all_widths = _get_column_widths()
        widths = list(all_widths.get(group_name) or [])
        if not widths:
            widths = [h_header.sectionSize(i) for i in range(h_header.count())]
        width_idx = max(1, min(data_insert_idx + 1, len(widths)))
        new_width = h_header.defaultSectionSize()
        widths.insert(width_idx, new_width)
        all_widths[group_name] = widths
        _save_column_widths(all_widths)

    def _remove_column_width(self: ProfileTab, group_name: str, data_idx: int, table: QTableWidget) -> None:  # type: ignore[misc]
        """删除数据列时同步列宽数组；第 0 列为用户名。"""
        h_header = table.horizontalHeader()
        assert h_header is not None
        all_widths = _get_column_widths()
        widths = list(all_widths.get(group_name) or [])
        if not widths:
            widths = [h_header.sectionSize(i) for i in range(h_header.count())]
        width_idx = data_idx + 1
        if 0 <= width_idx < len(widths):
            del widths[width_idx]
            all_widths[group_name] = widths
            _save_column_widths(all_widths)

    def _on_header_context_menu(self: ProfileTab, pos, group_name: str):  # type: ignore[misc]
        """表头右键菜单（分组上下文）"""
        from PyQt6.QtWidgets import QMenu

        table = self._tables.get(group_name)
        if not table:
            return

        h_header = table.horizontalHeader()
        assert h_header is not None
        logical_index = h_header.logicalIndexAt(pos)
        menu = QMenu(self)

        if logical_index == 0:
            menu.addAction(tr("右侧新增列"), lambda: self._add_column(group_name, -1))
        else:
            # 数据列索引需要减 1（跳过用户名列）
            data_index = logical_index - 1
            menu.addAction(tr("右侧新增列"), lambda: self._add_column(group_name, data_index))
            menu.addAction(tr("删除当前列"), lambda: self._remove_column(group_name, data_index))

        menu.exec(h_header.mapToGlobal(pos))

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
        layout = QVBoxLayout(dialog)

        selected = [current_key]  # 可变容器，供级联菜单回调
        btn = self._create_key_picker(config, all_keys, current_key, selected)
        layout.addWidget(btn)

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
        layout.addLayout(btn_row)

        if dialog.exec():
            if selected[0]:
                self._set_column_field(group_name, data_index, selected[0])

    def _add_column(self: ProfileTab, group_name: str, after_index: int):  # type: ignore[misc]
        """在指定分组的指定列后新增一列"""
        config = get_profile_config()

        # 过滤掉该分组已有的 key
        groups = get_groups()
        group_data = groups.get(group_name, {"columns": []})
        used_keys = set(group_data.get("columns", []))
        all_keys = [kd for kd in config.get_all_keys() if kd.key not in used_keys]

        if not all_keys:
            QMessageBox.information(self, tr("提示"), tr("没有可用的数据模型 key，请先在数据模型定义中添加"))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(tr("选择字段"))
        dialog.setMinimumWidth(250)
        layout = QVBoxLayout(dialog)

        selected = [""]  # 可变容器，供级联菜单回调
        btn = self._create_key_picker(config, all_keys, "", selected)
        layout.addWidget(btn)

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
        layout.addLayout(btn_row)

        if dialog.exec():
            selected_key = selected[0]
            if selected_key:
                groups = get_groups()
                group_data = groups.get(group_name, {"columns": []})
                column_keys = list(group_data.get("columns", []))
                if selected_key in column_keys:
                    QMessageBox.warning(self, tr("重复"), tr("Key '{key}' 已在该分组中显示").format(key=selected_key))
                    return
                configured_after = self._configured_index(group_name, after_index)
                insert_idx = 0 if configured_after is None else configured_after + 1
                insert_overview_column(group_name, insert_idx, selected_key)
                self._insert_column_width(group_name, after_index + 1, self._tables[group_name])
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
            self._remove_column_width(group_name, logical_index, self._tables[group_name])
            self._refresh_group(group_name, self._tables[group_name])

    def _create_key_picker(  # type: ignore[misc]
        self: ProfileTab,
        config, all_keys: list, current_key: str, selected: list,
    ) -> QPushButton:
        """创建级联菜单 key 选择按钮

        一级菜单：模型类型（配额/再生/库存/备注）
        二级菜单：该类型下的具体 key

        selected: 可变容器 [key]，选中后更新 selected[0]。
        """
        from PyQt6.QtWidgets import QMenu

        def _label_for_key(key: str) -> str:
            kd = config.get_key(key)
            if kd:
                return f"{kd.label} ({kd.key})"
            return key

        btn = QPushButton(_label_for_key(current_key) if current_key else tr("（请选择）"))
        btn.setMinimumWidth(200)
        apply_button_style(btn, variant="neutral")

        def show_menu():
            menu = QMenu(btn)
            # 按模型类型分组
            keys_by_model: dict[str, list] = {}
            for kd in all_keys:
                mt = config.get_model_type(kd.key) or ""
                keys_by_model.setdefault(mt, []).append(kd)

            for mt in ALL_MODELS:
                kds = keys_by_model.get(mt, [])
                if not kds:
                    continue
                model_label = MODEL_LABELS.get(mt, mt)
                submenu = menu.addMenu(model_label)
                for kd in kds:
                    action = submenu.addAction(f"{kd.label} ({kd.key})")
                    action.triggered.connect(
                        lambda checked, k=kd.key: (
                            selected.__setitem__(0, k),
                            btn.setText(_label_for_key(k)),
                        )
                    )

            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

        btn.clicked.connect(show_menu)
        return btn

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
