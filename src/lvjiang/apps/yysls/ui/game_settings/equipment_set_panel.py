"""装备套装配置：左右套装独立登记，左套装定义两件套收益。"""

from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr


class EquipmentSetPanel(QWidget):
    """展示并编辑套装名称、推荐关系和左四两件套属性。"""

    def __init__(self, parent=None, *, data: dict | None = None, on_changed=None):
        super().__init__(parent)
        self._data = data if data is not None else {}
        self._on_changed = on_changed
        self._loading = False
        layout = QVBoxLayout(self)
        hint = QLabel(tr(
            "左四与右四是独立套装；推荐关系只用于说明搭配，不会把两者合并。"
            "左四两件套参与毕业率，右四当前仅记录和展示。"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(hint)

        layout.addWidget(QLabel(tr("左四套装（武器、环、佩）")))
        self._left = QTableWidget(0, 4)
        self._left.setHorizontalHeaderLabels([
            tr("套装名称"), tr("推荐右四"), tr("推荐流派"), tr("两件套属性"),
        ])
        self._configure(self._left)
        layout.addWidget(self._left, 2)

        layout.addWidget(QLabel(tr("右四套装（防具）")))
        self._right = QTableWidget(0, 1)
        self._right.setHorizontalHeaderLabels([tr("套装名称")])
        self._configure(self._right)
        layout.addWidget(self._right, 1)
        self._reload()
        self._left.itemChanged.connect(self._save)
        self._right.itemChanged.connect(self._save)

    @staticmethod
    def _configure(table: QTableWidget) -> None:
        vertical_header = table.verticalHeader()
        horizontal_header = table.horizontalHeader()
        assert vertical_header is not None
        assert horizontal_header is not None
        vertical_header.setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        horizontal_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

    def _reload(self) -> None:
        raw = self._data.get("equipment_sets") or {}
        left = raw.get("left") or {}
        right = raw.get("right") or {}
        right_names = {
            key: str(entry.get("name") or key)
            for key, entry in right.items() if isinstance(entry, dict)
        }
        self._loading = True
        self._left.setRowCount(len(left))
        for row, (key, entry) in enumerate(left.items()):
            values = (
                str(entry.get("name") or ""),
                right_names.get(str(entry.get("recommended_right") or ""), ""),
                str(entry.get("recommended_school") or ""),
                str(entry.get("two_piece_affix") or ""),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, key)
                self._left.setItem(row, column, item)
        self._right.setRowCount(len(right))
        for row, (key, entry) in enumerate(right.items()):
            item = QTableWidgetItem(str(entry.get("name") or ""))
            item.setData(Qt.ItemDataRole.UserRole, key)
            self._right.setItem(row, 0, item)
        self._loading = False

    def _save(self, _item: QTableWidgetItem | None = None) -> None:
        if self._loading:
            return
        raw = self._data.setdefault("equipment_sets", {})
        old_left = raw.get("left") or {}
        old_right = raw.get("right") or {}
        right_names: dict[str, str] = {}
        new_right: dict[str, dict] = {}
        for row in range(self._right.rowCount()):
            item = self._right.item(row, 0)
            if item is None:
                continue
            key = str(item.data(Qt.ItemDataRole.UserRole) or "")
            name = item.text().strip()
            if key and name:
                new_right[key] = {"name": name}
                right_names[name] = key
        new_left: dict[str, dict] = {}
        for row in range(self._left.rowCount()):
            name_item = self._left.item(row, 0)
            if name_item is None:
                continue
            key = str(name_item.data(Qt.ItemDataRole.UserRole) or "")
            name = name_item.text().strip()
            if not key or not name:
                continue
            old = old_left.get(key) or {}
            right_item = self._left.item(row, 1)
            school_item = self._left.item(row, 2)
            affix_item = self._left.item(row, 3)
            right_name = right_item.text().strip() if right_item else ""
            new_left[key] = {
                "name": name,
                "recommended_right": right_names.get(
                    right_name, str(old.get("recommended_right") or "")),
                "recommended_school": (
                    school_item.text().strip() if school_item else ""),
                "two_piece_affix": (
                    affix_item.text().strip() if affix_item else ""),
            }
        raw["left"] = new_left
        raw["right"] = new_right or old_right
        if self._on_changed is not None:
            self._on_changed()
            return
        try:
            from ...config import get_game_config
            from ...config.game_config_files import save_game_config
            save_game_config(self._data)
            get_game_config().reload()
        except Exception as exc:  # noqa: BLE001 - 配置 UI 必须显示为日志而非崩溃
            logger.error(f"保存套装配置失败: {exc}")
