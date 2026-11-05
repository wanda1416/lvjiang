"""与流派判定共用模型的八部位五词条编辑器。"""
from __future__ import annotations

from copy import deepcopy

from lvjiang.apps.shuijiao_tuning.core.build_profiles import (
    cards,
    clean_flexible_pool,
    editable_candidates,
    editor_cards,
    is_divine,
    legacy_rules,
    load_profiles,
    normalize_field,
    preset_totals,
    selected_from_legacy,
)
from PyQt6.QtCore import QPointF, QStringListModel, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPalette, QTextCharFormat, QTextLayout
from PyQt6.QtWidgets import (
    QCheckBox,
    QCompleter,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.config.equipment_slots import SLOT_BY_KEY
from lvjiang.apps.yysls.ui.tune_settings.affix_picker import AffixSelectSortDialog
from lvjiang.ui.combo_box import AutoWidthComboBox

GOLD = "#c79216"
SCHOOL_COLORS = {"鸣金": "#183a70", "牵丝": "#69ad82", "裂石": "#997126", "破竹": "#593365"}


def profile_color(profile):
    from lvjiang.apps.yysls.config import get_game_config
    attr = (get_game_config().get_playstyle(profile["playstyle"]) or {}).get("attr")
    return SCHOOL_COLORS.get(attr, "#52657a")


class AffixLineEdit(QLineEdit):
    """保留原生编辑行为，按词为可选奇术着色。"""
    def paintEvent(self, event):
        super().paintEvent(event)
        token = "全奇术增伤"
        if token not in self.text():
            return
        from PyQt6.QtWidgets import QStyle, QStyleOptionFrame
        option = QStyleOptionFrame()
        self.initStyleOption(option)
        rect = self.style().subElementRect(QStyle.SubElement.SE_LineEditContents, option, self)
        painter = QPainter(self)
        painter.setClipRect(rect)
        painter.fillRect(rect, self.palette().brush(QPalette.ColorRole.Base))
        painter.setPen(self.palette().color(QPalette.ColorRole.Text))
        layout = QTextLayout(self.text(), self.font())
        formats = []
        start = self.text().find(token)
        while start >= 0:
            style = QTextCharFormat()
            style.setForeground(QColor(GOLD))
            span = QTextLayout.FormatRange()
            span.start, span.length, span.format = start, len(token), style
            formats.append(span)
            start = self.text().find(token, start + len(token))
        layout.setFormats(formats)
        layout.beginLayout()
        line = layout.createLine()
        line.setLineWidth(max(rect.width(), self.fontMetrics().horizontalAdvance(self.text()) + 2))
        layout.endLayout()
        width = line.naturalTextWidth()
        x = rect.x() + (rect.width() - width) / 2 if width <= rect.width() else (
            self.cursorRect().x() - self.fontMetrics().horizontalAdvance(self.text()[:self.cursorPosition()]))
        origin = QPointF(x, rect.y() + (rect.height() - line.height()) / 2)
        selections = []
        if self.hasSelectedText():
            selection = QTextLayout.FormatRange()
            selection.start, selection.length = self.selectionStart(), len(self.selectedText())
            selected_style = QTextCharFormat()
            selected_style.setBackground(self.palette().brush(QPalette.ColorRole.Highlight))
            selected_style.setForeground(self.palette().brush(QPalette.ColorRole.HighlightedText))
            selection.format = selected_style
            selections.append(selection)
        layout.draw(painter, origin, selections)
        if self.hasFocus() and not self.isReadOnly():
            layout.drawCursor(painter, origin, self.cursorPosition())


class DivineLabel(QLabel):
    """固定类别仍提供编辑器读取协议，但不创建下拉框。"""
    def __init__(self):
        super().__init__("神力")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def currentData(self):
        return "divine"

    def findData(self, value):
        return 0 if value == "divine" else -1

    def setCurrentIndex(self, index):
        pass


class CenteredModeCombo(AutoWidthComboBox):
    def paintEvent(self, event):
        from PyQt6.QtWidgets import QStyle, QStyleOptionComboBox, QStylePainter
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        caption = option.currentText
        option.currentText = ""
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        rect = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                           QStyle.SubControl.SC_ComboBoxEditField, self)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, caption)


class ProfileRequirementsWidget(QWidget):
    config_changed = pyqtSignal()
    layout_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.catalog = load_profiles()
        self._overrides: dict = {}
        self._mode_overrides: dict = {}
        self._mode_controls: dict[str, list] = {}
        self._library_buttons: dict[str, list[QPushButton]] = {}
        self._strict_parts: list[str] = []
        self._strict_flexible = True
        self.save_strict_callback = None
        self._display_order = {}
        self.setObjectName("profileEditor")
        self.setStyleSheet("""
            #profileEditor {background: #f4f7fb;}
            #profileEditor QLabel {color: #52657a;}
            #profileEditor QGroupBox {border: 1px solid #dce4ef; border-radius: 9px;
                margin-top: 12px; padding: 8px 6px 6px; background: #ffffff;}
            #profileEditor QGroupBox::title {subcontrol-origin: margin; left: 14px;
                padding: 0 6px; color: #243b53; font-weight: 600;}
            #profileEditor QLineEdit {border: 1px solid #dce4ef; border-radius: 5px;
                padding: 3px 5px; background: #f8fafc; min-height: 18px;}
            #profileEditor QLineEdit:focus {border: 1px solid #4b86c6;}
            #profileEditor QPushButton {padding: 5px 10px; border-radius: 5px;
                border: 1px solid #cbd7e6; background: #eef4fb; color: #294d74;}
            #profileEditor QCheckBox {spacing: 7px; padding: 2px;}
        """)
        self._loading = False
        self._shown = ""
        self._fields: dict[str, list[QLineEdit]] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(4)
        root.addWidget(QLabel("培养目标（可多选；其他流派毕业装由跨流派保护保留）"))
        choices = QGridLayout()
        self._checks = {}
        for i, (key, profile) in enumerate(self.catalog.items()):
            cb = QCheckBox(profile["label"])
            cb.setStyleSheet(f"color: {profile_color(profile)}; font-weight: 600;")
            cb.toggled.connect(self._selection_changed)
            self._checks[key] = cb
            choices.addWidget(cb, i // 7, i % 7)
        root.addLayout(choices)
        row = QHBoxLayout()
        row.addWidget(QLabel("查看／编辑流派"))
        self.selector = AutoWidthComboBox(width_mode="full")
        for key, profile in self.catalog.items():
            self.selector.addItem(profile["label"], key)
            self.selector.setItemData(self.selector.count() - 1, QColor(profile_color(profile)), Qt.ItemDataRole.ForegroundRole)
        self.selector.currentIndexChanged.connect(self._show_profile)
        row.addWidget(self.selector, 1)
        reset = QPushButton("恢复该流派要求")
        reset.clicked.connect(self._reset_current)
        row.addWidget(reset)
        root.addLayout(row)
        strict_row = QHBoxLayout()
        self.strict_enabled = QCheckBox("严格调律")
        self.strict_enabled.setToolTip("需包含首条、全部刚需及必需神力，最多一次合法转律；不保留缺刚需过渡装。保存后生效。")
        strict_row.addWidget(self.strict_enabled)
        strict_row.addStretch()
        self.strict_save_button = QPushButton("保存")
        self.strict_save_button.clicked.connect(self._save_strict)
        strict_row.addWidget(self.strict_save_button)
        root.addLayout(strict_row)
        self.strict_panel = QWidget()
        strict_body = QVBoxLayout(self.strict_panel)
        strict_body.setContentsMargins(0, 0, 0, 0)
        strict_body.addWidget(QLabel("所选部位必须满足全部刚需与必需神力（最多一次合法转律），不留缺刚需过渡装。"))
        from lvjiang.apps.shuijiao_tuning.core.build_profiles import PART_NAMES, PARTS
        part_row = QHBoxLayout()
        self.strict_checks = {}
        for key, label in zip(PARTS, PART_NAMES, strict=True):
            cb = QCheckBox(label)
            cb.toggled.connect(self._strict_draft_changed)
            self.strict_checks[key] = cb
            part_row.addWidget(cb)
        strict_body.addLayout(part_row)
        self.strict_flexible_check = QCheckBox("其余非刚需词条也必须来自蓝色灵活候选池")
        self.strict_flexible_check.setChecked(True)
        self.strict_flexible_check.toggled.connect(self._strict_draft_changed)
        strict_body.addWidget(self.strict_flexible_check)
        self.strict_panel.hide()
        self.strict_enabled.toggled.connect(self._strict_draft_changed)
        root.addWidget(self.strict_panel)
        self.strict_summary = QLabel()
        self.strict_summary.setWordWrap(True)
        root.addWidget(self.strict_summary)
        self._update_strict_summary()
        self.note = QLabel()
        self.note.setWordWrap(True)
        root.addWidget(self.note)
        help_label = QLabel('<span style="color:#8b0000;font-weight:600">深红：固定首条</span>　'
                            '<span style="color:#d33b3b;font-weight:600">红色：刚需</span>　'
                            '<span style="color:#1565c0;font-weight:600">蓝色：灵活候选</span>　'
                            '<span style="color:#c79216;font-weight:600">金色：神力</span>')
        help_label.setToolTip("后四条无顺序要求且同名最多一次；所有灵活框共用候选池。普通模式可留过渡装，满条缺必需神力回收。")
        help_label.setWordWrap(True)
        root.addWidget(help_label)
        self._cards_container = QGroupBox("八部位目标规则")
        self._cards_layout = QGridLayout(self._cards_container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._cards_container)
        root.addStretch()
        self._show_profile()

    def set_config(self, config: dict):
        self._loading = True
        try:
            self._overrides = deepcopy(config.get("profile_overrides", {}))
            self._mode_overrides = deepcopy(config.get("profile_field_modes", {}))
            self._strict_parts = list(config.get("strict_parts", []))
            self._strict_flexible = config.get("strict_flexible", True)
            self._load_strict_controls()
            self._update_strict_summary()
            selected = config.get("build_profiles")
            if selected is None:
                selected = selected_from_legacy(config.get("rules", {}), self.catalog)
            for key, cb in self._checks.items():
                cb.setChecked(key in selected)
            self._shown = ""
            self._show_profile()
        finally:
            self._loading = False

    def get_config(self) -> dict:
        self._store_fields()
        return {"build_profiles": [k for k, cb in self._checks.items() if cb.isChecked()],
                "profile_overrides": deepcopy(self._overrides), "profile_field_modes": deepcopy(self._mode_overrides),
                "strict_parts": list(self._strict_parts), "strict_flexible": self._strict_flexible}

    def _update_strict_summary(self):
        from lvjiang.apps.shuijiao_tuning.core.build_profiles import PART_NAMES, PARTS
        names = [label for key, label in zip(PARTS, PART_NAMES, strict=True) if key in self._strict_parts]
        tail = "其余词条限灵活池" if self._strict_flexible else "其余词条不限"
        self.strict_summary.setText("严格调律：" + ("、".join(names) + " · " + tail if names else "未启用（普通调律，可保留过渡装）"))

    def _load_strict_controls(self):
        self.strict_enabled.setChecked(bool(self._strict_parts))
        for key, check in self.strict_checks.items():
            check.setChecked(key in self._strict_parts)
        self.strict_flexible_check.setChecked(self._strict_flexible)
        self.strict_panel.setVisible(bool(self._strict_parts))

    def _strict_draft_changed(self, *_):
        if self._loading:
            return
        self.strict_panel.setVisible(self.strict_enabled.isChecked())
        self._update_strict_summary()
        self.strict_summary.setText(self.strict_summary.text() + "（选择尚未保存，点击保存后生效）")
        self.layout_changed.emit()

    def _save_strict(self):
        parts = [key for key, check in self.strict_checks.items() if check.isChecked()] if self.strict_enabled.isChecked() else []
        if self.strict_enabled.isChecked() and not parts:
            QMessageBox.warning(self, "请选择部位", "请至少勾选一个严格调律部位，再点击保存。")
            return
        previous = (list(self._strict_parts), self._strict_flexible)
        self._strict_parts = parts
        self._strict_flexible = self.strict_flexible_check.isChecked()
        try:
            if self.save_strict_callback is None or self.save_strict_callback() is not True:
                raise ValueError("尚未选择执行用户，无法保存严格调律设置")
        except Exception as exc:
            self._strict_parts, self._strict_flexible = previous
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._update_strict_summary()
        from lvjiang.apps.shuijiao_tuning.core.build_profiles import PART_NAMES, PARTS
        names = [label for key, label in zip(PARTS, PART_NAMES, strict=True) if key in parts]
        message = "保存成功，" + "、".join(names) + "将按照严格调律进行。" if names else "保存成功，已关闭严格调律。"
        self._saved_notice = QMessageBox(QMessageBox.Icon.Information, "保存成功", message, parent=self)
        self._saved_notice.setWindowModality(Qt.WindowModality.NonModal)
        self._saved_notice.show()

    def get_rules(self) -> dict:
        return legacy_rules(self.get_config()["build_profiles"], self.catalog)

    def _selection_changed(self):
        if not self._loading:
            self.config_changed.emit()

    def _store_fields(self):
        if not self._shown or self._loading:
            return
        defaults = {c["key"]: c["fields"] for c in cards(self.catalog[self._shown])}
        saved = {}
        for key, fields in self._fields.items():
            values = [f.text().strip() if f.text().strip() != "------" else "" for f in fields]
            if values != defaults[key]:
                saved[key] = values
        if saved:
            self._overrides[self._shown] = saved
        else:
            self._overrides.pop(self._shown, None)
        self._mode_overrides[self._shown] = {key: ["initial", *[control.currentData() for control in controls[1:]]]
                                               for key, controls in self._mode_controls.items()}

    def _field_changed(self):
        if self._loading:
            return
        for key, controls in self._mode_controls.items():
            for index, control in enumerate(controls[1:], 1):
                if control.findData("divine") >= 0:
                    control.blockSignals(True)
                    control.setCurrentIndex(control.findData("divine"))
                    control.blockSignals(False)
                    target = next(c["divine"] for c in cards(self.catalog[self._shown]) if c["key"] == key)
                    self._fields[key][index].setText(target)
                if control.currentData() == "none":
                    self._fields[key][index].clear()
            live = {"fields": [normalize_field(f.text()) for f in self._fields[key]]}
            modes = ["initial", *[c.currentData() for c in controls[1:]]]
            for index in range(1, 5):
                if modes[index] == "flexible":
                    self._fields[key][index].setText(clean_flexible_pool(self.catalog[self._shown], live, modes, live["fields"][index]))
        self._refresh_colors()
        self._store_fields()
        for card in cards(self.catalog[self._shown]):
            key = card["key"]
            card["fields"] = [normalize_field(f.text()) for f in self._fields[key]]
            modes = ["initial", *[c.currentData() for c in self._mode_controls[key][1:]]]
            for index in range(1, 5):
                completer = self._fields[key][index].completer()
                model = completer.model() if completer is not None else None
                if isinstance(model, QStringListModel):
                    options = editable_candidates(self.catalog[self._shown], card, index)
                    model.setStringList([t for t in options if modes[index] != "flexible" or
                                         clean_flexible_pool(self.catalog[self._shown], card, modes, t)])
        self.config_changed.emit()

    def _refresh_colors(self, *_):
        for key, fields in self._fields.items():
            for index, field in enumerate(fields):
                control = self._mode_controls[key][index]
                active = index == 0 or control.currentData() != "none"
                divine = bool(index and control.currentData() == "divine")
                field.setEnabled(active)
                field.setReadOnly(divine)
                self._library_buttons[key][index].setEnabled(active and not divine)
                if control is not None:
                    control.setEnabled(not divine)
                color = "#8b0000" if index == 0 else "#c79216" if divine else "#d33b3b" if control.currentData() == "required" else "#1565c0"
                if not active:
                    color = "#999999"
                field.setStyleSheet("background: #f1f1f1; color: #999999;" if not active else
                                   f"color: {color}; font-weight: 600;" if field.text().strip() else "")
                field.setToolTip(("神力由流派与部位固定，不可编辑、不可转律补出：" if divine else "") + (field.text() or "未配置词条"))
                if control is not None:
                    control.setStyleSheet(f"color: {color};")

    def _show_profile(self, *_):
        self._store_fields()
        self._shown = self.selector.currentData() or ""
        self._fields = {}
        self._mode_controls = {}
        self._library_buttons = {}
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if item and item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if not self._shown:
            return
        profile = self.catalog[self._shown]
        mode_name = {"small": "小外", "large": "大外", "middle": "中外", "wei": "威威"}[profile["mode"]]
        totals = preset_totals(profile)
        goal = (f"整套预设：12大外＋{totals['神力']}神力。" if profile["mode"] == "large" else
                f"整套预设：12小外＋8敏＋{totals['神力']}神力。" if profile["mode"] == "small" else "")
        self.note.setText(f"当前查看：{profile['label']} · {mode_name}。" + goal + profile.get("set_goal", ""))
        for card in editor_cards(profile, self._overrides.get(self._shown), self._mode_overrides.get(self._shown)):
            group = QGroupBox(card["label"])
            form = QVBoxLayout(group)
            form.setSpacing(4)
            edits = []
            controls = []
            buttons = []
            rows = []
            for index, value in enumerate(card["fields"]):
                row = QHBoxLayout()
                control = None
                if index == 0:
                    label = QLabel("首条")
                    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    label.setStyleSheet("color: #8b0000; font-weight: 600;")
                    row.addWidget(label)
                elif card["modes"][index] == "divine":
                    control = DivineLabel()
                    row.addWidget(control)
                else:
                    control = CenteredModeCombo(width_mode="full")
                    control.addItem("刚需", "required")
                    control.addItem("灵活", "flexible")
                    control.addItem("不选择", "none")
                    if card["modes"][index] == "divine":
                        control.addItem("神力", "divine")
                    control.setCurrentIndex(control.findData(card["modes"][index]))
                    control.currentIndexChanged.connect(self._field_changed)
                    row.addWidget(control)
                controls.append(control)
                edit = AffixLineEdit(value)
                edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
                edit.setCursorPosition(0)
                edit.setMinimumWidth(120)
                edit.setPlaceholderText("------")
                edit.setAccessibleName(f"{profile['label']} {card['label']} 词条{index + 1}")
                edit.setToolTip("固定首条范围，不参与转律" if index == 0 else "整件必需词条，不要求按此顺序调出")
                edit.setStyleSheet("color: #d33b3b; font-weight: 600;" if value else "")
                options = editable_candidates(profile, card, index)
                if index and card["modes"][index] == "flexible":
                    options = [t for t in options if clean_flexible_pool(profile, card, card["modes"], t)]
                completer = QCompleter(options, edit)
                completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
                edit.setCompleter(completer)
                edit.editingFinished.connect(self._field_changed)
                row.addWidget(edit, 1)
                library = QPushButton("编辑")
                library.setFixedWidth(52)
                library.setToolTip("从词条库编辑此框；多选表示备选词条")
                library.clicked.connect(lambda _=False, c=card, n=index, e=edit: self._choose_affix(c, n, e))
                row.addWidget(library)
                if index and card["modes"][index] == "divine":
                    library.hide()
                buttons.append(library)
                rows.append(row)
                edits.append(edit)
            divine_indices = [i for i in range(1, 5) if any(is_divine(t) for t in card["fields"][i].split("/") if t)]
            order = [0, *divine_indices, *[i for i in range(1, 5) if i not in divine_indices]]
            self._display_order[card["key"]] = order
            for index in order:
                form.addLayout(rows[index])
            self._fields[card["key"]] = edits
            self._mode_controls[card["key"]] = controls
            self._library_buttons[card["key"]] = buttons
            for edit in edits:
                edit.textChanged.connect(self._refresh_colors)
            spec = SLOT_BY_KEY[card["key"]]
            self._cards_layout.addWidget(group, spec.row, spec.col)
        self._refresh_colors()
        self.selector.setStyleSheet(f"color: {profile_color(profile)}; font-weight: 600;")
        self.layout_changed.emit()

    def _choose_affix(self, card, index, edit):
        candidates = editable_candidates(self.catalog[self._shown], card, index)
        if index and self._mode_controls[card["key"]][index].currentData() == "flexible":
            live = {"fields": [normalize_field(f.text()) for f in self._fields[card["key"]]]}
            modes = ["initial", *[c.currentData() for c in self._mode_controls[card["key"]][1:]]]
            candidates = [t for t in candidates if clean_flexible_pool(self.catalog[self._shown], live, modes, t)]
        dialog = AffixSelectSortDialog(candidates, [s for s in edit.text().split("/") if s],
                                      "编辑词条候选（多选以 / 分隔）", self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            edit.setText("/".join(dialog.selected()))
            self._field_changed()

    def _reset_current(self):
        self._overrides.pop(self._shown, None)
        self._mode_overrides.pop(self._shown, None)
        self._shown = ""
        self._show_profile()
        self.config_changed.emit()
