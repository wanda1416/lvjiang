"""基础配置页（全局 tuning_base.yaml）

编辑品阶门槛（quality_thresholds）与 PVP 词条等价（pvp.names /
pvp.substitutions）。沿用「变更即校验即保存」模式：控件变更即重建
raw dict → 校验 → 通过才写盘并 reload，失败时状态栏红字提示。

- 品阶门槛表：类别（weapon/jewelry/armor/default 等）× gold/purple/blue
  勾选，须含 default；
- PVP 词条集合：命中即标记保留的词条（候选来自标准词条全集）；
- PVP 部位替换：<部位> 的 源词条 → 目标词条（仅当源词条不在规则词条库
  时生效）；
- PVP 部位并库：<部位> 临时并入词条库的词条。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from loguru import logger
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from src.apps.yysls.game_config import get_game_config
from src.apps.yysls.evaluator.tuning_rules import PART_KEYS, TuningBaseManager
from src.ui.widgets import NoWheelComboBox

_QUALITIES = ("gold", "purple", "blue")


class BaseConfigPage(QWidget):
    """全局基础配置编辑页（持有 raw dict 深拷贝为工作副本）"""

    def __init__(self, manager: TuningBaseManager,
                 status_cb: Callable[[str, bool], None], parent=None):
        super().__init__(parent)
        self._manager = manager
        self._status_cb = status_cb
        self._data = manager.get_raw()
        self._affixes = get_game_config().get_normal_affix_names()
        self._loading = True
        self._init_ui()
        self._load()
        self._loading = False

    # ── UI 构建 ──

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 品阶门槛
        layout.addWidget(QLabel(
            "<b>品阶门槛</b>（类别 = weapon/jewelry/armor/default；"
            "勾选有调律价值的品阶；未列类别回退 default，须保留 default 行）"))
        self._q_table = QTableWidget(0, 4)
        self._q_table.setHorizontalHeaderLabels(
            ["类别", "gold", "purple", "blue"])
        for col, width in enumerate((160, 70, 70, 70)):
            self._q_table.setColumnWidth(col, width)
        self._q_table.cellChanged.connect(self._apply)
        layout.addWidget(self._q_table)
        layout.addLayout(self._table_buttons(
            self._q_table, self._insert_quality_row))

        # PVP 词条集合
        layout.addWidget(QLabel(
            "<b>PVP 词条集合</b>（keep_pvp 开启时命中即标记保留）"))
        self._names_table = QTableWidget(0, 1)
        self._names_table.setHorizontalHeaderLabels(["词条"])
        self._names_table.setColumnWidth(0, 200)
        layout.addWidget(self._names_table)
        layout.addLayout(self._table_buttons(
            self._names_table, self._insert_name_row))

        # PVP 部位替换
        layout.addWidget(QLabel(
            "<b>PVP 部位替换</b>（源词条不在规则词条库时视作目标词条）"))
        self._subs_table = QTableWidget(0, 3)
        self._subs_table.setHorizontalHeaderLabels(["部位", "源词条", "目标词条"])
        for col, width in enumerate((90, 200, 200)):
            self._subs_table.setColumnWidth(col, width)
        layout.addWidget(self._subs_table)
        layout.addLayout(self._table_buttons(
            self._subs_table, self._insert_sub_row))

        # PVP 部位并库
        layout.addWidget(QLabel(
            "<b>PVP 部位并库</b>（keep_pvp 开启时临时并入该部位词条库）"))
        self._pool_table = QTableWidget(0, 2)
        self._pool_table.setHorizontalHeaderLabels(["部位", "词条"])
        for col, width in enumerate((90, 200)):
            self._pool_table.setColumnWidth(col, width)
        layout.addWidget(self._pool_table)
        layout.addLayout(self._table_buttons(
            self._pool_table, self._insert_pool_row))
        layout.addStretch()

    def _table_buttons(self, table: QTableWidget, insert) -> QHBoxLayout:
        row = QHBoxLayout()
        btn_add = QPushButton("添加行")
        btn_add.clicked.connect(lambda: (insert(table.rowCount()), self._apply()))
        btn_del = QPushButton("删除选中行")

        def _delete():
            r = table.currentRow()
            if r >= 0:
                table.removeRow(r)
                self._apply()

        btn_del.clicked.connect(_delete)
        row.addWidget(btn_add)
        row.addWidget(btn_del)
        row.addStretch()
        return row

    # ── 行构建 ──

    def _affix_combo(self, value: str = "") -> NoWheelComboBox:
        combo = NoWheelComboBox()
        combo.addItem("")
        combo.addItems(self._affixes)
        if value and value not in self._affixes:
            combo.addItem(value)
        combo.setCurrentText(value)
        combo.currentTextChanged.connect(lambda _t: self._apply())
        return combo

    def _part_combo(self, value: str = "") -> NoWheelComboBox:
        combo = NoWheelComboBox()
        combo.addItems(list(PART_KEYS))
        combo.setCurrentText(value or PART_KEYS[0])
        combo.currentTextChanged.connect(lambda _t: self._apply())
        return combo

    def _insert_quality_row(self, row: int, cat: str = "",
                            qualities: set[str] | None = None):
        qualities = qualities or set()
        table = self._q_table
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(cat))
        for i, q in enumerate(_QUALITIES, start=1):
            cb = QCheckBox()
            cb.setChecked(q in qualities)
            cb.stateChanged.connect(lambda _s: self._apply())
            table.setCellWidget(row, i, cb)

    def _insert_name_row(self, row: int, value: str = ""):
        self._names_table.insertRow(row)
        self._names_table.setCellWidget(row, 0, self._affix_combo(value))

    def _insert_sub_row(self, row: int, part: str = "",
                        src: str = "", dst: str = ""):
        table = self._subs_table
        table.insertRow(row)
        table.setCellWidget(row, 0, self._part_combo(part))
        table.setCellWidget(row, 1, self._affix_combo(src))
        table.setCellWidget(row, 2, self._affix_combo(dst))

    def _insert_pool_row(self, row: int, part: str = "", affix: str = ""):
        table = self._pool_table
        table.insertRow(row)
        table.setCellWidget(row, 0, self._part_combo(part))
        table.setCellWidget(row, 1, self._affix_combo(affix))

    # ── 回填 ──

    def _load(self):
        d = self._data
        q = d.get("quality_thresholds") or {}
        self._q_table.blockSignals(True)
        self._q_table.setRowCount(0)
        for cat, qs in q.items():
            self._insert_quality_row(
                self._q_table.rowCount(), str(cat), set(qs or []))
        self._q_table.blockSignals(False)

        pvp = d.get("pvp") or {}
        self._names_table.setRowCount(0)
        for name in (pvp.get("names") or []):
            self._insert_name_row(self._names_table.rowCount(), str(name))

        subs = pvp.get("substitutions") or {}
        self._subs_table.setRowCount(0)
        self._pool_table.setRowCount(0)
        for part, spec in subs.items():
            spec = spec or {}
            for src, dst in spec.items():
                if src == "add_to_pool":
                    continue
                self._insert_sub_row(
                    self._subs_table.rowCount(), str(part),
                    str(src), str(dst))
            for affix in (spec.get("add_to_pool") or []):
                self._insert_pool_row(
                    self._pool_table.rowCount(), str(part), str(affix))

    # ── 收集 → 校验 → 写盘 → reload ──

    def _combo_text(self, table: QTableWidget, row: int, col: int) -> str:
        widget = table.cellWidget(row, col)
        return widget.currentText().strip() if widget else ""

    def _build(self) -> dict:
        # 品阶门槛
        quality: dict[str, list[str]] = {}
        for r in range(self._q_table.rowCount()):
            item = self._q_table.item(r, 0)
            cat = item.text().strip() if item else ""
            if not cat:
                continue
            chosen = []
            for i, q in enumerate(_QUALITIES, start=1):
                cb = self._q_table.cellWidget(r, i)
                if cb and cb.isChecked():
                    chosen.append(q)
            quality[cat] = chosen

        names = []
        for r in range(self._names_table.rowCount()):
            name = self._combo_text(self._names_table, r, 0)
            if name and name not in names:
                names.append(name)

        substitutions: dict[str, dict] = {}
        for r in range(self._subs_table.rowCount()):
            part = self._combo_text(self._subs_table, r, 0)
            src = self._combo_text(self._subs_table, r, 1)
            dst = self._combo_text(self._subs_table, r, 2)
            if not (part and src and dst):
                continue
            substitutions.setdefault(part, {})[src] = dst
        for r in range(self._pool_table.rowCount()):
            part = self._combo_text(self._pool_table, r, 0)
            affix = self._combo_text(self._pool_table, r, 1)
            if not (part and affix):
                continue
            spec = substitutions.setdefault(part, {})
            spec.setdefault("add_to_pool", [])
            if affix not in spec["add_to_pool"]:
                spec["add_to_pool"].append(affix)

        return {
            "quality_thresholds": quality,
            "pvp": {"names": names, "substitutions": substitutions},
        }

    def _apply(self):
        if self._loading:
            return
        data = self._build()
        err = self._manager.validate(data)
        if err:
            self._status_cb(f"校验失败（未保存）：{err}", True)
            return
        try:
            self._manager.save(data)
        except Exception as e:  # noqa: BLE001
            logger.exception("基础配置保存失败")
            self._status_cb(f"保存失败：{e}", True)
            return
        self._data = data
        now = datetime.now().strftime("%H:%M:%S")
        self._status_cb(f"已保存并生效（{now}）", False)
