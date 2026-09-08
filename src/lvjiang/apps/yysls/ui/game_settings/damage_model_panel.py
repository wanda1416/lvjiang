"""伤害建模面板：技能系数表与增益表。

毕业率方案 JSON 里那份 ``program`` 能精确复现整张 Excel，但它是两千多个
节点的表达式图——看不出「第一道剑气的外功倍率是 1.3066」，也改不动。
这一页就是那份程序的可读面：左侧选技能，右侧是它的四个系数与自带加成，
下面是增益表。

**这一页不参与求值**。毕业率仍走编译程序，改这里的系数不会改变毕业率
结果——写第二份公式就是两份真相。所以页面顶部一直显示与配套方案的
同源校验：sha256 对不上就说明有一边换过表，页面上的系数可能已经过期。
"""
from __future__ import annotations

import yaml
from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.button_styles import (
    apply_button_style,
    apply_dialog_button_box_style,
)
from lvjiang.ui.theme import get_theme_manager

from .....i18n import tr
from ...core.damage import (
    MODIFIER_FIELDS,
    RATIO_FIELDS,
    get_damage_model_manager,
    invalidate_damage_model_cache,
)
from ..layout_helpers import configure_navigation_list


def _status_color(status: str) -> QBrush:
    """状态色取自主题令牌，跟随明暗主题，不写死颜色值"""
    tokens = get_theme_manager().tokens
    return QBrush(QColor({
        "done": tokens.success,
        "pending": tokens.warning,
        "muted": tokens.text_muted,
    }[status]))


class _SkillDialog(QDialog):
    """直接编辑一个技能的 YAML

    系数只有四个，但自带加成（`modifiers`）与强制结算（`force`）的组合
    太散——为每种组合做一套控件，还不如让人看见配置本身。
    """

    def __init__(self, name: str, payload: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("编辑技能：{name}").format(name=name))
        self.resize(560, 460)
        layout = QVBoxLayout(self)
        hint = QLabel(tr(
            "系数：{ratios}；另可写 kind / charge / qi_ratio / modifiers / force"
        ).format(ratios=" ".join(RATIO_FIELDS)))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        layout.addWidget(hint)
        self._editor = QPlainTextEdit(
            yaml.dump(payload, allow_unicode=True, sort_keys=False))
        layout.addWidget(self._editor)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        apply_dialog_button_box_style(buttons)
        layout.addWidget(buttons)

    def payload(self) -> dict:
        return yaml.safe_load(self._editor.toPlainText()) or {}


class DamageModelPanel(QWidget):
    """伤害建模主面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._skills: list[str] = []
        self._build_ui()
        self._reload()

    # ── 构建 ──

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel(tr("流派")))
        self._combo_school = QComboBox()
        self._combo_school.currentIndexChanged.connect(self._refresh_skills)
        top.addWidget(self._combo_school)
        self._source = QLabel()
        self._source.setStyleSheet("color: palette(mid); font-size: 11px;")
        top.addWidget(self._source)
        top.addStretch()
        layout.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("搜索技能"))
        self._search.textChanged.connect(self._refresh_skills)
        left_layout.addWidget(self._search)
        self._list = QListWidget()
        configure_navigation_list(self._list, minimum_width=200)
        self._list.currentItemChanged.connect(self._on_skill_selected)
        left_layout.addWidget(self._list)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels([tr("字段"), tr("取值")])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        rows_header = self._table.verticalHeader()
        if rows_header is not None:
            rows_header.setVisible(False)
        right_layout.addWidget(self._table)

        self._btn_edit = QPushButton(tr("编辑该技能"))
        self._btn_edit.clicked.connect(self._on_edit)
        apply_button_style(self._btn_edit)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._btn_edit)
        right_layout.addLayout(row)

        right_layout.addWidget(QLabel(tr("增益表")))
        self._buffs = QTableWidget(0, 2)
        self._buffs.setHorizontalHeaderLabels([tr("增益"), tr("效果")])
        self._buffs.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        buff_header = self._buffs.horizontalHeader()
        if buff_header is not None:
            buff_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        buff_rows = self._buffs.verticalHeader()
        if buff_rows is not None:
            buff_rows.setVisible(False)
        right_layout.addWidget(self._buffs)

        note = QLabel(tr(
            "本页是毕业率编译程序的可读参考层，改这里的系数不会改变毕业率"
            "结果；表换了版本要重跑 scripts/extract_damage_model.py"))
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid); font-size: 11px;")
        right_layout.addWidget(note)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 650])
        layout.addWidget(splitter)

    # ── 数据 ──

    def _manager(self):
        return get_damage_model_manager()

    def _school(self) -> str:
        return self._combo_school.currentText()

    def _model(self):
        return self._manager().model(self._school())

    def _reload(self) -> None:
        invalidate_damage_model_cache()
        errors = self._manager().errors()
        if errors:
            logger.error(f"伤害模型加载有错: {errors}")
        previous = self._school()
        self._combo_school.blockSignals(True)
        self._combo_school.clear()
        self._combo_school.addItems(self._manager().schools())
        if previous:
            index = self._combo_school.findText(previous)
            if index >= 0:
                self._combo_school.setCurrentIndex(index)
        self._combo_school.blockSignals(False)
        self._refresh_skills()

    def reload(self) -> None:
        self._reload()

    def _refresh_skills(self) -> None:
        model = self._model()
        self._list.clear()
        self._skills = []
        if model is None:
            self._source.setText(tr("还没有任何流派的伤害模型"))
            self._table.setRowCount(0)
            self._buffs.setRowCount(0)
            return

        done, total = model.progress()
        parts = [tr("{file}  技能 {done}/{total}").format(
            file=model.source.get("file", "-"), done=done, total=total)]
        mismatch = self._manager().mismatched(model.school)
        if mismatch:
            parts.append(mismatch)
        self._source.setText("　".join(parts))

        keyword = self._search.text().strip()
        for skill in model.skills:
            if keyword and keyword not in skill.name:
                continue
            item = QListWidgetItem(skill.name)
            item.setForeground(_status_color("done" if skill.modeled else "pending"))
            if not skill.modeled:
                item.setToolTip(tr("四个系数都是 0，还没填"))
            self._list.addItem(item)
            self._skills.append(skill.name)
        self._fill_buffs(model)
        if self._skills:
            self._list.setCurrentRow(0)
        else:
            self._table.setRowCount(0)

    def _fill_buffs(self, model) -> None:
        self._buffs.setRowCount(len(model.buffs))
        for row, buff in enumerate(model.buffs):
            self._buffs.setItem(row, 0, QTableWidgetItem(buff.name))
            text = "　".join(
                f"{tr(MODIFIER_FIELDS[name])} {value:+.4g}"
                for name, value in buff.modifiers.items()
            )
            cell = QTableWidgetItem(text or tr("无静态加成"))
            if not text:
                cell.setForeground(_status_color("muted"))
            self._buffs.setItem(row, 1, cell)

    def _on_skill_selected(self, *_args) -> None:
        row = self._list.currentRow()
        model = self._model()
        if model is None or not 0 <= row < len(self._skills):
            self._table.setRowCount(0)
            return
        skill = model.skill(self._skills[row])
        if skill is None:
            self._table.setRowCount(0)
            return
        rows: list[tuple[str, str]] = [(tr("类型"), skill.kind or "-")]
        rows += [
            (tr(label), f"{getattr(skill, name):.6g}")
            for name, label in RATIO_FIELDS.items()
        ]
        rows.append((tr("蓄力技定音"), tr("是") if skill.charge else tr("否")))
        rows.append((tr("真气比例"), f"{skill.qi_ratio:.6g}"))
        for name, value in skill.modifiers.items():
            rows.append((tr(MODIFIER_FIELDS[name]), f"{value:+.6g}"))
        for name, enabled in skill.force.items():
            if enabled:
                rows.append((tr("强制结算"), name))
        self._table.setRowCount(len(rows))
        for index, (label, value) in enumerate(rows):
            self._table.setItem(index, 0, QTableWidgetItem(label))
            self._table.setItem(index, 1, QTableWidgetItem(value))

    # ── 编辑 ──

    def _on_edit(self) -> None:
        row = self._list.currentRow()
        model = self._model()
        if model is None or not 0 <= row < len(self._skills):
            return
        name = self._skills[row]
        raw = self._raw_skill(model, name)
        dialog = _SkillDialog(name, raw, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._manager().save_skill(model.school, name, dialog.payload())
        except Exception as exc:
            logger.error(f"保存技能 {name} 失败: {exc}")
            QMessageBox.warning(self, tr("保存失败"), str(exc))
            return
        self._refresh_skills()

    def _raw_skill(self, model, name: str) -> dict:
        """技能的 YAML 片段。空字段不写进去，编辑框才不会一屏都是 0。"""
        skill = model.skill(name)
        if skill is None:
            return {}
        payload: dict = {}
        if skill.kind:
            payload["kind"] = skill.kind
        if skill.charge:
            payload["charge"] = True
        if skill.qi_ratio:
            payload["qi_ratio"] = skill.qi_ratio
        for field_name in RATIO_FIELDS:
            value = getattr(skill, field_name)
            if value:
                payload[field_name] = value
        if skill.modifiers:
            payload["modifiers"] = dict(skill.modifiers)
        if skill.force:
            payload["force"] = dict(skill.force)
        return payload
