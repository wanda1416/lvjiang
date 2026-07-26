"""流派设置页（流派级字段，不随变体切换）

key（只读）、名称、keep_pvp、own_attr（大本属来源）、
子流派/玩法表、weapons 武器角色映射表。表格采用文本格式单元格：
玩法 "key:名,key:名"、主武器 "武器:词条;武器:词条"、副武器逗号分隔。
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

# own_attr 候选：属名 / 跟随子流派勾选 / 无
_OWN_ATTR_OPTIONS = [
    ("鸣金", "鸣金"), ("裂石", "裂石"), ("破竹", "破竹"), ("牵丝", "牵丝"),
    ("跟随子流派勾选", "from_sub_schools"), ("（无）", ""),
]


def _parse_playstyles(text: str) -> dict[str, str]:
    """"key:名,key:名" → dict（空段忽略）"""
    result: dict[str, str] = {}
    for seg in text.split(","):
        seg = seg.strip()
        if not seg:
            continue
        key, _, name = seg.partition(":")
        if key.strip():
            result[key.strip()] = name.strip() or key.strip()
    return result


def _parse_main(text: str) -> dict[str, str]:
    """"武器:词条;武器:词条" → dict"""
    result: dict[str, str] = {}
    for seg in text.split(";"):
        seg = seg.strip()
        if not seg:
            continue
        weapon, _, affix = seg.partition(":")
        if weapon.strip():
            result[weapon.strip()] = affix.strip()
    return result


class SchoolSettingsPage(QWidget):
    """流派级设置页（编辑共享 raw dict，变更即回调保存）"""

    def __init__(self, data: dict, on_changed: Callable[[], None],
                 parent=None):
        super().__init__(parent)
        self._data = data
        self._on_changed = on_changed
        self._loading = True
        self._init_ui()
        self._load()
        self._loading = False

    def _init_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self._key_label = QLabel()
        form.addRow("标识 key：", self._key_label)

        self._name_edit = QLineEdit()
        self._name_edit.editingFinished.connect(self._apply_basic)
        form.addRow("流派名称：", self._name_edit)

        self._keep_pvp_check = QCheckBox("支持「保留 PVP 词条」配置")
        self._keep_pvp_check.stateChanged.connect(self._apply_basic)
        form.addRow("keep_pvp：", self._keep_pvp_check)

        self._own_attr_combo = QComboBox()
        for label, value in _OWN_ATTR_OPTIONS:
            self._own_attr_combo.addItem(label, value)
        self._own_attr_combo.currentIndexChanged.connect(self._apply_basic)
        form.addRow("大本属来源：", self._own_attr_combo)
        layout.addLayout(form)

        # ── 子流派/玩法表 ──
        layout.addWidget(QLabel("<b>子流派与玩法</b>（玩法格式 key:名,key:名）"))
        self._sub_table = QTableWidget(0, 3)
        self._sub_table.setHorizontalHeaderLabels(["子流派 key", "显示名", "玩法"])
        self._sub_table.horizontalHeader().setStretchLastSection(True)
        self._sub_table.cellChanged.connect(self._apply_subs)
        layout.addWidget(self._sub_table)
        layout.addLayout(self._table_buttons(self._sub_table, self._apply_subs))

        # ── weapons 映射表 ──
        layout.addWidget(QLabel(
            "<b>武器角色表</b>（角色 = default 或 子流派[.玩法]；"
            "主武器格式 武器:增伤词条;…，副武器逗号分隔）"))
        self._weapon_table = QTableWidget(0, 3)
        self._weapon_table.setHorizontalHeaderLabels(["角色", "主武器", "副武器"])
        self._weapon_table.horizontalHeader().setStretchLastSection(True)
        self._weapon_table.cellChanged.connect(self._apply_weapons)
        layout.addWidget(self._weapon_table)
        layout.addLayout(
            self._table_buttons(self._weapon_table, self._apply_weapons))
        layout.addStretch()

    def _table_buttons(self, table: QTableWidget, apply) -> QHBoxLayout:
        row = QHBoxLayout()
        btn_add = QPushButton("添加行")
        btn_add.clicked.connect(
            lambda: table.insertRow(table.rowCount()))
        btn_del = QPushButton("删除选中行")

        def _delete():
            r = table.currentRow()
            if r >= 0:
                table.removeRow(r)
                apply()

        btn_del.clicked.connect(_delete)
        row.addWidget(btn_add)
        row.addWidget(btn_del)
        row.addStretch()
        return row

    # ── 回填 ──

    def _load(self):
        d = self._data
        self._key_label.setText(str(d.get("key", "")))
        self._name_edit.setText(str(d.get("name", "")))
        self._keep_pvp_check.setChecked(bool(d.get("has_keep_pvp")))
        idx = self._own_attr_combo.findData(str(d.get("own_attr") or ""))
        self._own_attr_combo.setCurrentIndex(max(idx, 0))

        subs = d.get("sub_schools") or {}
        self._sub_table.blockSignals(True)
        self._sub_table.setRowCount(len(subs))
        for i, (sk, s_raw) in enumerate(subs.items()):
            s_raw = s_raw or {}
            playstyles = s_raw.get("playstyles") or {}
            ps_text = ",".join(f"{k}:{v}" for k, v in playstyles.items())
            for col, text in enumerate(
                    (sk, str(s_raw.get("name", "")), ps_text)):
                self._sub_table.setItem(i, col, QTableWidgetItem(text))
        self._sub_table.blockSignals(False)

        weapons = d.get("weapons") or {}
        self._weapon_table.blockSignals(True)
        self._weapon_table.setRowCount(len(weapons))
        for i, (wk, w_raw) in enumerate(weapons.items()):
            w_raw = w_raw or {}
            main = w_raw.get("main") or {}
            main_text = ";".join(f"{w}:{a}" for w, a in main.items())
            sub_text = ",".join(w_raw.get("sub") or [])
            for col, text in enumerate((wk, main_text, sub_text)):
                self._weapon_table.setItem(i, col, QTableWidgetItem(text))
        self._weapon_table.blockSignals(False)

    # ── 收集（写回共享 dict） ──

    def _apply_basic(self):
        if self._loading:
            return
        self._data["name"] = self._name_edit.text().strip()
        self._data["has_keep_pvp"] = self._keep_pvp_check.isChecked()
        own_attr = self._own_attr_combo.currentData()
        if own_attr:
            self._data["own_attr"] = own_attr
        else:
            self._data.pop("own_attr", None)
        self._on_changed()

    def _apply_subs(self):
        if self._loading:
            return
        subs: dict = {}
        for i in range(self._sub_table.rowCount()):
            key = self._cell(self._sub_table, i, 0)
            if not key:
                continue
            entry: dict = {"name": self._cell(self._sub_table, i, 1) or key}
            playstyles = _parse_playstyles(self._cell(self._sub_table, i, 2))
            if playstyles:
                entry["playstyles"] = playstyles
            subs[key] = entry
        if subs:
            self._data["sub_schools"] = subs
            self._data["needs_sub_school"] = self._data.get(
                "needs_sub_school", True)
        else:
            self._data.pop("sub_schools", None)
            self._data["needs_sub_school"] = False
        self._on_changed()

    def _apply_weapons(self):
        if self._loading:
            return
        weapons: dict = {}
        for i in range(self._weapon_table.rowCount()):
            key = self._cell(self._weapon_table, i, 0)
            if not key:
                continue
            entry: dict = {}
            main = _parse_main(self._cell(self._weapon_table, i, 1))
            if main:
                entry["main"] = main
            sub = [s.strip()
                   for s in self._cell(self._weapon_table, i, 2).split(",")
                   if s.strip()]
            if sub:
                entry["sub"] = sub
            weapons[key] = entry
        if weapons:
            self._data["weapons"] = weapons
        else:
            self._data.pop("weapons", None)
        self._on_changed()

    @staticmethod
    def _cell(table: QTableWidget, row: int, col: int) -> str:
        item = table.item(row, col)
        return item.text().strip() if item else ""
