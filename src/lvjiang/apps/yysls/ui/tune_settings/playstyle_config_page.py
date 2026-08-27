"""流派规则页（全局 tune_config.yaml）

编辑品阶门槛（quality_thresholds）与开关注册表（switches）。
流派维度全局唯一、不随基础规则组切换。沿用「变更即校验即保存」
模式：控件变更即重建 raw dict → 校验 → 通过才写盘并 reload，
失败时状态栏红字提示。`_build()` 以管理器最新 raw 为底、只替换
本页负责的段/键。

- 品阶门槛表：固定 7 个标准部位（QUALITY_PARTS，锁死不可增删）×
  gold/purple/blue 勾选；规则级可在规则设置页按部位覆盖；
- 开关设定表：开关 key → 显示名；规则条件组 when 引用的开关
  禁止删除（保存时由管理器校验拦截）；
- 装备评级：四档判定机制的只读说明（置底宽敞展示）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.core.tuning_rules import (
    QUALITY_PARTS,
    TuneConfigManager,
)
from lvjiang.ui.button_styles import apply_button_style

from .....i18n import tr

_QUALITIES = ("gold", "purple", "blue")


class PlaystyleConfigPage(QWidget):
    """流派规则编辑页（品阶门槛 + 开关设定 + 装备评级说明）"""

    def __init__(self, manager: TuneConfigManager,
                 status_cb: Callable[[str, bool], None], parent=None):
        super().__init__(parent)
        self._manager = manager
        self._status_cb = status_cb
        self._data = manager.get_raw()
        self._loading = True
        self._init_ui()
        self._load()
        self._loading = False

    # ── UI 构建 ──

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 品阶门槛（固定 7 个标准部位，锁死不可增删）
        layout.addWidget(QLabel(
            "<b>" + tr("品阶门槛") + "</b>（" + tr("固定标准部位；勾选有调律价值的品阶；"
            "规则设置页可按部位覆盖本全局默认") + "）"))
        self._q_table = QTableWidget(0, 4)
        self._q_table.setHorizontalHeaderLabels(
            [tr("部位"), "gold", "purple", "blue"])
        for col, width in enumerate((160, 70, 70, 70)):
            self._q_table.setColumnWidth(col, width)
        layout.addWidget(self._q_table)

        # 开关设定（标题顶部留半个字高度）
        layout.addSpacing(self.fontMetrics().height() // 2)
        layout.addWidget(QLabel(
            "<b>" + tr("开关设定") + "</b>（" + tr("开关 key → 显示名；规则条件组 when 引用的"
            "开关禁止删除") + "）"))
        self._sw_table = QTableWidget(0, 2)
        self._sw_table.setHorizontalHeaderLabels([tr("开关 key"), tr("名称")])
        for col, width in enumerate((160, 200)):
            self._sw_table.setColumnWidth(col, width)
        self._sw_table.itemChanged.connect(lambda _i: self._apply())
        layout.addWidget(self._sw_table)
        layout.addLayout(self._table_buttons(
            self._sw_table, self._insert_switch_row))

        # 装备评级（四档判定机制只读说明，置底宽敞展示）
        rating_label = QLabel(
            "<b>" + tr("装备评级") + "</b>（" + tr("机制说明，不可配置") + "）<br><br>"
            "• " + tr("四档：垃圾 / 一般 / 优秀 / 顶级") + "<br><br>"
            "• " + tr("每档由若干条件组定义：组间 OR、组内 AND，"
                      "条件组可绑定开关前提 when") + "<br><br>"
            "• " + tr("判定顺序：垃圾 → 一般 → 优秀 → 顶级，命中即定档") + "<br><br>"
            "• " + tr("全部未命中时取「默认判定」：部位页可按部位设置，"
                      "未设置则跟随规则设置页"))
        rating_label.setWordWrap(True)
        rating_label.setContentsMargins(4, 16, 4, 8)
        layout.addWidget(rating_label)
        layout.addStretch()

    def _table_buttons(self, table: QTableWidget, insert) -> QHBoxLayout:
        row = QHBoxLayout()
        btn_add = QPushButton(tr("添加行"))
        btn_add.clicked.connect(lambda: (insert(table.rowCount()), self._apply()))
        btn_del = QPushButton(tr("删除选中行"))
        apply_button_style(btn_add)
        apply_button_style(btn_del, variant="danger")

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

    def _insert_quality_row(self, row: int, part: str = "",
                            qualities: set[str] | None = None):
        qualities = qualities or set()
        table = self._q_table
        table.insertRow(row)
        item = QTableWidgetItem(part)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, 0, item)
        for i, q in enumerate(_QUALITIES, start=1):
            cb = QCheckBox()
            cb.setChecked(q in qualities)
            cb.stateChanged.connect(lambda _s: self._apply())
            table.setCellWidget(row, i, cb)

    def _insert_switch_row(self, row: int, key: str = "", name: str = ""):
        table = self._sw_table
        table.blockSignals(True)
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(key))
        table.setItem(row, 1, QTableWidgetItem(name))
        table.blockSignals(False)

    # ── 回填 ──

    def _load(self):
        d = self._data
        # 品阶门槛
        q = d.get("quality_thresholds") or {}
        self._q_table.blockSignals(True)
        self._q_table.setRowCount(0)
        for part in QUALITY_PARTS:
            self._insert_quality_row(
                self._q_table.rowCount(), part, set(q.get(part) or []))
        self._q_table.blockSignals(False)
        # 行数锁死 7 行，默认高度直接容纳全部部位，不留滚动
        vh = self._q_table.verticalHeader()
        height = (self._q_table.horizontalHeader().sizeHint().height()
                  + sum(vh.sectionSize(r)
                        for r in range(self._q_table.rowCount()))
                  + 2 * self._q_table.frameWidth())
        self._q_table.setMinimumHeight(height)

        self._sw_table.setRowCount(0)
        for key, spec in (d.get("switches") or {}).items():
            name = spec.get("name") if isinstance(spec, dict) else ""
            self._insert_switch_row(
                self._sw_table.rowCount(), str(key), str(name or ""))

    # ── 收集 → 校验 → 写盘 → reload ──

    def _build(self) -> dict:
        # 品阶门槛（固定行，部位列只读）
        quality: dict[str, list[str]] = {}
        for r in range(self._q_table.rowCount()):
            item = self._q_table.item(r, 0)
            part = item.text().strip() if item else ""
            if not part:
                continue
            chosen = []
            for i, q in enumerate(_QUALITIES, start=1):
                cb = self._q_table.cellWidget(r, i)
                if cb and cb.isChecked():
                    chosen.append(q)
            quality[part] = chosen

        switches: dict[str, dict] = {}
        for r in range(self._sw_table.rowCount()):
            key_item = self._sw_table.item(r, 0)
            name_item = self._sw_table.item(r, 1)
            key = key_item.text().strip() if key_item else ""
            name = name_item.text().strip() if name_item else ""
            if not (key or name):
                continue  # 全空行忽略（新增未填）
            switches[key] = {"name": name}

        # 以最新 raw 为底只替换本页负责的段
        data = self._manager.get_raw()
        data["quality_thresholds"] = quality
        data["switches"] = switches
        return data

    def _apply(self):
        if self._loading:
            return
        data = self._build()
        err = self._manager.validate(data)
        if err:
            self._status_cb(tr("校验失败（未保存）：{err}").format(err=err), True)
            return
        try:
            self._manager.save(data)
        except Exception as e:  # noqa: BLE001
            logger.exception("流派规则保存失败")
            self._status_cb(tr("保存失败：{e}").format(e=e), True)
            return
        self._data = data
        now = datetime.now().strftime("%H:%M:%S")
        self._status_cb(tr("已保存并生效（{now}）").format(now=now), False)
