"""装备调律规则对话框

顶层 QTabWidget：每个调律规则一个 Tab（SchoolRulePanel）。
Tab 栏右上角「＋ 新增规则」可添加规则 Tab、「装备调律验证」
可在改规则后立即验证；规则设置页可删除本规则（移除 Tab）。
底部状态栏显示校验错误（红色）/ 最后保存时间。自动保存，
无手动保存按钮。
"""

import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from src.apps.yysls.evaluator.rules import (
    RuleValidationError, get_tuning_rule_manager,
)

from .school_rule_panel import SchoolRulePanel

# 规则 key 约束（作文件名，与 rules._KEY_RE 一致）
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class _NewRuleDialog(QDialog):
    """新增规则对话框：输入 key（英文标识，作文件名）与名称"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新增调律规则")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("如 my_rule（小写字母/数字/下划线）")
        form.addRow("标识 key：", self._key_edit)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("如 治疗-纯奶")
        form.addRow("规则名称：", self._name_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        key, name = self.rule_key(), self.rule_name()
        if not _KEY_RE.match(key):
            QMessageBox.warning(
                self, "新增调律规则",
                "key 须为小写英文标识（字母开头，可含数字/下划线）")
            return
        if not name:
            QMessageBox.warning(self, "新增调律规则", "规则名称不能为空")
            return
        self.accept()

    def rule_key(self) -> str:
        return self._key_edit.text().strip()

    def rule_name(self) -> str:
        return self._name_edit.text().strip()


class TuningRulesDialog(QDialog):
    """装备调律规则对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("调律规则")
        self.setMinimumSize(900, 700)
        self.resize(1200, 800)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._manager = get_tuning_rule_manager()
        self._tabs = QTabWidget()
        for key, rule in self._manager.get_rules().items():
            self._add_rule_tab(key, rule.name)

        # Tab 栏右上角：新增规则 + 验证入口
        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(0, 0, 0, 0)
        corner_layout.setSpacing(4)
        btn_new = QPushButton("＋ 新增规则")
        btn_new.clicked.connect(self._create_rule)
        corner_layout.addWidget(btn_new)
        btn_judge_test = QPushButton("装备调律验证")
        btn_judge_test.clicked.connect(self._open_judge_test)
        corner_layout.addWidget(btn_judge_test)
        self._tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)
        layout.addWidget(self._tabs)

        self._status_label = QLabel("规则变更即校验，校验通过自动保存并生效")
        layout.addWidget(self._status_label)

    # ── 规则 Tab 增删 ──

    def _add_rule_tab(self, key: str, name: str) -> SchoolRulePanel:
        panel = SchoolRulePanel(key, self._manager, self._set_status,
                                on_delete=self._delete_rule)
        self._tabs.addTab(panel, name)
        return panel

    def _create_rule(self):
        dlg = _NewRuleDialog(self)
        if not dlg.exec():
            return
        key, name = dlg.rule_key(), dlg.rule_name()
        try:
            self._manager.create_rule(key, name)
        except RuleValidationError as e:
            QMessageBox.warning(self, "新增调律规则", str(e))
            return
        panel = self._add_rule_tab(key, name)
        self._tabs.setCurrentWidget(panel)
        self._set_status(f"已新增规则「{name}」", False)

    def _delete_rule(self, key: str):
        try:
            self._manager.delete_rule(key)
        except RuleValidationError as e:
            QMessageBox.warning(self, "删除规则", str(e))
            return
        for i in range(self._tabs.count()):
            panel = self._tabs.widget(i)
            if isinstance(panel, SchoolRulePanel) and panel.rule_key == key:
                self._tabs.removeTab(i)
                panel.deleteLater()
                break
        self._set_status(f"已删除规则 {key}", False)

    # ── 其他 ──

    def _open_judge_test(self):
        from src.apps.yysls.ui.equip_judge_dialog import EquipJudgeTestDialog
        dialog = EquipJudgeTestDialog(parent=self)
        dialog.exec()

    def _set_status(self, text: str, is_error: bool):
        color = "#c62828" if is_error else "#2e7d32"
        self._status_label.setStyleSheet(f"color: {color};")
        self._status_label.setText(text)
