"""装备调律配置对话框

左侧一级导航（基础规则组 + 状态机三行为点 ｜ 流派规则 + 各规则）
+ 右侧内容区（QStackedWidget）：
- 基础规则：规则组切换/新增/复制/删除 + 等级/调律门槛
  （BaseRuleGroupPage），切换后三个行为页同步对准该组；
- 扫描处理：进调律前的进入门槛与处置表（ScanBehaviorPage）；
- 材料处理：每轮调律开始前的律准石检查与狗粮规则（MaterialConfigPage）；
- 结束处理：每轮调律结束后的行为表与重置设置（TuneBehaviorPage）；
- 流派规则：品阶门槛与开关设定（PlaystyleConfigPage，全局不随组切换）；
- 各规则：单规则编辑面板（RulePanel，内部含 7 项二级导航）；
  双击规则导航项弹窗修改规则名称（配置页项不可改名）。
左侧导航下方为「＋ 新增规则 / 装备调律验证」入口。
底部状态栏显示校验错误（红色）/ 最后保存时间。自动保存，无手动保存按钮。
"""

import re

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush
from PyQt6.QtWidgets import (
    QComboBox,
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
    QWidget,
)

from lvjiang.apps.yysls.evaluator.tuning_rules import (
    RuleValidationError,
    get_tune_config,
    get_tune_config_manager,
    get_tuning_group_manager,
    get_tuning_rule_manager,
)
from lvjiang.core.config.wf_configs import get_wf_config

from .....i18n import tr
from .base_rule_page import BaseRuleGroupPage
from .behavior_pages import ScanBehaviorPage, TuneBehaviorPage
from .material_config_page import MaterialConfigPage
from .playstyle_config_page import PlaystyleConfigPage
from .rule_panel import RulePanel, add_nav_separator

# 规则 key 约束（作文件名，与 rules._KEY_RE 一致）
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class _NewRuleDialog(QDialog):
    """新增规则对话框：输入 key（英文标识，作文件名）与名称"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("新增调律规则"))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText(tr("如 my_rule（小写字母/数字/下划线）"))
        form.addRow(tr("标识 key："), self._key_edit)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText(tr("如 治疗-纯奶"))
        form.addRow(tr("规则名称："), self._name_edit)
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
                self, tr("新增调律规则"),
                tr("key 须为小写英文标识（字母开头，可含数字/下划线）"))
            return
        if not name:
            QMessageBox.warning(self, tr("新增调律规则"), tr("规则名称不能为空"))
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
        self.setWindowTitle(tr("调律配置"))
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
        self._config_manager = get_tune_config_manager()
        self._group_manager = get_tuning_group_manager()
        # 初始规则组：session 持久值，组不存在取第一个可用
        group_key = get_wf_config("auto_tuning").get("base_group", "")
        if self._group_manager.get_group(group_key) is None:
            groups = self._group_manager.get_groups()
            group_key = next(iter(groups), "")

        body = QHBoxLayout()

        # ── 左侧一级导航 + 规则入口 ──
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        self._nav = QListWidget()
        self._nav.setFixedWidth(180)
        self._nav.currentRowChanged.connect(self._on_nav_changed)
        self._nav.itemDoubleClicked.connect(self._on_nav_double_clicked)
        left.addWidget(self._nav, 1)
        btn_new = QPushButton(tr("＋ 新增规则"))
        btn_new.setAutoDefault(False)
        btn_new.clicked.connect(self._create_rule)
        left.addWidget(btn_new)
        btn_judge_test = QPushButton(tr("装备调律验证"))
        btn_judge_test.setAutoDefault(False)
        btn_judge_test.clicked.connect(self._open_judge_test)
        left.addWidget(btn_judge_test)
        body.addLayout(left)

        # ── 右侧内容区 ──
        self._stack = QStackedWidget()
        body.addWidget(self._stack, 1)
        layout.addLayout(body, 1)

        self._status_label = QLabel(tr("规则变更即校验，校验通过自动保存并生效"))
        layout.addWidget(self._status_label)

        # 一级节点：基础规则 → 扫描处理 → 材料处理 → 结束处理 →
        # 分割线 → 流派规则 → 各规则（导航含分割线：行 0-3 = 栈页 0-3，
        # 行 ≥5 = 栈页 - 1）
        self._nav.addItem(tr("基础规则"))
        self._nav.addItem(tr("扫描处理"))
        self._nav.addItem(tr("材料处理"))
        self._nav.addItem(tr("结束处理"))
        add_nav_separator(self._nav)
        self._nav.addItem(tr("流派规则"))
        self._base_page = BaseRuleGroupPage(
            self._group_manager, group_key, self._set_status)
        self._stack.addWidget(self._base_page)
        self._scan_page = ScanBehaviorPage(
            self._group_manager, group_key, self._set_status)
        self._stack.addWidget(self._scan_page)
        self._material_page = MaterialConfigPage(
            self._group_manager, group_key, self._set_status)
        self._stack.addWidget(self._material_page)
        self._tune_page = TuneBehaviorPage(
            self._group_manager, group_key, self._set_status)
        self._stack.addWidget(self._tune_page)
        self._stack.addWidget(PlaystyleConfigPage(
            self._config_manager, self._set_status))
        # 规则组切换后三个行为页同步重载
        self._base_page.set_switch_callback(self._on_group_switched)
        # 扫描处理页保存后通知基础规则页刷新展示（门槛值同步）
        self._scan_page.set_save_callback(self._base_page.refresh)
        # 三个行为页顶部插入「当前规则」下拉（快速切换基础规则组）
        self._group_dropdowns: list[QComboBox] = []
        self._syncing_group = False
        for page in (self._scan_page, self._material_page, self._tune_page):
            self._insert_group_dropdown(page)
        self._sync_group_dropdowns(group_key)
        for key, rule in self._manager.get_rules().items():
            self._add_rule_page(key, rule.name)
        # 加载全部规则（含禁用），禁用规则导航文字置灰
        self._disabled_rule_keys: set[str] = set()
        try:
            tuning_rules = get_tune_config().tuning_rules
            self._disabled_rule_keys = {
                k for k, v in tuning_rules.items() if not v}
        except Exception:
            pass
        for key, name in self._manager.get_all_rule_keys_and_names():
            if key not in self._manager.get_rules():
                panel = self._add_rule_page(key, name)
                self._apply_disabled_nav_style(panel, True)
        self._nav.setCurrentRow(0)

    # ── 规则页增删 ──

    def _add_rule_page(self, key: str, name: str) -> RulePanel:
        panel = RulePanel(key, self._manager, self._set_status,
                                on_delete=self._delete_rule)
        panel._dialog_rename_cb = self._rename_rule  # type: ignore[assignment]
        self._stack.addWidget(panel)
        self._nav.addItem(name)
        # 连接启用状态回调，更新导航灰色样式
        settings_page = panel._settings_page
        orig_cb = settings_page._on_enable_changed

        def _on_enable(enabled: bool):
            if orig_cb is not None:
                orig_cb(enabled)
            self._apply_disabled_nav_style(panel, not enabled)

        settings_page._on_enable_changed = _on_enable  # type: ignore[assignment]
        return panel

    def _apply_disabled_nav_style(self, panel: RulePanel, disabled: bool):
        """更新规则导航项的灰色样式（禁用=灰色，启用=正常）"""
        key = panel.rule_key
        if disabled:
            self._disabled_rule_keys.add(key)
        else:
            self._disabled_rule_keys.discard(key)
        for i in range(6, self._nav.count()):
            if (self._stack.widget(i - 1) is panel):
                item = self._nav.item(i)
                if disabled:
                    item.setForeground(QBrush(Qt.GlobalColor.gray))
                else:
                    item.setData(
                        Qt.ItemDataRole.ForegroundRole, None)
                break

    def _on_nav_changed(self, row: int):
        item = self._nav.item(row)
        if row < 0 or item is None or not item.flags():
            return  # 分割线项不响应
        self._stack.setCurrentIndex(row if row <= 3 else row - 1)

    def _on_group_switched(self, group_key: str):
        """基础规则组切换 → 三个行为页对准新组并重载"""
        self._syncing_group = True
        try:
            self._scan_page.set_group(group_key)
            self._material_page.set_group(group_key)
            self._tune_page.set_group(group_key)
            self._sync_group_dropdowns(group_key)
        finally:
            self._syncing_group = False

    def _insert_group_dropdown(self, page: QWidget):
        """在行为页布局顶部插入「当前规则」下拉"""
        layout = page.layout()
        if layout is None:
            return
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(tr("当前规则：")))
        combo = QComboBox()
        combo.currentIndexChanged.connect(
            lambda idx: self._on_group_dropdown_changed(idx, combo))
        row.addWidget(combo)
        row.addStretch()
        layout.insertLayout(0, row)
        self._group_dropdowns.append(combo)

    def _sync_group_dropdowns(self, group_key: str):
        """同步所有下拉框选中状态（不触发信号）"""
        for combo in self._group_dropdowns:
            combo.blockSignals(True)
            combo.clear()
            for key, group in self._group_manager.get_groups().items():
                combo.addItem(group.name, key)
            idx = combo.findData(group_key)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def _on_group_dropdown_changed(self, idx: int, combo: QComboBox):
        """下拉变更 → 切换基础规则组"""
        if self._syncing_group or idx < 0:
            return
        key = combo.currentData()
        if key and key != self._base_page._group_key:
            # 先同步基础规则页的 combo 到新 key，再触发切换
            base_combo = self._base_page._combo
            base_idx = base_combo.findData(key)
            if base_idx >= 0:
                base_combo.blockSignals(True)
                base_combo.setCurrentIndex(base_idx)
                base_combo.blockSignals(False)
                # 委托基础规则页处理（持久化 + 刷新 + 回调行为页）
                self._base_page._on_combo_changed(base_idx)

    def _on_nav_double_clicked(self, item):
        """双击规则导航项 → 弹窗修改规则名称（配置页不可改名）"""
        row = self._nav.row(item)
        if row < 6:  # 四张基础规则/行为页 + 分割线 + 流派规则页
            return
        panel = self._stack.widget(row - 1)
        if not isinstance(panel, RulePanel):
            return
        old_name = panel.rule_name
        new_name, ok = QInputDialog.getText(
            self, tr("重命名规则"), tr("规则名称："), text=old_name)
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
            QMessageBox.warning(self, tr("新增调律规则"), str(e))
            return
        self._add_rule_page(key, name)
        self._nav.setCurrentRow(self._nav.count() - 1)
        self._set_status(tr("已新增规则「{name}」").format(name=name), False)

    def _delete_rule(self, key: str):
        try:
            self._manager.delete_rule(key)
        except RuleValidationError as e:
            QMessageBox.warning(self, tr("删除规则"), str(e))
            return
        for i in range(5, self._stack.count()):
            panel = self._stack.widget(i)
            if isinstance(panel, RulePanel) and panel.rule_key == key:
                self._stack.removeWidget(panel)
                panel.deleteLater()
                self._nav.takeItem(i + 1)  # 导航含分割线，行号 +1
                break
        self._nav.setCurrentRow(0)
        self._set_status(tr("已删除规则 {key}").format(key=key), False)

    def _rename_rule(self, old_key: str, new_key: str, new_name: str):
        """更新对应导航项的标题文本（由 panel 在 key/name 变更时回调）"""
        for i in range(5, self._stack.count()):
            panel = self._stack.widget(i)
            if isinstance(panel, RulePanel) and panel.rule_key == new_key:
                item = self._nav.item(i + 1)  # 含分割线偏移
                item.setText(new_name)
                # 重命名后保持禁用灰色样式
                if new_key in self._disabled_rule_keys:
                    item.setForeground(QBrush(Qt.GlobalColor.gray))
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
