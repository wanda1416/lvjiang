"""装备调律规则对话框

顶层 QTabWidget：每个已加载流派一个 Tab（SchoolRulePanel），
Tab 栏右上角「装备调律验证」按钮可在改规则后立即验证；
底部状态栏显示校验错误（红色）/ 最后保存时间。自动保存，
无手动保存按钮。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QTabWidget, QVBoxLayout

from src.apps.yysls.evaluator.rules import get_tuning_rule_manager

from .school_rule_panel import SchoolRulePanel


class TuningRulesDialog(QDialog):
    """装备调律规则对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("装备调律规则")
        self.setMinimumSize(900, 700)
        self.resize(1200, 800)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        manager = get_tuning_rule_manager()
        self._tabs = QTabWidget()
        for key, rule in manager.get_rules().items():
            panel = SchoolRulePanel(key, manager, self._set_status)
            self._tabs.addTab(panel, rule.name)

        # Tab 栏右上角：验证入口（改规则后立即验证，无需回主菜单）
        self._btn_judge_test = QPushButton("装备调律验证")
        self._btn_judge_test.clicked.connect(self._open_judge_test)
        self._tabs.setCornerWidget(
            self._btn_judge_test, Qt.Corner.TopRightCorner)
        layout.addWidget(self._tabs)

        self._status_label = QLabel("规则变更即校验，校验通过自动保存并生效")
        layout.addWidget(self._status_label)

    def _open_judge_test(self):
        from src.apps.yysls.ui.equip_judge_dialog import EquipJudgeTestDialog
        dialog = EquipJudgeTestDialog(parent=self)
        dialog.exec()

    def _set_status(self, text: str, is_error: bool):
        color = "#c62828" if is_error else "#2e7d32"
        self._status_label.setStyleSheet(f"color: {color};")
        self._status_label.setText(text)
