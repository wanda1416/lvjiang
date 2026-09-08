"""角色属性表：来源、成长状态、静态推导和未求值的条件声明。"""
from __future__ import annotations

from copy import deepcopy

import yaml
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr
from ...core.attr_model.character import CharacterProfileManager, evaluate_profile
from ...core.attr_model.models import (
    DIMENSION_LABELS,
    PERCENT_FIELDS,
    ROLE_FIELDS,
    WORKING_FIELDS,
    AttrModelError,
    Formula,
)
from ...core.combat.combat_attrs import COMBAT_ATTR_FIELDS

_LABELS = {name: label for name, label, *_ in COMBAT_ATTR_FIELDS} | DIMENSION_LABELS | ROLE_FIELDS
_KINDS = {"level": "个人等级", "talent": "基础天赋", "oddity": "蹊跷",
          "martial_art": "武学天赋", "inner_way": "心法"}
_STATUS = {"complete": "已录入", "partial": "部分录入", "pending": "待补"}


_PERCENT = PERCENT_FIELDS | {"qi_damage_bonus", "stamina_regen"}
_CONDITION_LABELS = {
    "target.qi_ratio": "目标真气比例", "target.qi_unbalanced": "目标真气失衡",
    "target.exhausted": "目标气竭", "target.kind": "目标类型",
    "self.stamina_ratio": "自身耐力比例", "self.buff": "自身效果",
    "self.in_combat": "自身处于战斗", "skill": "技能", "event": "事件",
}
_VALUE_LABELS = {"player": "玩家", "non_player": "非玩家", True: "是", False: "否"}


def _display(name, value):
    return f"{value * 100:.6g}%" if name in _PERCENT else f"{value:.6g}"


def _condition_text(data):
    if "all" in data or "any" in data:
        key = "all" if "all" in data else "any"
        return (" 且 " if key == "all" else " 或 ").join(
            f"（{_condition_text(child)}）" for child in data[key])
    if "recent_event" in data:
        event = data["recent_event"]
        return f"{event['name']}后 {event['seconds']} 秒内"
    value = data["value"]
    if isinstance(value, bool):
        value = "是" if value else "否"
    elif isinstance(value, str):
        value = _VALUE_LABELS.get(value, value)
    operators = {"eq": "为", "lt": "<", "le": "≤", "gt": ">", "ge": "≥", "contains": "包含"}
    return f"{_CONDITION_LABELS[data['field']]} {operators[data['op']]} {value}"


def _formula_text(formula):
    text = f"{_LABELS[formula.source]} × {formula.multiplier:.8g}"
    if formula.offset:
        text += f" {formula.offset:+.6g}"
    if formula.minimum is not None:
        text += f"，下限 {formula.minimum:.6g}"
    if formula.maximum is not None:
        text += f"，上限 {formula.maximum:.6g}"
    return text


class CharacterProfilePanel(QWidget):
    def __init__(self, parent=None, *, manager=None):
        super().__init__(parent)
        self._manager = manager or CharacterProfileManager()
        self._profile = None
        self._growth_inputs: dict[tuple[str, str], QSpinBox] = {}
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("流派")))
        self._school = QComboBox()
        self._school.addItems(self._manager.schools())
        top.addWidget(self._school)
        self._save_growth = QPushButton(tr("保存成长状态"))
        self._save_growth.clicked.connect(self._on_save_growth)
        top.addWidget(self._save_growth)
        top.addStretch()
        layout.addLayout(top)
        self._notice = QLabel()
        self._notice.setWordWrap(True)
        layout.addWidget(self._notice)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        source_page = QWidget()
        source_layout = QVBoxLayout(source_page)
        splitter = QSplitter()
        self._sources = self._table(["类别", "来源", "记录状态", "当前状态"])
        self._sources.itemSelectionChanged.connect(self._show_source)
        splitter.addWidget(self._sources)
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        splitter.addWidget(self._detail)
        source_layout.addWidget(splitter)
        edit = QPushButton(tr("编辑所选来源"))
        edit.clicked.connect(self._edit_source)
        source_layout.addWidget(edit)
        tabs.addTab(source_page, tr("属性来源"))
        growth_page = QWidget()
        growth_layout = QVBoxLayout(growth_page)
        self._growth_widget = QWidget()
        self._growth_form = QFormLayout(self._growth_widget)
        growth_layout.addWidget(self._growth_widget)
        hint = QLabel(tr("未知档位保留为待补；心法和武学填 0 表示未装备。等级累计表只选对应档，不叠加其他等级。"))
        hint.setWordWrap(True)
        growth_layout.addWidget(hint)
        growth_layout.addStretch()
        tabs.addTab(growth_page, tr("成长状态"))
        attrs_page = QWidget()
        attrs_layout = QVBoxLayout(attrs_page)
        note = QLabel(tr("这里只汇总已录入的静态贡献，含估计值，尚不是完整角色面板。观测值仅供核对，不作为来源相加。条件效果不计入总量。"))
        note.setWordWrap(True)
        attrs_layout.addWidget(note)
        self._attrs = self._table(["属性", "已录入面板贡献", "已录入战斗贡献", "记录的角色观测值"])
        attrs_layout.addWidget(self._attrs)
        tabs.addTab(attrs_page, tr("静态推导"))
        self._declarations = self._table(["来源", "效果", "状态"])
        tabs.addTab(self._declarations, tr("条件与技能效果"))
        equipment_page = QWidget()
        equipment_layout = QVBoxLayout(equipment_page)
        equipment_hint = QLabel(tr("可输入装备提供的直接属性和原始五维，以验证天赋阈值。不要填角色最终面板，也不要重复填写五维转换后的攻击。此处输入仅用于本次预览，不写入内置来源。"))
        equipment_hint.setWordWrap(True)
        equipment_layout.addWidget(equipment_hint)
        self._equipment = QTableWidget(0, 2)
        self._equipment.setHorizontalHeaderLabels([tr("装备贡献字段"), tr("数值（比例使用小数）")])
        self._equipment.setRowCount(len(WORKING_FIELDS))
        from PyQt6.QtCore import Qt
        for row, name in enumerate(WORKING_FIELDS):
            item = QTableWidgetItem(_LABELS.get(name, name))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._equipment.setItem(row, 0, item)
            self._equipment.setItem(row, 1, QTableWidgetItem(""))
        equipment_layout.addWidget(self._equipment)
        calculate = QPushButton(tr("重新推导"))
        calculate.clicked.connect(self._recompute)
        equipment_layout.addWidget(calculate)
        tabs.addTab(equipment_page, tr("装备输入预览"))
        self._school.currentTextChanged.connect(self.reload)
        self.reload()

    @staticmethod
    def _table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels([tr(x) for x in headers])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def reload(self, *_args):
        self._profile = None
        if not self._school.currentText():
            self._notice.setText(tr("尚无内置角色属性表"))
            return
        try:
            self._profile = self._manager.load(self._school.currentText())
        except (AttrModelError, ValueError, OSError) as exc:
            self._notice.setText(str(exc))
            return
        self._growth_inputs.clear()
        while self._growth_form.rowCount():
            self._growth_form.removeRow(0)
        growth = self._profile.growth
        fields = [("", "character_level", "个人等级", growth.get("character_level")),
                  ("", "solo_level", "单人模式等级", growth.get("solo_level"))]
        for group, label in (("martial_arts", "武学"), ("inner_ways", "心法"), ("oddities", "蹊跷进度")):
            fields.extend((group, name, f"{label} · {name}", value)
                          for name, value in growth.get(group, {}).items())
        for group, name, label, value in fields:
            spin = QSpinBox()
            spin.setRange(-1, 6 if group == "inner_ways" else 99999)
            spin.setSpecialValueText(tr("未知"))
            spin.setValue(-1 if value is None else value)
            spin.valueChanged.connect(self._recompute)
            self._growth_inputs[group, name] = spin
            self._growth_form.addRow(tr(label), spin)
        self._recompute()

    def _growth(self):
        result = deepcopy(self._profile.growth)
        for (group, name), widget in self._growth_inputs.items():
            value = None if widget.value() == -1 else widget.value()
            if group:
                result.setdefault(group, {})[name] = value
            else:
                result[name] = value
        return result

    def _recompute(self, *_args):
        if self._profile is None:
            return
        try:
            equipment = {name: float(self._equipment.item(row, 1).text())
                         for row, name in enumerate(WORKING_FIELDS)
                         if self._equipment.item(row, 1).text().strip()}
            result = evaluate_profile(self._profile, growth=self._growth(), equipment=equipment)
        except (AttrModelError, ValueError) as exc:
            self._notice.setText(tr("推导失败：") + str(exc))
            self._attrs.setRowCount(0)
            return
        self._notice.setText(tr("{school} · {sources} 项来源 · {pending} 项待补/部分录入 · {declared} 项效果已声明、未求值\n{notes}").format(
            school=self._profile.school, sources=len(self._profile.sources),
            pending=len(result.missing), declared=len(result.declarations), notes=self._profile.notes))
        self._sources.setRowCount(len(self._profile.sources))
        for row, source in enumerate(self._profile.sources):
            state = "未启用" if source.label in result.excluded else "待补/部分录入" if source.label in result.missing else "已纳入"
            for col, text in enumerate((_KINDS[source.kind], source.label, _STATUS[source.status], state)):
                self._sources.setItem(row, col, QTableWidgetItem(tr(text)))
        self._attrs.setRowCount(len(WORKING_FIELDS))
        for row, name in enumerate(WORKING_FIELDS):
            observed = self._profile.observations.get(name)
            for col, value in enumerate((_LABELS.get(name, name),
                                          _display(name, result.resolved.panel.values[name]),
                                          _display(name, result.resolved.combat.values[name]),
                                          "—" if observed is None else _display(name, observed))):
                self._attrs.setItem(row, col, QTableWidgetItem(value))
        self._declarations.setRowCount(len(result.declarations))
        for row, (label, declaration) in enumerate(result.declarations):
            for col, value in enumerate((label, declaration.get("description") or declaration["target"], "已声明，未求值")):
                self._declarations.setItem(row, col, QTableWidgetItem(value))
        self._show_source()

    def _show_source(self):
        row = self._sources.currentRow()
        if self._profile is None or row < 0 or row >= len(self._profile.sources):
            return
        source = self._profile.sources[row]
        lines = [source.label, _STATUS[source.status], source.notes, "", "静态属性贡献："]
        for name, value in source.effect.stats.items():
            if isinstance(value, Formula):
                lines.append(f"{_LABELS[name]}：{_formula_text(value)}")
                observed = value.apply(self._profile.observations)
                if observed is not None:
                    lines.append(f"  按已记录观测值可得：{_display(name, observed)}")
            else:
                lines.append(f"{_LABELS[name]}：+{_display(name, value)}")
        if not source.effect.stats:
            lines.append("尚无静态数值" if source.status == "pending" else "本条为效果声明")
        for name, value in source.effect.extra.items():
            lines.append(f"{name}：{value:+.6g}")
        for declaration in source.declarations:
            lines.extend(["", "已声明，未求值：" + declaration["target"]])
            if declaration.get("shared_key"):
                lines.append("共享效果：同一效果只保留一次")
            if "condition" in declaration:
                lines.append("条件：" + _condition_text(declaration["condition"]))
            if "value" in declaration:
                value = declaration["value"]
                if isinstance(value, dict):
                    from ...core.attr_model.parsing import parse_formula
                    lines.append("数值：" + _formula_text(parse_formula(value["formula"], "效果")))
                else:
                    lines.append(f"数值/行为：{value}")
            for key, label in (("duration", "持续"), ("interval", "间隔"), ("max_reduction", "每轮最多缩短间隔")):
                if key in declaration:
                    lines.append(f"{label}：{declaration[key]} 秒")
            if declaration.get("description"):
                lines.append(declaration["description"])
        self._detail.setPlainText("\n".join(lines))

    def _on_save_growth(self):
        if self._profile is None:
            return
        raw = deepcopy(self._profile.raw)
        raw["growth"] = self._growth()
        self._save(raw)

    def _save(self, raw):
        try:
            self._manager.save(self._profile.school, raw)
        except (AttrModelError, ValueError, OSError) as exc:
            QMessageBox.warning(self, tr("保存失败"), str(exc))
            return
        self.reload()

    def _edit_source(self):
        row = self._sources.currentRow()
        if self._profile is None or row < 0:
            return
        source = self._profile.sources[row]
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("编辑来源：") + source.label)
        dialog.resize(720, 600)
        layout = QVBoxLayout(dialog)
        editor = QPlainTextEdit(yaml.safe_dump(self._profile.raw["sources"][source.source_id],
                                              allow_unicode=True, sort_keys=False))
        layout.addWidget(editor)
        error = QLabel()
        error.setWordWrap(True)
        layout.addWidget(error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        buttons.rejected.connect(dialog.reject)
        def save():
            raw = deepcopy(self._profile.raw)
            try:
                raw["sources"][source.source_id] = yaml.safe_load(editor.toPlainText())
                self._manager.save(self._profile.school, raw)
            except (AttrModelError, ValueError, OSError, yaml.YAMLError) as exc:
                error.setText(str(exc))
                return
            dialog.accept()
        buttons.accepted.connect(save)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.reload()
