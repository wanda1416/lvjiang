"""技能轴查看器 —— 导入毕业率计算器 Excel，看轴和伤害来源

方案 JSON 在编译时丢掉了技能名（整个节点程序里只剩一个字符串常量），
所以「这套配装的伤害来自哪个技能」只能回到原始 Excel 看。本对话框只读：
伤害数值直接取 Excel 已算好的 `期望` 列，不重算、不改表。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr
from ...core.graduation.rotation import (
    Rotation,
    RotationParseError,
    parse_rotation,
)

_ZERO_HINT = tr("该行不产出伤害，通常是心法切换或起手动作")


def _num(value: float, digits: int = 0) -> QTableWidgetItem:
    """右对齐的数值单元格"""
    item = QTableWidgetItem(f"{value:,.{digits}f}")
    item.setTextAlignment(
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item


def _fit(table: QTableWidget, stretch_col: int) -> None:
    header = table.horizontalHeader()
    if header is None:
        return
    for col in range(table.columnCount()):
        header.setSectionResizeMode(
            col,
            QHeaderView.ResizeMode.Stretch if col == stretch_col
            else QHeaderView.ResizeMode.ResizeToContents,
        )


class RotationDialog(QDialog):
    """技能轴与伤害来源"""

    def __init__(self, parent=None, initial: Path | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("技能轴"))
        self.resize(920, 640)
        self._rotation: Rotation | None = None

        layout = QVBoxLayout(self)

        bar = QHBoxLayout()
        self._lbl_source = QLabel(tr("尚未导入。选择毕业率计算器 Excel 查看其竞速轴。"))
        self._lbl_source.setWordWrap(True)
        bar.addWidget(self._lbl_source, stretch=1)
        btn_open = QPushButton(tr("导入 Excel"))
        btn_open.clicked.connect(self._on_open)
        bar.addWidget(btn_open)
        layout.addLayout(bar)

        self._lbl_summary = QLabel("")
        self._lbl_summary.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._lbl_summary)

        self._tabs = QTabWidget()
        self._table_source = self._make_source_table()
        self._table_axis = self._make_axis_table()
        self._tabs.addTab(self._table_source, tr("伤害来源"))
        self._tabs.addTab(self._table_axis, tr("轴序"))
        layout.addWidget(self._tabs, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        if initial is not None:
            self._load(initial)

    # ─── 表格构建 ─────────────────────────────────────────

    def _make_source_table(self) -> QTableWidget:
        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels([
            tr("技能"), tr("类型"), tr("轴行"), tr("命中"),
            tr("总伤害"), tr("占比"), tr("单次均值"),
        ])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        vheader = table.verticalHeader()
        if vheader is not None:
            vheader.setVisible(False)
        _fit(table, 0)
        return table

    def _make_axis_table(self) -> QTableWidget:
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels([
            tr("#"), tr("技能"), tr("次数"), tr("类型"), tr("伤害"),
        ])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        vheader = table.verticalHeader()
        if vheader is not None:
            vheader.setVisible(False)
        _fit(table, 1)
        return table

    # ─── 导入 ─────────────────────────────────────────────

    def _on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择毕业率计算器 Excel"), "", tr("Excel 文件 (*.xlsx)"))
        if path:
            self._load(Path(path))

    def _load(self, path: Path):
        try:
            rotation = parse_rotation(path)
        except RotationParseError as exc:
            QMessageBox.warning(self, tr("无法解析"), str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - 解析异常不应让对话框崩掉
            logger.exception(f"解析技能轴失败: {path}")
            QMessageBox.warning(
                self, tr("无法解析"),
                tr("解析 {name} 时出错：{err}").format(name=path.name, err=exc))
            return
        self._rotation = rotation
        self._render(rotation)

    # ─── 渲染 ─────────────────────────────────────────────

    def _render(self, rotation: Rotation):
        self._lbl_source.setText(rotation.source)
        self._lbl_summary.setText(
            tr("战斗 {time:.1f} 秒 · {rows} 行 / {hits} 次命中 · "
               "{skills} 个技能 · 总伤 {damage:,.0f} · DPS {dps:,.0f}").format(
                time=rotation.combat_time, rows=len(rotation.hits),
                hits=rotation.total_hits, skills=len(rotation.by_skill()),
                damage=rotation.total_damage, dps=rotation.dps))

        sources = rotation.by_skill()
        self._table_source.setRowCount(len(sources))
        for row, item in enumerate(sources):
            self._table_source.setItem(row, 0, QTableWidgetItem(item.skill))
            self._table_source.setItem(row, 1, QTableWidgetItem(item.kind))
            self._table_source.setItem(row, 2, _num(item.rows))
            self._table_source.setItem(row, 3, _num(item.hits))
            self._table_source.setItem(row, 4, _num(item.damage))
            # 占比用进度条：一眼看出主力技能，比读百分比数字快
            bar = QProgressBar()
            bar.setRange(0, 10000)
            bar.setValue(int(item.share * 100))
            bar.setFormat(f"{item.share:.2f}%")
            bar.setTextVisible(True)
            self._table_source.setCellWidget(row, 5, bar)
            self._table_source.setItem(row, 6, _num(item.average))
            if item.damage <= 0:
                for col in (0, 1):
                    cell = self._table_source.item(row, col)
                    if cell is not None:
                        cell.setToolTip(_ZERO_HINT)

        self._table_axis.setRowCount(len(rotation.hits))
        for row, hit in enumerate(rotation.hits):
            self._table_axis.setItem(row, 0, _num(hit.index))
            self._table_axis.setItem(row, 1, QTableWidgetItem(hit.skill))
            self._table_axis.setItem(row, 2, _num(hit.count))
            self._table_axis.setItem(row, 3, QTableWidgetItem(hit.kind))
            self._table_axis.setItem(row, 4, _num(hit.damage))

    # ─── 供测试与外部读取 ─────────────────────────────────

    @property
    def rotation(self) -> Rotation | None:
        return self._rotation


def open_rotation_dialog(parent: QWidget | None = None,
                         initial: Path | None = None) -> RotationDialog:
    dialog = RotationDialog(parent, initial)
    dialog.exec()
    return dialog
