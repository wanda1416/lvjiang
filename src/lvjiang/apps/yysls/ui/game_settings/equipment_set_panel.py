"""装备配置中的套装定义表。

数据仍使用 ``equipment_sets.left/right`` 的稳定 key；界面分别称为
“攻具套装”和“防具套装”。防具页的“关联套装”
是 ``left.recommended_right`` 的反向编辑视图，不另存一份平行关系。

新增套装由界面生成稳定 key（``custom_N``），用户只填名称：名称是展示
label，key 才是配装方案、装备记录和跨语言协议引用的身份。
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from lvjiang.ui.button_styles import apply_button_style

from .....i18n import tr, tr_label

#: 界面新增套装的稳定 key 前缀，后接第一个未被占用的序号。
_NEW_SET_KEY_PREFIX = "custom_"


class EquipmentSetEditor(QFrame):
    """嵌入装备配置“攻具/防具”页的套装表。"""

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
        header = QHBoxLayout()
        self._title = QLabel()
        self._title.setStyleSheet("font-weight: 600;")
        header.addWidget(self._title)
        header.addStretch()
        self._btn_add = QPushButton(tr("添加套装"))
        self._btn_add.clicked.connect(self._on_add)
        apply_button_style(self._btn_add)
        header.addWidget(self._btn_add)
        self._btn_delete = QPushButton(tr("删除套装"))
        self._btn_delete.clicked.connect(self._on_delete)
        apply_button_style(self._btn_delete)
        header.addWidget(self._btn_delete)
        layout.addLayout(header)

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
        """切换攻具/防具视图并从共享数据重新加载。"""
        if series not in {"output", "armor"}:
            self.setVisible(False)
            return
        self._series = series
        self.setVisible(True)
        if series == "output":
            self._title.setText(tr("攻具套装定义"))
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

    def _reload(self) -> None:
        """按当前页重建行；期间阻断 itemChanged，避免刷新触发保存。"""
        self._loading = True
        try:
            self._table.setRowCount(0)
            self._reload_rows()
        finally:
            self._loading = False

    def _on_add(self) -> None:
        """新增一条套装定义：用户只填名称，稳定 key 由界面生成。"""
        if self._series not in {"output", "armor"}:
            return
        side = "left" if self._series == "output" else "right"
        side_label = tr("攻具") if side == "left" else tr("防具")
        name, ok = QInputDialog.getText(
            self, tr("添加{side}套装").format(side=side_label),
            tr_label('套装名称'))
        name = (name or "").strip()
        if not ok or not name:
            return
        raw = self._data.setdefault("equipment_sets", {})
        entries = raw.setdefault(side, {})
        if any(str(entry.get("name") or "").strip() == name
               for entry in entries.values() if isinstance(entry, dict)):
            QMessageBox.warning(
                self, tr("无法添加"),
                tr("「{name}」套装已存在。").format(name=name))
            return
        entries[self._new_key(raw)] = {"name": name}
        self._reload()
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is not None and item.text() == name:
                self._table.setCurrentCell(row, 0)
                break
        if self._on_changed is not None:
            self._on_changed()

    @staticmethod
    def _new_key(raw: dict) -> str:
        """取左右两侧都没用过的 ``custom_N`` key。

        取最大序号加一，不补中间的空号：删掉的套装 key 可能还留在任务历史、
        装备记录和备战方案里，复用编号会让旧记录指向另一套装备。
        """
        used = {
            str(key)
            for side in ("left", "right")
            for key in (raw.get(side) or {})
        }
        indexes = [
            int(key[len(_NEW_SET_KEY_PREFIX):])
            for key in used
            if key.startswith(_NEW_SET_KEY_PREFIX) and key[len(_NEW_SET_KEY_PREFIX):].isdigit()
        ]
        return f"{_NEW_SET_KEY_PREFIX}{max(indexes, default=0) + 1}"

    def _on_delete(self) -> None:
        """删除选中的套装定义，被引用时先列出引用者并二次确认。"""
        if self._series not in {"output", "armor"}:
            return
        row = self._table.currentRow()
        name_item = self._table.item(row, 0) if row >= 0 else None
        key = str((name_item.data(Qt.ItemDataRole.UserRole) if name_item else "") or "")
        if name_item is None or not key:
            QMessageBox.information(
                self, tr("无法删除"), tr("先在表格里选中要删除的套装。"))
            return
        name = name_item.text().strip() or key
        side = "left" if self._series == "output" else "right"
        raw = self._data.get("equipment_sets") or {}
        references = self._references(raw, side, key)
        detail = ""
        if references:
            detail = tr("\n\n同一份配置里有引用，删除后会显示为未知套装：\n{items}").format(
                items="\n".join(f"· {item}" for item in references))
        answer = QMessageBox.question(
            self, tr("删除套装"),
            tr("删除套装「{name}」？\n\n"
               "备战方案和装备记录里引用它的部位，删除后会按未知套装处理，"
               "不会自动清空。{detail}").format(name=name, detail=detail),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        entries = dict(raw.get(side) or {})
        entries.pop(key, None)
        raw[side] = entries
        if side == "right":
            # 关联关系存在攻具一侧，删掉防具套装时要顺手解开，避免悬空引用。
            for entry in (raw.get("left") or {}).values():
                if isinstance(entry, dict) and entry.get("recommended_right") == key:
                    entry["recommended_right"] = ""
        self._data["equipment_sets"] = raw
        self._reload()
        if self._on_changed is not None:
            self._on_changed()

    @staticmethod
    def _references(raw: dict, side: str, key: str) -> list[str]:
        """列出同一份配置里引用该套装的条目（跨文件的方案与记录在确认文案里统一提示）。"""
        if side != "right":
            return []
        return [
            tr("攻具套装「{name}」把它登记为关联防具").format(
                name=str(entry.get("name") or left_key))
            for left_key, entry in (raw.get("left") or {}).items()
            if isinstance(entry, dict) and entry.get("recommended_right") == key
        ]

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
