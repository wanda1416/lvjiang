"""装备配置中的套装定义表。

数据仍使用 ``equipment_sets.left/right`` 的稳定 key；界面把左四套装
称为“输出套装”、右四套装称为“防具套装”。防具页的“关联套装”
是 ``left.recommended_right`` 的反向编辑视图，不另存一份平行关系。
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .....i18n import tr


class EquipmentSetEditor(QFrame):
    """嵌入装备配置“输出/防具”页的套装表。"""

    def __init__(
        self,
        parent=None,
        *,
        data: dict,
        on_changed: Callable[[], None] | None = None,
    ):
        super().__init__(parent)
        self._data = data
        self._on_changed = on_changed
        self._series = "output"
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        self._title = QLabel()
        self._title.setStyleSheet("font-weight: 600;")
        layout.addWidget(self._title)

        self._table = QTableWidget()
        vertical_header = self._table.verticalHeader()
        horizontal_header = self._table.horizontalHeader()
        assert vertical_header is not None
        assert horizontal_header is not None
        vertical_header.setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        horizontal_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.itemChanged.connect(self._save)
        layout.addWidget(self._table)

        self.set_series("output")

    def set_series(self, series: str) -> None:
        """切换输出/防具视图并从共享数据重新加载。"""
        if series not in {"output", "armor"}:
            self.setVisible(False)
            return
        self._series = series
        self.setVisible(True)
        if series == "output":
            self._title.setText(tr("输出套装定义"))
            headers = [tr("套装名称"), tr("推荐流派"), tr("基础属性")]
        else:
            self._title.setText(tr("防具套装定义"))
            headers = [tr("套装名称"), tr("推荐流派"), tr("关联套装")]
        self._loading = True
        self._table.clear()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(headers)
        self._reload_rows()
        self._loading = False

    def _reload_rows(self) -> None:
        raw = self._data.get("equipment_sets") or {}
        left = raw.get("left") or {}
        right = raw.get("right") or {}
        entries = left if self._series == "output" else right
        self._table.setRowCount(len(entries))

        if self._series == "output":
            for row, (key, entry) in enumerate(entries.items()):
                self._set_row(row, key, (
                    str(entry.get("name") or ""),
                    str(entry.get("recommended_school") or ""),
                    str(entry.get("two_piece_affix") or ""),
                ))
            return

        left_for_right = {
            str(entry.get("recommended_right") or ""): (key, entry)
            for key, entry in left.items()
            if isinstance(entry, dict) and entry.get("recommended_right")
        }
        for row, (key, entry) in enumerate(entries.items()):
            left_key, linked = left_for_right.get(key, ("", {}))
            linked_name = str((left.get(left_key) or {}).get("name") or "")
            self._set_row(row, key, (
                str(entry.get("name") or ""),
                str(linked.get("recommended_school") or ""),
                linked_name,
            ))

    def _set_row(self, row: int, key: str, values: tuple[str, str, str]) -> None:
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, key)
            self._table.setItem(row, column, item)

    def _save(self, _item: QTableWidgetItem | None = None) -> None:
        if self._loading:
            return
        if self._series == "output":
            self._save_output()
        else:
            self._save_armor()
        if self._on_changed is not None:
            self._on_changed()

    def _save_output(self) -> None:
        raw = self._data.setdefault("equipment_sets", {})
        old_left = raw.get("left") or {}
        new_left: dict[str, dict] = {}
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 0)
            if name_item is None:
                continue
            key = str(name_item.data(Qt.ItemDataRole.UserRole) or "")
            name = name_item.text().strip()
            if not key or not name:
                continue
            school_item = self._table.item(row, 1)
            affix_item = self._table.item(row, 2)
            new_left[key] = {
                **(old_left.get(key) or {}),
                "name": name,
                "recommended_school": (
                    school_item.text().strip() if school_item else ""),
                "two_piece_affix": (
                    affix_item.text().strip() if affix_item else ""),
            }
        raw["left"] = new_left

    def _save_armor(self) -> None:
        raw = self._data.setdefault("equipment_sets", {})
        left = raw.get("left") or {}
        old_right = raw.get("right") or {}
        left_by_name = {
            str(entry.get("name") or "").strip(): key
            for key, entry in left.items()
            if isinstance(entry, dict) and str(entry.get("name") or "").strip()
        }
        new_right: dict[str, dict] = {}
        relations: list[tuple[str, str, str]] = []
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 0)
            if name_item is None:
                continue
            key = str(name_item.data(Qt.ItemDataRole.UserRole) or "")
            name = name_item.text().strip()
            if not key or not name:
                continue
            school_item = self._table.item(row, 1)
            linked_item = self._table.item(row, 2)
            linked_name = linked_item.text().strip() if linked_item else ""
            linked_key = str(left_by_name.get(linked_name) or "")
            new_right[key] = {**(old_right.get(key) or {}), "name": name}
            # 未知名称可能是正在编辑的中间态，不因此清空旧关系。
            if linked_key:
                relations.append((key, linked_key, (
                    school_item.text().strip() if school_item else "")))

        new_left = {
            key: dict(entry) for key, entry in left.items()
            if isinstance(entry, dict)
        }
        for right_key, linked_key, school in relations:
            for key, entry in new_left.items():
                if key != linked_key and entry.get("recommended_right") == right_key:
                    entry["recommended_right"] = ""
            linked = new_left.get(linked_key)
            if linked is not None:
                linked["recommended_right"] = right_key
                linked["recommended_school"] = school
        raw["left"] = new_left
        raw["right"] = new_right
