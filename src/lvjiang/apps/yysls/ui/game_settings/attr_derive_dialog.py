"""基础属性推导对话框：由属性来源算出装备之外的战斗属性。

和「创建基础属性」互补——那边是抄面板再反推，这边是正向推导，两者
应当得到同一个结果。所以对话框的重点不是算出一个数，而是**逐来源的
明细与差异**：面板对不上时，能直接看出是哪一个来源贡献错了，而不是
只知道总数不对。

推导结果通过「存为基础属性」写进现有的基础属性存储，毕业率链路照旧
读它，不需要为此改动任何既有计算。
"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.button_styles import apply_button_style

from .....i18n import tr
from ...config import get_game_config, get_play_styles, save_play_style
from ...core.attr_model import (
    SOURCE_KIND_LABELS,
    AttrModelError,
    diff_against_panel,
    get_attr_model_manager,
)
from ...core.combat.combat_attrs import COMBAT_ATTR_FIELDS, CombatAttributes
from .level_combo import LevelCombo

#: 差异大于该值才算对不上。面板只显示到小数点后一位。
_DIFF_EPSILON = 0.05


class AttrDeriveDialog(QDialog):
    """选来源 → 推导 → 与实测面板比对 → 存为基础属性。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("基础属性推导"))
        self.resize(940, 620)
        self._build_ui()
        self._refresh_sources()
        self._recompute()

    # ── 构建 ──

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel(tr("流派")))
        self._combo_school = QComboBox()
        self._combo_school.addItems(list(get_game_config().get_schools()))
        self._combo_school.currentIndexChanged.connect(self._on_school_changed)
        top.addWidget(self._combo_school)

        top.addWidget(QLabel(tr("等级")))
        self._combo_level = LevelCombo()
        self._combo_level.currentIndexChanged.connect(self._recompute)
        top.addWidget(self._combo_level)

        top.addWidget(QLabel(tr("对照基础属性")))
        self._combo_reference = QComboBox()
        self._combo_reference.currentIndexChanged.connect(self._recompute)
        top.addWidget(self._combo_reference)
        top.addStretch()
        layout.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel(tr("参与推导的来源")))
        self._list_sources = QListWidget()
        self._list_sources.itemChanged.connect(lambda _item: self._recompute())
        left_layout.addWidget(self._list_sources)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet("color: palette(mid); font-size: 11px;")
        right_layout.addWidget(self._summary)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            [tr("属性"), tr("推导值"), tr("对照值"), tr("按来源拆分")])
        header = self._table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        rows_header = self._table.verticalHeader()
        if rows_header is not None:
            rows_header.setVisible(False)
        right_layout.addWidget(self._table)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 660])
        layout.addWidget(splitter)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self._btn_save = QPushButton(tr("存为基础属性"))
        self._btn_save.clicked.connect(self._on_save)
        apply_button_style(self._btn_save)
        buttons.addWidget(self._btn_save)
        close = QPushButton(tr("关闭"))
        apply_button_style(close, variant="neutral")
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    # ── 数据 ──

    def _manager(self):
        return get_attr_model_manager()

    def _school(self) -> str:
        return self._combo_school.currentText()

    def _school_attr(self) -> str:
        return get_game_config().get_school_attr(self._school()) or "通用"

    def _refresh_sources(self) -> None:
        """列出已填数值的条目。未填的贡献 0，列出来只会让人以为漏勾了。"""
        self._list_sources.blockSignals(True)
        self._list_sources.clear()
        for effect in self._manager().effects():
            if not effect.modeled:
                continue
            item = QListWidgetItem(
                f"[{tr(SOURCE_KIND_LABELS[effect.kind])}] {effect.label}")
            item.setData(Qt.ItemDataRole.UserRole, effect.source_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self._list_sources.addItem(item)
        self._list_sources.blockSignals(False)
        self._refresh_reference()

    def _refresh_reference(self) -> None:
        self._combo_reference.blockSignals(True)
        self._combo_reference.clear()
        self._combo_reference.addItem(tr("（不对照）"), "")
        for name in get_play_styles(self._school()):
            self._combo_reference.addItem(name, name)
        self._combo_reference.blockSignals(False)

    def _selected_ids(self) -> tuple[str, ...]:
        chosen: list[str] = []
        for index in range(self._list_sources.count()):
            item = self._list_sources.item(index)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                chosen.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return tuple(chosen)

    def _reference_attrs(self) -> CombatAttributes | None:
        name = self._combo_reference.currentData()
        if not name:
            return None
        stored = get_play_styles(self._school()).get(name) or {}
        known = set(CombatAttributes.__dataclass_fields__)
        reference = CombatAttributes()
        for key, value in stored.items():
            if str(key) in known and isinstance(value, (int, float)):
                setattr(reference, str(key), float(value))
        # extra_attrs 是嵌套的一层；漏读它，指定武学增效这类动态属性
        # 就永远显示成「模型有、对照没有」。
        nested = stored.get("extra_attrs")
        if isinstance(nested, dict):
            reference.extra_attrs = {
                str(k): float(v) for k, v in nested.items()
                if isinstance(v, (int, float))
            }
        return reference

    def _on_school_changed(self) -> None:
        self._refresh_reference()
        self._recompute()

    # ── 推导 ──

    def _recompute(self) -> None:
        level = self._combo_level.get_level()
        if level is None:
            return
        try:
            result = self._manager().resolve(
                level=level,
                school_attr=self._school_attr(),
                selected=self._selected_ids(),
            )
        except AttrModelError as exc:
            self._summary.setText(tr("推导失败：{msg}").format(msg=str(exc)))
            self._table.setRowCount(0)
            return

        reference = self._reference_attrs()
        differences = (
            diff_against_panel(result, reference) if reference is not None else {}
        )
        self._fill_table(result, reference, differences)

        done, total = self._manager().progress()
        parts = [tr("已确认来源 {done}/{total}").format(done=done, total=total)]
        if reference is None:
            parts.append(tr("未选对照，只显示推导值"))
        elif differences:
            parts.append(tr("与对照有 {n} 项不一致，看「按来源拆分」定位")
                         .format(n=len(differences)))
        else:
            parts.append(tr("与对照完全一致"))
        combat_only = len(result.combat.modifiers) - len(result.panel.modifiers)
        if combat_only > 0:
            parts.append(tr("另有 {n} 项仅战斗内生效，不进本表").format(n=combat_only))
        self._summary.setText("　".join(parts))

    def _rows(self, result, reference) -> list[tuple[str, str, bool]]:
        """(字段名, 显示名, 是否 extra)：模型或对照任一有值就列出来"""
        rows: list[tuple[str, str, bool]] = []
        for name, display, _unit, _ in COMBAT_ATTR_FIELDS:
            if getattr(result.panel_attrs, name, 0.0) or (
                    reference is not None and getattr(reference, name, 0.0)):
                rows.append((name, display, False))
        extra_names = set(result.panel_attrs.extra_attrs)
        if reference is not None:
            extra_names |= set(reference.extra_attrs)
        for name in sorted(extra_names):
            rows.append((name, name, True))
        return rows

    def _fill_table(self, result, reference, differences: dict) -> None:
        # 一律走 result.panel：显示的是面板值，拆分就必须是面板明细。
        # 混用战斗明细的话，两栏加不到一起（吃食只在战斗侧有贡献）。
        panel = result.panel
        rows = self._rows(result, reference)
        self._table.setRowCount(len(rows))
        for row, (name, display, is_extra) in enumerate(rows):
            derived = (
                panel.attrs.extra_attrs.get(name, 0.0) if is_extra
                else getattr(panel.attrs, name, 0.0)
            )
            self._table.setItem(row, 0, QTableWidgetItem(display))
            self._table.setItem(row, 1, QTableWidgetItem(f"{derived:.4g}"))

            if reference is None:
                self._table.setItem(row, 2, QTableWidgetItem("-"))
            else:
                actual = (
                    reference.extra_attrs.get(name, 0.0) if is_extra
                    else getattr(reference, name, 0.0)
                )
                cell = QTableWidgetItem(f"{actual:.4g}")
                if abs(actual - derived) > _DIFF_EPSILON:
                    cell.setForeground(Qt.GlobalColor.red)
                self._table.setItem(row, 2, cell)

            breakdown = panel.contribution_by_kind(name)
            text = "　".join(
                f"{tr(SOURCE_KIND_LABELS.get(kind, kind))} {value:+.4g}"
                for kind, value in breakdown.items() if value
            )
            self._table.setItem(row, 3, QTableWidgetItem(text))

    # ── 保存 ──

    def _on_save(self) -> None:
        level = self._combo_level.get_level()
        if level is None:
            return
        try:
            result = self._manager().resolve(
                level=level,
                school_attr=self._school_attr(),
                selected=self._selected_ids(),
            )
        except AttrModelError as exc:
            QMessageBox.warning(self, tr("推导失败"), str(exc))
            return

        name, ok = QInputDialog.getText(
            self, tr("存为基础属性"), tr("名称："),
            text=tr("{school} 推导").format(school=self._school()))
        if not ok or not name.strip():
            return
        # 存的是战斗属性全集：吃食一类只在战斗内生效的加成也要计入，
        # 毕业率算的是战斗内表现，而不是角色面板。
        attrs = result.combat_attrs.to_dict()
        try:
            save_play_style(self._school(), name.strip(), attrs)
        except Exception as exc:
            logger.error(f"保存基础属性失败: {exc}")
            QMessageBox.warning(self, tr("保存失败"), str(exc))
            return
        self._refresh_reference()
        QMessageBox.information(
            self, tr("已保存"),
            tr("已存为基础属性「{name}」，毕业率可直接选用").format(
                name=name.strip()),
        )
