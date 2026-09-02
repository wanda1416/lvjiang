"""基础属性创建对话框 Mix-in —— 创建/保存基础属性配置。

职责：
- _CreatePlayStyleDialog：输入面板属性，反推基础值
- PlayStyleDialogMixin：对话框调度、保存逻辑
"""
from __future__ import annotations

from typing import cast

from loguru import logger
from PyQt6.QtCore import QLocale, Qt
from PyQt6.QtGui import QDoubleValidator
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ......i18n import tr
from ......ui.button_styles import apply_dialog_button_box_style
from ....core.combat.combat_attrs import (
    COMBAT_ATTR_FIELDS,
    PLAY_STYLE_FIELD_GROUPS,
    CombatAttributes,
)

# 样式常量（与 attrs_tab 共享）
_CARD_STYLE = """
    #combatAttrCard {
        background: palette(base);
        border: 1px solid palette(mid);
        border-radius: 6px;
    }
"""
_TITLE_STYLE = "font-size: 15px; font-weight: 600;"
_NAME_STYLE = "font-size: 13px; color: palette(mid);"


def _format_prefill(value: float) -> str:
    """预填数值格式化：整数值不带小数点（"170.0" → "170"），其余保留原样。"""
    if value == int(value):
        return str(int(value))
    return str(value)


class PlayStyleDialogMixin:
    """基础属性创建 Mix-in。

    需要主类提供：
    - self._get_current_school() -> str
    - self._compute_equip_base_attrs() -> CombatAttributes
    - self._compute_equip_attrs() -> CombatAttributes
    - self._compute_gongjue_attrs() -> CombatAttributes
    - self._refresh_play_styles()
    - self._combo_play_style (QComboBox)
    """

    def _play_style_dialog_parent(self) -> QWidget:
        """Return the concrete QWidget host expected by Qt dialog APIs."""
        return cast(QWidget, self)

    def _on_create_play_style(self):
        """创建基础属性按钮 → 弹出对话框。"""
        school = self._get_current_school()
        if not school:
            QMessageBox.warning(
                self._play_style_dialog_parent(),
                tr("无法创建"),
                tr("请先选择一个流派"),
            )
            return

        # 获取流派属性
        from ....config import get_game_config
        gc = get_game_config()
        school_attr = gc.get_school_attr(school)

        dlg = _CreatePlayStyleDialog(
            self._play_style_dialog_parent(), school_attr=school_attr)
        self._finish_play_style_dialog(school, dlg)

    def _on_open_play_style_form(self, prefill: dict):
        """工作流触发（open_play_style_form 信号）→ 弹出对话框并预填 OCR 识别结果。

        与手动点击「创建基础属性」按钮走同一套保存尾逻辑，唯一区别是
        对话框构造时带上 initial_values 预填。当前未选择流派时提示用户
        先选择，不静默丢弃识别结果。
        """
        school = self._get_current_school()
        if not school:
            self._show_workflow_message(
                QMessageBox.Icon.Warning,
                tr("无法创建"),
                tr("请先选择一个流派后再确认基础属性识别结果"),
            )
            return

        from ....config import get_game_config
        gc = get_game_config()
        school_attr = gc.get_school_attr(school)

        dlg = _CreatePlayStyleDialog(
            self._play_style_dialog_parent(),
            school_attr=school_attr,
            initial_values=prefill,
        )
        self._finish_play_style_dialog(school, dlg, workflow_triggered=True)

    def _finish_play_style_dialog(
        self,
        school: str,
        dlg: "_CreatePlayStyleDialog",
        *,
        workflow_triggered: bool = False,
    ):
        """展示对话框并处理保存：重名检查、反推基础值、写入、刷新。

        手动按钮（_on_create_play_style）和工作流预填触发
        （_on_open_play_style_form）共用保存逻辑。工作流触发时必须非模态：
        自动化本身不等待表单，主窗口也不能因表单而被禁用。
        """
        if workflow_triggered:
            dlg.setModal(False)
            dlg.setWindowModality(Qt.WindowModality.NonModal)
            dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            self._workflow_play_style_dialog = dlg

            def finished(code: int) -> None:
                if getattr(self, "_workflow_play_style_dialog", None) is dlg:
                    self._workflow_play_style_dialog = None
                if code == QDialog.DialogCode.Accepted:
                    self._process_play_style_dialog(
                        school, dlg, workflow_triggered=True)

            dlg.finished.connect(finished)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            return

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._process_play_style_dialog(school, dlg, workflow_triggered=False)

    def _process_play_style_dialog(
        self,
        school: str,
        dlg: "_CreatePlayStyleDialog",
        *,
        workflow_triggered: bool,
    ) -> None:
        """Validate an accepted form, then save now or after overwrite choice."""

        panel_attrs = dlg.get_panel_attrs()
        name = dlg.get_play_style_name()

        if not name:
            if workflow_triggered:
                self._show_workflow_message(
                    QMessageBox.Icon.Warning,
                    tr("无法保存"),
                    tr("基础属性名称不能为空"),
                )
            else:
                QMessageBox.warning(
                    self._play_style_dialog_parent(),
                    tr("无法保存"),
                    tr("基础属性名称不能为空"),
                )
            return

        # 检查重名
        from ....config import get_play_styles
        existing = get_play_styles(school)
        if name in existing:
            message = tr("基础属性「{name}」已存在，是否覆盖？").format(
                name=name
            )
            if workflow_triggered:
                self._show_workflow_overwrite(
                    school, name, panel_attrs, message
                )
                return
            ret = QMessageBox.question(
                self._play_style_dialog_parent(), tr("基础属性已存在"),
                message,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return

        self._commit_play_style(
            school, name, panel_attrs, workflow_triggered=workflow_triggered
        )

    def _show_workflow_overwrite(
        self,
        school: str,
        name: str,
        panel_attrs: CombatAttributes,
        message: str,
    ) -> None:
        """Ask overwrite without disabling the main window."""
        box = self._show_workflow_message(
            QMessageBox.Icon.Question,
            tr("基础属性已存在"),
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        def save_if_confirmed(_code: int) -> None:
            clicked = box.clickedButton()
            if (clicked is not None and
                    box.standardButton(clicked) == QMessageBox.StandardButton.Yes):
                self._commit_play_style(
                    school, name, panel_attrs, workflow_triggered=True
                )

        box.finished.connect(save_if_confirmed)

    def _commit_play_style(
        self,
        school: str,
        name: str,
        panel_attrs: CombatAttributes,
        *,
        workflow_triggered: bool,
    ) -> None:
        """Reverse equipment contributions, persist, then refresh the selector."""

        # 反推：base = panel - equip_base - equip_affix - gongjue
        # equip_base: 装备基础外功攻击（武器/环/佩，根据品阶不同）
        # equip_affix: 装备词条属性（含劲/势/敏五维转换）
        # gongjue: 弓玦属性
        # 注意：穿透类用户填写的就是基础值，不需要扣减装备
        equip_base_attrs = self._compute_equip_base_attrs()
        equip_attrs = self._compute_equip_attrs()
        gongjue_attrs = self._compute_gongjue_attrs()
        base_attrs = panel_attrs - equip_base_attrs - equip_attrs - gongjue_attrs

        # 穿透类特殊处理：用户填写的就是基础值，直接保存
        from ....core.combat.combat_attrs import PENETRATION_FIELDS
        for pen_field in PENETRATION_FIELDS:
            panel_val = getattr(panel_attrs, pen_field, 0.0)
            setattr(base_attrs, pen_field, panel_val)

        # 保存
        try:
            self._save_play_style(school, name, base_attrs)
        except Exception as exc:
            if workflow_triggered:
                self._show_workflow_message(
                    QMessageBox.Icon.Warning, tr("无法保存"), str(exc)
                )
            else:
                QMessageBox.warning(
                    self._play_style_dialog_parent(), tr("无法保存"), str(exc)
                )
            return

        saved_message = tr(
            "基础属性「{name}」已保存到流派「{school}」"
        ).format(name=name, school=school)
        if workflow_triggered:
            self._show_workflow_message(
                QMessageBox.Icon.Information, tr("保存成功"), saved_message
            )
        else:
            QMessageBox.information(
                self._play_style_dialog_parent(), tr("保存成功"), saved_message
            )

        # 刷新
        self._refresh_play_styles()
        self._combo_play_style.setCurrentText(name)

    def _show_workflow_message(
        self,
        icon: QMessageBox.Icon,
        title: str,
        message: str,
        buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
    ) -> QMessageBox:
        """Show a workflow-originated message while keeping the host interactive."""
        parent = self._play_style_dialog_parent()
        box = QMessageBox(icon, title, message, buttons, parent)
        box.setModal(False)
        box.setWindowModality(Qt.WindowModality.NonModal)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialogs = getattr(self, "_workflow_message_dialogs", None)
        if dialogs is None:
            dialogs = set()
            self._workflow_message_dialogs = dialogs
        dialogs.add(box)
        box.finished.connect(lambda _code, item=box: dialogs.discard(item))
        box.show()
        box.raise_()
        box.activateWindow()
        return box

    def _save_play_style(self, school: str, name: str, base_attrs: CombatAttributes):
        """保存基础属性到 session 配置（仅保存允许的字段）。"""
        from ....config import get_game_config, save_play_style
        from ....core.combat.combat_attrs import SCHOOL_ATTR_FIELD_MAP

        # 解析占位符：根据流派属性获取实际字段名
        gc = get_game_config()
        school_attr = gc.get_school_attr(school)
        attr_map = SCHOOL_ATTR_FIELD_MAP.get(school_attr, {}) if school_attr else {}

        play_style_data = {}
        for _, fields in PLAY_STYLE_FIELD_GROUPS:
            for fn, _, _ in fields:
                # 解析占位符
                if fn == "__attr_pen__":
                    fn = attr_map.get("attr_pen", "")
                elif fn == "__attr_bonus__":
                    fn = attr_map.get("attr_bonus", "")
                elif fn == "__min_attr__":
                    fn = attr_map.get("min_attr", "")
                elif fn == "__max_attr__":
                    fn = attr_map.get("max_attr", "")
                if not fn or fn.startswith("__"):
                    continue
                v = getattr(base_attrs, fn, 0)
                if v:
                    play_style_data[fn] = v

        try:
            save_play_style(school, name, play_style_data)
        except Exception as e:
            logger.error(f"保存基础属性失败: {e}")
            raise


class _CreatePlayStyleDialog(QDialog):
    """创建基础属性对话框 — 输入面板属性，反推并保存基础值。"""

    def __init__(self, parent=None, school_attr: str | None = None,
                 initial_values: dict[str, float] | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("创建基础属性"))
        self.setMinimumWidth(720)
        self._school_attr = school_attr
        self._edits: dict[str, QLineEdit] = {}
        self._initial_values = self._resolve_initial_values(initial_values, school_attr)
        self._setup_ui()

    @staticmethod
    def _resolve_initial_values(
        initial_values: dict[str, float] | None, school_attr: str | None,
    ) -> dict[str, float]:
        """把"当前流派"通用兜底 key（*_current）映射到当前流派对应的具体字段名。

        OCR 识别只能拿到"属性攻击/属攻穿透/属攻伤害加成"当前生效流派的合并
        数值，不知道要落到 mingjin_pen/lieshi_pen/... 中的哪一个——这个映射
        要等对话框打开、知道 school_attr 后才能确定，跟手动填写时的占位符
        解析（__attr_pen__ 等，见 _get_resolved_fields）是同一套机制。已有
        的具体字段值（比如 OCR 恰好给出了分流派数据）优先，不覆盖。

        安全性说明（勿删）：initial_values 里可能同时带着全部四门武学的
        具体字段（如 to_role_base_attrs 解析 detail_2 时就是这样——角色
        面板会展示装备词条带来的非本流派属攻残留值，例如裂石流派角色的
        装备恰好有牵丝词条）。这些"其他三门流派"的具体字段名不会被
        _get_resolved_fields 按 school_attr 解析出来，_create_input_section
        自然也就不会为它们创建输入框、get_panel_attrs 也读不到——即使
        _finish_play_style_dialog 之后用 panel(默认 0) - equip(装备提供的
        非本属数值) 反推出一个错误的负数，_save_play_style 只按
        PLAY_STYLE_FIELD_GROUPS 占位符解析出的字段名读取 base_attrs，这个
        负数永远不会被读出来持久化。改这个函数或 _get_resolved_fields 时
        务必保持这条链路：占位符只解析出"当前流派"一个具体字段名，绝不能
        变成同时暴露多门流派的具体字段（无相攻击 min_wuxiang/max_wuxiang
        除外——它是 PLAY_STYLE_FIELD_GROUPS 里唯一非占位符、任意流派都
        始终存在的字段，合法地会被直接填入，参见
        tests/yysls/test_play_style_dialog.py 的回归测试）。
        """
        from ....core.combat.combat_attrs import SCHOOL_ATTR_FIELD_MAP

        values = dict(initial_values or {})
        if not school_attr or school_attr not in SCHOOL_ATTR_FIELD_MAP:
            return values
        attr_map = SCHOOL_ATTR_FIELD_MAP[school_attr]
        fallback_pairs = (
            ("min_attr_current", attr_map.get("min_attr")),
            ("max_attr_current", attr_map.get("max_attr")),
            ("attr_pen_current", attr_map.get("attr_pen")),
            ("attr_bonus_current", attr_map.get("attr_bonus")),
        )
        for generic_key, concrete_field in fallback_pairs:
            if concrete_field and generic_key in values:
                values.setdefault(concrete_field, values[generic_key])
        return values

    def _get_resolved_fields(self) -> list[tuple[str, list[tuple[str, str, str]]]]:
        """解析占位符字段，返回实际字段列表"""
        from ....core.combat.combat_attrs import SCHOOL_ATTR_FIELD_MAP

        if not self._school_attr or self._school_attr not in SCHOOL_ATTR_FIELD_MAP:
            # 无流派属性时，跳过属攻相关字段
            result = []
            for label, fields in PLAY_STYLE_FIELD_GROUPS:
                resolved = [(fn, dl, u) for fn, dl, u in fields if not fn.startswith("__")]
                if resolved:
                    result.append((label, resolved))
            return result

        # 有流派属性时，替换占位符
        attr_map = SCHOOL_ATTR_FIELD_MAP[self._school_attr]
        result = []
        for label, fields in PLAY_STYLE_FIELD_GROUPS:
            resolved = []
            for fn, dl, u in fields:
                if fn == "__attr_pen__":
                    resolved.append((attr_map["attr_pen"], dl, u))
                elif fn == "__attr_bonus__":
                    resolved.append((attr_map["attr_bonus"], dl, u))
                elif fn == "__min_attr__":
                    resolved.append((attr_map["min_attr"], dl, u))
                elif fn == "__max_attr__":
                    resolved.append((attr_map["max_attr"], dl, u))
                else:
                    resolved.append((fn, dl, u))
            result.append((label, resolved))
        return result

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(8)

        title = QLabel(tr("创建基础属性"))
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)

        hint = QLabel(
            tr("填写这套基础配置对应的面板属性。装备专属属性无需填写；"
               "外功穿透和属攻穿透仅填写基础值，不包含装备定音。")
        )
        hint.setStyleSheet(
            "color: palette(mid); font-size: 13px; padding-bottom: 4px;"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form_widget = QWidget()
        form_layout = QVBoxLayout(form_widget)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(6)

        display_names = {
            field_name: display_name
            for field_name, display_name, _unit, _interval in COMBAT_ATTR_FIELDS
        }
        categories: dict[str, list[tuple[str, str, str]]] = {
            "攻击属性": [], "判定属性": [], "增益效果": [], "伤害加成": [],
        }
        judgement_fields = {
            "precision", "crit_rate", "intent_rate",
            "direct_crit", "direct_intent",
        }
        for _group, fields in self._get_resolved_fields():
            for field_name, fallback_label, unit in fields:
                display_label = display_names.get(field_name, fallback_label)
                item = (field_name, display_label, unit)
                if field_name.startswith(("min_", "max_")):
                    categories["攻击属性"].append(item)
                elif field_name in judgement_fields:
                    categories["判定属性"].append(item)
                elif field_name.endswith("_pen"):
                    categories["增益效果"].append(item)
                else:
                    categories["伤害加成"].append(item)

        judgement_by_field = {
            field_name: item
            for item in categories["判定属性"]
            for field_name in (item[0],)
        }
        categories["判定属性"] = [
            judgement_by_field["precision"], ("", "", ""),
            judgement_by_field["crit_rate"], judgement_by_field["direct_crit"],
            judgement_by_field["intent_rate"], judgement_by_field["direct_intent"],
        ]

        for section_title, fields in categories.items():
            subtitle = tr("三率填写白字") if section_title == "判定属性" else ""
            card = self._create_input_section(
                tr(section_title), fields, subtitle=subtitle
            )
            form_layout.addWidget(card)
        layout.addWidget(form_widget)

        name_row = QHBoxLayout()
        name_label = QLabel(tr("基础属性名称"))
        name_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        name_row.addWidget(name_label)
        self._edit_name = QLineEdit()
        self._edit_name.setPlaceholderText(tr("输入基础属性名称"))
        self._edit_name.setMaxLength(20)
        self._edit_name.setMinimumHeight(32)
        name_row.addWidget(self._edit_name)
        layout.addLayout(name_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        apply_dialog_button_box_style(buttons)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("保存"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("取消"))
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _create_input_section(self, title: str,
                              fields: list[tuple[str, str, str]],
                              subtitle: str = "") -> QFrame:
        card = QFrame()
        card.setObjectName("combatAttrCard")
        card.setStyleSheet(_CARD_STYLE)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 7, 12, 8)
        card_layout.setSpacing(4)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        title_label = QLabel(title)
        title_label.setStyleSheet(_TITLE_STYLE)
        header_layout.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setStyleSheet(
                "font-size: 12px; font-weight: normal; color: palette(mid);"
            )
            header_layout.addWidget(subtitle_label)
        header_layout.addStretch()
        card_layout.addLayout(header_layout)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(3)
        for index, (field_name, display_label, unit) in enumerate(fields):
            if not field_name:
                continue
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(8)

            if field_name.endswith("_pen"):
                display_label = tr("{label}（基础值）").format(label=display_label)
            if unit:
                display_label = tr("{label}（{unit}）").format(
                    label=display_label, unit=unit
                )
            label = QLabel(tr(display_label))
            label.setStyleSheet(_NAME_STYLE)
            edit = QLineEdit()
            edit.setAlignment(Qt.AlignmentFlag.AlignRight)
            edit.setFixedWidth(130)
            edit.setFixedHeight(26)
            validator = QDoubleValidator(-999999.0, 999999.0, 2, edit)
            validator.setNotation(QDoubleValidator.Notation.StandardNotation)
            validator.setLocale(QLocale.c())
            edit.setValidator(validator)
            edit.setPlaceholderText("")
            prefill = self._initial_values.get(field_name)
            if prefill is not None:
                edit.setText(_format_prefill(prefill))
            cell_layout.addWidget(label)
            cell_layout.addStretch()
            cell_layout.addWidget(edit)
            grid.addWidget(cell, index // 2, index % 2)
            self._edits[field_name] = edit

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        card_layout.addLayout(grid)
        return card

    def _on_save(self):
        """保存按钮"""
        if not self._edit_name.text().strip():
            QMessageBox.warning(self, tr("名称为空"), tr("基础属性名称不能为空"))
            return
        self.accept()

    def get_panel_attrs(self) -> CombatAttributes:
        """获取用户输入的面板属性"""
        attrs = CombatAttributes()
        for _, fields in self._get_resolved_fields():
            for fn, _, unit in fields:
                edit = self._edits.get(fn)
                if not edit:
                    continue
                text = edit.text().strip()
                value = float(text) if text else 0.0
                if unit == "%":
                    value = value / 100.0
                setattr(attrs, fn, value)
        return attrs

    def get_play_style_name(self) -> str:
        """获取基础属性配置名称。"""
        return self._edit_name.text().strip()
