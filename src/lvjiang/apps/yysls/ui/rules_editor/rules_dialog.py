"""装备调律配置对话框

左侧一级导航（基础配置 + 材料配置 + 各规则）+ 右侧内容区（QStackedWidget）：
- 基础配置：品阶门槛与开关设定（BaseConfigPage）；
- 材料配置：大律准石数量检查与狗粮添加规则（MaterialConfigPage）；
- 各规则：单规则编辑面板（RulePanel，内部含 7 项二级导航）；
  双击规则导航项弹窗修改规则名称（配置页项不可改名）。
左侧导航下方为「＋ 新增规则 / 装备调律验证」入口。
底部状态栏显示校验错误（红色）/ 最后保存时间。自动保存，无手动保存按钮。
"""

import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
)

from lvjiang.apps.yysls.evaluator.tuning_rules import (
    RuleValidationError,
    get_tuning_base_manager,
    get_tuning_rule_manager,
)

from .base_config_page import BaseConfigPage
from .material_config_page import MaterialConfigPage
from .rule_panel import RulePanel, add_nav_separator

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
    """装备调律配置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("调律配置")
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
        self._base_manager = get_tuning_base_manager()

        body = QHBoxLayout()

        # ── 左侧一级导航 + 规则入口 ──
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        self._nav = QListWidget()
        self._nav.setFixedWidth(180)
        self._nav.currentRowChanged.connect(self._on_nav_changed)
        self._nav.itemDoubleClicked.connect(self._on_nav_double_clicked)
        left.addWidget(self._nav, 1)
        btn_new = QPushButton("＋ 新增规则")
        btn_new.clicked.connect(self._create_rule)
        left.addWidget(btn_new)
        btn_judge_test = QPushButton("装备调律验证")
        btn_judge_test.clicked.connect(self._open_judge_test)
        left.addWidget(btn_judge_test)
        body.addLayout(left)

        # ── 右侧内容区 ──
        self._stack = QStackedWidget()
        body.addWidget(self._stack, 1)
        layout.addLayout(body, 1)

        self._status_label = QLabel("规则变更即校验，校验通过自动保存并生效")
        layout.addWidget(self._status_label)

        # 一级节点：基础配置 → 材料配置 → 分割线 → 各规则
        # （导航含分割线：行 0/1 = 栈页 0/1，行 ≥3 = 栈页 - 1）
        self._nav.addItem("基础配置")
        self._nav.addItem("材料配置")
        add_nav_separator(self._nav)
        self._stack.addWidget(BaseConfigPage(
            self._base_manager, self._set_status))
        self._stack.addWidget(MaterialConfigPage(
            self._base_manager, self._set_status))
        for key, rule in self._manager.get_rules().items():
            self._add_rule_page(key, rule.name)
        self._nav.setCurrentRow(0)

    # ── 规则页增删 ──

    def _add_rule_page(self, key: str, name: str) -> RulePanel:
        panel = RulePanel(key, self._manager, self._set_status,
                                on_delete=self._delete_rule)
        panel._dialog_rename_cb = self._rename_rule
        self._stack.addWidget(panel)
        self._nav.addItem(name)
        return panel

    def _on_nav_changed(self, row: int):
        item = self._nav.item(row)
        if row < 0 or item is None or not item.flags():
            return  # 分割线项不响应
        self._stack.setCurrentIndex(row if row <= 1 else row - 1)

    def _on_nav_double_clicked(self, item):
        """双击规则导航项 → 弹窗修改规则名称（配置页不可改名）"""
        row = self._nav.row(item)
        if row < 3:  # 基础配置、材料配置与分割线
            return
        panel = self._stack.widget(row - 1)
        if not isinstance(panel, RulePanel):
            return
        old_name = panel.rule_name
        new_name, ok = QInputDialog.getText(
            self, "重命名规则", "规则名称：", text=old_name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return
        panel.set_rule_name(new_name)
        item.setText(new_name)

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
        self._add_rule_page(key, name)
        self._nav.setCurrentRow(self._nav.count() - 1)
        self._set_status(f"已新增规则「{name}」", False)

    def _delete_rule(self, key: str):
        try:
            self._manager.delete_rule(key)
        except RuleValidationError as e:
            QMessageBox.warning(self, "删除规则", str(e))
            return
        for i in range(2, self._stack.count()):
            panel = self._stack.widget(i)
            if isinstance(panel, RulePanel) and panel.rule_key == key:
                self._stack.removeWidget(panel)
                panel.deleteLater()
                self._nav.takeItem(i + 1)  # 导航含分割线，行号 +1
                break
        self._nav.setCurrentRow(0)
        self._set_status(f"已删除规则 {key}", False)

    def _rename_rule(self, old_key: str, new_key: str, new_name: str):
        """更新对应导航项的标题文本（由 panel 在 key/name 变更时回调）"""
        for i in range(2, self._stack.count()):
            panel = self._stack.widget(i)
            if isinstance(panel, RulePanel) and panel.rule_key == new_key:
                self._nav.item(i + 1).setText(new_name)  # 含分割线偏移
                break

    # ── 其他 ──

    def _open_judge_test(self):
        from lvjiang.apps.yysls.ui.equip_judge_dialog import EquipJudgeTestDialog
        dialog = EquipJudgeTestDialog(parent=self)
        dialog.exec()

    def _set_status(self, text: str, is_error: bool | str):
        # is_error: True=红（错误）/ False=绿（正常）/ "warn"=黄（软警告）
        if is_error == "warn":
            color = "#f9a825"
        else:
            color = "#c62828" if is_error else "#2e7d32"
        self._status_label.setStyleSheet(f"color: {color};")
        self._status_label.setText(text)
