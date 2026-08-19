"""Responsive loadout workspace with sidebar/half/full combat modes."""
from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ....core.config import load_ui_page_state, update_ui_page_state
from ....i18n import tr
from ..core.loadout import LoadoutRepository, resolve_school
from .character_detail_tab import CharacterDetailTab
from .equip_status_tab import EquipStatusTab
from .profile.tab import add_user_nav_buttons

_METRIC_CARD = (
    "QFrame {background:#f8f9fa;border:1px solid #dee2e6;border-radius:6px;}"
)

# ui_state 页面归档键：view_mode 与 half_split_sizes 两个字段独立保存，
# update_ui_page_state 的浅合并保证写其一不影响另一。
_UI_PAGE_KEY = "loadout_panel"
_VIEW_MODES = ("sidebar", "half", "full")

# 视图切换图标按钮样式（与刷新按钮视觉一致）
_VIEW_MODE_BTN_STYLE = (
    "QPushButton{border:0;border-radius:4px;font-size:16px;color:#546E7A;}"
    "QPushButton:hover{background:#E8EEF2;color:#263238;}"
)


class _PlanCreateDialog(QDialog):
    """新建方案对话框：必须同时绑定主武学与副武学，且组合需匹配流派。"""

    def __init__(self, schools: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("新建方案"))
        self._schools = schools
        form = QFormLayout(self)
        self._edit_name = QLineEdit()
        form.addRow(tr("方案名称:"), self._edit_name)
        main_arts = list(dict.fromkeys(
            (cfg.get("main") or {}).get("martial_art", "")
            for cfg in schools.values()))
        sub_arts = list(dict.fromkeys(
            (cfg.get("sub") or {}).get("martial_art", "")
            for cfg in schools.values()))
        self._combo_main = QComboBox()
        self._combo_main.addItems([a for a in main_arts if a])
        form.addRow(tr("主武学:"), self._combo_main)
        self._combo_sub = QComboBox()
        self._combo_sub.addItems([a for a in sub_arts if a])
        form.addRow(tr("副武学:"), self._combo_sub)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _validate_and_accept(self):
        main_art = self._combo_main.currentText()
        sub_art = self._combo_sub.currentText()
        if not main_art or not sub_art:
            QMessageBox.warning(
                self, tr("新建方案"), tr("必须同时绑定主武学和副武学"))
            return
        school = resolve_school(main_art, sub_art, self._schools)
        if school is None:
            QMessageBox.warning(
                self, tr("新建方案"),
                tr("所选主副武学无法匹配任何流派，请重新选择"))
            return
        self.accept()

    @property
    def plan_name(self) -> str:
        return self._edit_name.text().strip()

    @property
    def main_art(self) -> str:
        return self._combo_main.currentText()

    @property
    def sub_art(self) -> str:
        return self._combo_sub.currentText()


class LoadoutPanel(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._repo = None
        self._refreshing = False
        saved = load_ui_page_state(_UI_PAGE_KEY)
        mode = saved.get("view_mode")
        self._view_mode = mode if mode in _VIEW_MODES else "sidebar"
        half_sizes = saved.get("half_split_sizes")
        self._half_split_sizes = (
            [int(v) for v in half_sizes]
            if isinstance(half_sizes, (list, tuple)) and len(half_sizes) == 2
            else None
        )
        self._build_ui()
        host.user_changed.connect(lambda _name: self.refresh())
        host.graduation_updated.connect(self._sync_metrics)
        # 装备变更（UI 操作与工作流写入）：刷新方案展示并同步指标
        host.equipment_changed.connect(self._on_equipment_changed)
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # Row 1: same navigation structure as "其他信息".
        tools = QHBoxLayout()
        refresh = QPushButton(tr("刷新"))
        refresh.setFixedWidth(60)
        refresh.clicked.connect(self.refresh)
        tools.addWidget(refresh)
        add_user_nav_buttons(tools, self._host)
        # 视图切换图标：4 个汉字宽度间距
        tools.addSpacing(64)
        for symbol, tooltip, callback in (
            ("▣", tr("战斗属性全屏"), lambda: self._set_view_mode("full")),
            ("◧", tr("战斗属性半屏"), lambda: self._set_view_mode("half")),
            ("▣", tr("装备面板全屏"), lambda: self._set_view_mode("sidebar")),
        ):
            btn = QPushButton(symbol)
            btn.setToolTip(tooltip)
            btn.setFixedSize(36, 36)
            btn.setStyleSheet(_VIEW_MODE_BTN_STYLE)
            btn.clicked.connect(callback)
            tools.addWidget(btn)
        tools.addStretch()
        for label, callback in (
            (tr("计算最优组合"), lambda: self._equipment._on_optimal_combo()),
            (tr("创建模拟装备"), lambda: self._equipment._on_mock_create()),
            (tr("清空真实装备"), lambda: self._equipment._on_clear_real()),
            (tr("导出数据"), lambda: self._equipment._on_export()),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            tools.addWidget(button)
        root.addLayout(tools)

        # Row 2: plan management and martial arts.
        plan_row = QHBoxLayout()
        plan_row.addWidget(QLabel(tr("备战方案")))
        self._plans = QComboBox()
        self._plans.currentIndexChanged.connect(self._switch_plan)
        plan_row.addWidget(self._plans, 2)
        for label, callback in (
            (tr("新建"), self._create_plan),
            (tr("重命名"), self._rename_plan),
            (tr("删除"), self._delete_plan),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            plan_row.addWidget(button)
        plan_row.addSpacing(16)
        plan_row.addWidget(QLabel(tr("主武学")))
        self._main_art = QComboBox()
        plan_row.addWidget(self._main_art, 1)
        plan_row.addWidget(QLabel(tr("副武学")))
        self._sub_art = QComboBox()
        plan_row.addWidget(self._sub_art, 1)
        self._school = QLabel(tr("无方案"))
        self._school.setMinimumWidth(80)
        plan_row.addWidget(self._school)
        self._main_art.currentTextChanged.connect(self._configure_arts)
        self._sub_art.currentTextChanged.connect(self._configure_arts)
        root.addLayout(plan_row)

        # Row 3: symmetrical public metrics.
        metrics = QHBoxLayout()
        self._metric_dps = self._metric(metrics, tr("DPS"), yellow=False)
        self._metric_rate = self._metric(metrics, tr("毕业率"), yellow=True)
        root.addLayout(metrics)

        self._splitter = QSplitter()
        self._left_shell = self._make_combat_shell()
        self._right_shell = self._make_equipment_shell()
        self._splitter.addWidget(self._left_shell)
        self._splitter.addWidget(self._right_shell)
        self._splitter.setChildrenCollapsible(False)
        # 强制等分：忽略 sizeHint 差异，半屏模式下两 shell 宽度相等
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.splitterMoved.connect(self._on_splitter_moved)
        root.addWidget(self._splitter, 1)
        self._set_view_mode(self._view_mode)

    def _metric(self, parent: QHBoxLayout, name: str, *, yellow: bool) -> QLabel:
        card = QFrame()
        card.setStyleSheet(_METRIC_CARD)
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 8, 16, 8)
        label = QLabel(name)
        value = QLabel("--")
        value.setStyleSheet(
            "font-size:16px;font-weight:700;color:#F57C00;"
            if yellow else "font-size:16px;font-weight:700;color:#263238;"
        )
        row.addWidget(label)
        row.addStretch()
        row.addWidget(value)
        parent.addWidget(card, 1)
        return value

    def _make_combat_shell(self) -> QWidget:
        shell = QWidget()
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._character = CharacterDetailTab(self._host, shell)
        layout.addWidget(self._character, 1)
        return shell

    def _make_equipment_shell(self) -> QWidget:
        shell = QWidget()
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._equipment = EquipStatusTab(self._host, shell)
        self._equipment.set_embedded_mode(True)
        layout.addWidget(self._equipment, 1)
        return shell

    def _set_view_mode(self, mode: str) -> None:
        if mode != self._view_mode:
            update_ui_page_state(_UI_PAGE_KEY, {"view_mode": mode})
        self._view_mode = mode
        combat = self._character._combat_attrs_tab
        combat.set_embedded_mode(mode)
        self._left_shell.setVisible(mode != "sidebar")
        self._right_shell.setVisible(mode != "full")
        self._left_shell.setMaximumWidth(16777215)
        self._right_shell.setMaximumWidth(16777215)
        # 半屏模式左侧最低宽度：20 个汉字（280px）
        self._left_shell.setMinimumWidth(280 if mode == "half" else 0)
        # 强制忽略 sizeHint，让 splitter 按 setSizes 分配空间
        self._left_shell.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self._right_shell.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self._apply_split_sizes()

    def _apply_split_sizes(self) -> None:
        """按当前模式分配 splitter 尺寸。

        若在布局完成前调用 setSizes，Qt 只会把它当作相对比例提示，
        实际布局时按各子部件 sizeHint 重新分配 —— 左右内容 sizeHint
        差异会导致半屏初始分割不对称。因此延迟到布局周期结束后，
        用真实宽度重新施加一次。
        """
        QTimer.singleShot(0, self._do_apply_split_sizes)

    def _do_apply_split_sizes(self) -> None:
        if not (self.isVisible() and self._splitter.width() > 0):
            return
        width = self._splitter.width()
        if self._view_mode == "sidebar":
            self._splitter.setSizes([0, width])
        elif self._view_mode == "half":
            # 优先恢复上次记录的分割宽度（超出当前宽度时 Qt 按比例缩放）
            if self._half_split_sizes:
                self._splitter.setSizes(self._half_split_sizes)
            else:
                self._splitter.setSizes([width // 2, width // 2])
        else:
            self._splitter.setSizes([width, 0])

    def _on_splitter_moved(self, *_args) -> None:
        """用户拖动分割条时记录半屏分割宽度（仅半屏模式）。"""
        if self._view_mode != "half":
            return
        sizes = [int(v) for v in self._splitter.sizes()]
        self._half_split_sizes = sizes
        update_ui_page_state(_UI_PAGE_KEY, {"half_split_sizes": sizes})

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_split_sizes()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 半屏模式重施的是已记录的分割宽度（拖动时 splitterMoved 实时
        # 更新），不会覆盖用户手动调整的结果
        self._apply_split_sizes()

    def _current_repo(self):
        username = self._host.active_user_name()
        return LoadoutRepository(username) if username else None

    def refresh(self):
        self._repo = self._current_repo()
        if self._repo is None:
            return
        state = self._repo.load()
        self._refreshing = True
        self._plans.clear()
        for pid, plan in state.plans.items():
            self._plans.addItem(plan.name, pid)
        self._plans.setCurrentIndex(self._plans.findData(state.active_plan_id))
        from ..config import get_game_config
        schools = get_game_config().get_schools()
        main_arts = [""] + list(dict.fromkeys(
            (cfg.get("main") or {}).get("martial_art", "") for cfg in schools.values()))
        sub_arts = [""] + list(dict.fromkeys(
            (cfg.get("sub") or {}).get("martial_art", "") for cfg in schools.values()))
        self._set_combo(self._main_art, main_arts, state.active_plan.main_martial_art)
        self._set_combo(self._sub_art, sub_arts, state.active_plan.sub_martial_art)
        school = resolve_school(
            state.active_plan.main_martial_art,
            state.active_plan.sub_martial_art,
            schools,
        )
        self._school.setText(school or tr("无方案"))
        self._refreshing = False
        # 下游消费者（装备页/战斗属性页）已显式驱动，无需再 emit
        # equipment_changed：emit 会导致信号订阅者重复全量刷新
        self._equipment._refresh_all()
        combat = self._character._combat_attrs_tab
        school_index = combat._combo_school.findText(school or "")
        combat._combo_school.setCurrentIndex(max(0, school_index))
        combat._refresh_play_styles()
        combat._refresh_schemes()
        combat._refresh_display()

    def _on_equipment_changed(self):
        """装备变更（UI 操作或工作流写入）：刷新方案展示。

        指标（DPS/毕业率）经异步毕业率计算完成后的
        graduation_updated 信号同步，无需在此处理。
        """
        if self._refreshing:
            return
        self.refresh()

    def _sync_metrics(self):
        combat = self._character._combat_attrs_tab
        self._metric_dps.setText(combat._dps_value.text())
        self._metric_rate.setText(combat._graduation_value.text())
        self._metric_dps.setToolTip(combat._dps_value.toolTip())
        self._metric_rate.setToolTip(combat._graduation_value.toolTip())

    @staticmethod
    def _set_combo(combo, values, selected):
        combo.clear()
        combo.addItems(list(values))
        combo.setCurrentText(selected)

    def _switch_plan(self, _index):
        if self._refreshing or not self._repo:
            return
        pid = self._plans.currentData()
        if pid:
            self._repo.switch_plan(pid)
            self.refresh()

    def _create_plan(self):
        if not self._repo:
            return
        from ..config import get_game_config
        dialog = _PlanCreateDialog(get_game_config().get_schools(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._repo.create_plan(dialog.plan_name, dialog.main_art, dialog.sub_art)
        self.refresh()

    def _rename_plan(self):
        if not self._repo:
            return
        state = self._repo.load()
        name, ok = QInputDialog.getText(
            self, tr("重命名方案"), tr("方案名称:"), text=state.active_plan.name)
        if ok:
            self._repo.configure_plan(state.active_plan_id, name=name)
            self.refresh()

    def _delete_plan(self):
        if not self._repo:
            return
        state = self._repo.load()
        if QMessageBox.question(
            self, tr("删除方案"), tr("确定删除当前方案吗？"),
        ) == QMessageBox.StandardButton.Yes:
            try:
                self._repo.delete_plan(state.active_plan_id)
                self.refresh()
            except ValueError as exc:
                QMessageBox.warning(self, tr("删除失败"), str(exc))

    def _configure_arts(self):
        if self._refreshing or not self._repo:
            return
        main_art = self._main_art.currentText()
        sub_art = self._sub_art.currentText()
        if not main_art or not sub_art:
            # 方案必须始终绑定双武学，清空选择直接回退
            QMessageBox.warning(
                self, tr("武学绑定"), tr("方案必须同时绑定主武学和副武学"))
            self.refresh()
            return
        state = self._repo.load()
        self._repo.configure_plan(
            state.active_plan_id,
            main_martial_art=main_art,
            sub_martial_art=sub_art,
        )
        self.refresh()
