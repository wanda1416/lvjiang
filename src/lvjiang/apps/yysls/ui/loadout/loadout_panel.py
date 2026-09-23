"""Responsive loadout workspace with sidebar/half/full combat modes."""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import QRectF, QSize, QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.button_styles import apply_compact_button_style
from lvjiang.ui.user_toolbar import USER_ACTION_BTN_STYLE, add_user_toolbar_buttons

from .....core.config import load_ui_page_state, update_ui_page_state
from .....i18n import tr
from ...core.loadout import LoadoutRepository
from ..events import get_event_hub
from .character_detail import CharacterDetailTab
from .equip.status_tab import EquipStatusTab
from .plan_create_dialog import PlanCreateDialog
from .plan_manager_dialog import PlanManagerDialog

_METRIC_CARD = (
    "QFrame {background:palette(base);border:1px solid palette(midlight);"
    "border-radius:6px;}"
)

# ui_state 页面归档键：view_mode 与 half_split_sizes 两个字段独立保存，
# update_ui_page_state 的浅合并保证写其一不影响另一。
_UI_PAGE_KEY = "loadout_panel"
_VIEW_MODES = ("sidebar", "half", "full")

# 视图切换图标按钮样式（与刷新按钮视觉一致）
_VIEW_MODE_BTN_STYLE = (
    "QPushButton{border:0;border-radius:4px;color:palette(text);}"
    "QPushButton:hover{background:palette(midlight);color:palette(text);}"
    "QPushButton:checked{background:palette(midlight);border:1px solid palette(highlight);}"
)


def _view_mode_icon(mode: str) -> QIcon:
    """绘制左右面板布局图标，避免依赖字体对 Unicode 符号的支持。"""
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    border = QColor("#6f7b86")
    accent = QColor("#0078d4")
    soft = QColor("#9acbea")
    outer = QRectF(3.5, 5.5, 25, 21)
    painter.setPen(QPen(border, 1.6))
    painter.setBrush(QColor(0, 0, 0, 0))
    painter.drawRoundedRect(outer, 2.5, 2.5)
    if mode == "full":
        divider = 21.5
        painter.fillRect(QRectF(5, 7, 15.5, 18), accent)
    elif mode == "half":
        divider = 16
        painter.fillRect(QRectF(5, 7, 10.2, 18), accent)
        painter.fillRect(QRectF(16.8, 7, 10.2, 18), soft)
    else:
        divider = 10.5
        painter.fillRect(QRectF(11.3, 7, 15.7, 18), accent)
    painter.setPen(QPen(border, 1.4))
    painter.drawLine(int(divider), 6, int(divider), 26)
    painter.end()
    return QIcon(pixmap)

class LoadoutPanel(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._repo = None
        self._refreshing = False
        self._equipment_events_connected = False
        #: 隐藏期间不订阅装备变更，重新显示时才需要补一次全量刷新；
        #: 构造期已经刷新过，首次显示不再重复重建整套装备卡。
        self._stale_while_hidden = False
        self._graduation_result = None
        saved = load_ui_page_state(_UI_PAGE_KEY)
        mode = saved.get("view_mode")
        self._view_mode = mode if mode in _VIEW_MODES else "half"
        half_sizes = saved.get("half_split_sizes")
        self._half_split_sizes = (
            [int(v) for v in half_sizes]
            if isinstance(half_sizes, (list, tuple)) and len(half_sizes) == 2
            else None
        )
        self._build_ui()
        host.user_changed.connect(lambda _name: self.refresh())
        self._event_hub = get_event_hub(host)
        self._event_hub.graduation_updated.connect(self._sync_metrics)
        self._equipment_refresh_timer = QTimer(self)
        self._equipment_refresh_timer.setSingleShot(True)
        self._equipment_refresh_timer.timeout.connect(
            self._refresh_visible_equipment)
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # Row 1: same navigation structure as "用户信息".
        tools = QHBoxLayout()
        add_user_toolbar_buttons(
            tools,
            self._host,
            self.refresh,
            refresh_tooltip=tr("刷新装备数据"),
        )
        # 视图切换图标：4 个汉字宽度间距
        tools.addSpacing(64)
        self._view_buttons: dict[str, QPushButton] = {}
        for mode, tooltip in (
            ("full", tr("战斗属性全屏")),
            ("half", tr("战斗属性半屏")),
            ("sidebar", tr("装备面板全屏")),
        ):
            btn = QPushButton()
            btn.setIcon(_view_mode_icon(mode))
            btn.setIconSize(QSize(26, 26))
            btn.setToolTip(tooltip)
            btn.setAccessibleName(tooltip)
            btn.setFixedSize(36, 36)
            btn.setCheckable(True)
            btn.setStyleSheet(_VIEW_MODE_BTN_STYLE)
            btn.clicked.connect(lambda _checked, value=mode: self._set_view_mode(value))
            self._view_buttons[mode] = btn
            tools.addWidget(btn)
        tools.addStretch()
        for label, callback in (
            (tr("冷却装备"), self._on_cooldown_equipment),
            (tr("承音装备"), lambda: self._equipment._on_chengyin_merge()),
        ):
            button = QPushButton(label)
            button.setToolTip(tr("面向全部用户"))
            button.setStyleSheet(USER_ACTION_BTN_STYLE)
            button.clicked.connect(callback)
            tools.addWidget(button)

        scope_separator = QFrame()
        scope_separator.setFrameShape(QFrame.Shape.NoFrame)
        scope_separator.setFixedSize(12, 26)
        scope_separator.setStyleSheet(
            "border:0;border-left:1px dashed palette(mid);")
        tools.addWidget(scope_separator)

        for label, callback in (
            (tr("最优组合"), lambda: self._equipment._on_optimal_combo()),
            (tr("转律建议"), lambda: self._equipment._on_transmute()),
            (tr("培养建议"), lambda: self._equipment._on_affix_impact()),
            (tr("模拟装备"), lambda: self._equipment._on_mock_create()),
            (tr("导出数据"), lambda: self._equipment._on_export()),
        ):
            button = QPushButton(label)
            button.setStyleSheet(USER_ACTION_BTN_STYLE)
            button.clicked.connect(callback)
            tools.addWidget(button)
        root.addLayout(tools)

        # Row 2: plan management and martial arts.
        plan_row = QHBoxLayout()
        plan_row.addWidget(QLabel(tr("备战方案")))
        self._plans = QComboBox()
        self._plans.currentIndexChanged.connect(self._switch_plan)
        plan_row.addWidget(self._plans, 2)
        for label, callback, variant in (
            (tr("新建"), self._create_plan, "action"),
            (tr("管理"), self._manage_plans, "neutral"),
        ):
            button = QPushButton(label)
            apply_compact_button_style(button, variant=variant)
            button.clicked.connect(callback)
            plan_row.addWidget(button)
        plan_row.addSpacing(16)
        plan_row.addWidget(QLabel(tr("流派")))
        self._school = QLineEdit(tr("无方案"))
        plan_row.addWidget(self._school, 1)
        plan_row.addWidget(QLabel(tr("主武学")))
        self._main_art = QLineEdit("-")
        plan_row.addWidget(self._main_art, 1)
        plan_row.addWidget(QLabel(tr("副武学")))
        self._sub_art = QLineEdit("-")
        plan_row.addWidget(self._sub_art, 1)
        plan_row.addWidget(QLabel(tr("玩法")))
        self._playstyle = QLineEdit("-")
        plan_row.addWidget(self._playstyle, 1)
        for field in (self._school, self._main_art, self._sub_art,
                      self._playstyle):
            field.setReadOnly(True)
            field.setEnabled(False)
            field.setMinimumWidth(80)
        # 仅移动“方案/弓玦”的显示位置；控件仍由战斗属性页持有，
        # 其计算、选择与持久化路径均不因所在行改变。
        self._plan_row = plan_row
        root.addLayout(plan_row)

        # Row 3: always-visible assumptions + public metrics.
        # Keep the whole row at 2:1:1 — assumptions occupy the left half,
        # DPS and graduation rate split the right half equally.
        metrics = QHBoxLayout()
        self._metrics_layout = metrics
        self._assumption_card = QFrame()
        self._assumption_card.setStyleSheet(_METRIC_CARD)
        self._assumption_layout = QHBoxLayout(self._assumption_card)
        self._assumption_layout.setContentsMargins(16, 8, 16, 8)
        self._assumption_layout.setSpacing(16)
        metrics.addWidget(self._assumption_card, 2)
        self._metric_dps = self._metric(
            metrics, tr("DPS"), yellow=False, stretch=1)
        self._metric_rate = self._metric(
            metrics, tr("毕业率"), yellow=True, stretch=1)
        root.addLayout(metrics)

        self._splitter = QSplitter()
        self._left_shell = self._make_combat_shell()
        self._attach_plan_controls()
        self._attach_assumption_controls()
        self._right_shell = self._make_equipment_shell()
        # 装备页需要战斗属性页的假设副本：由面板显式注入，不靠 findChildren
        self._equipment.bind_combat_tab(self._character._combat_attrs_tab)
        self._splitter.addWidget(self._left_shell)
        self._splitter.addWidget(self._right_shell)
        self._splitter.setChildrenCollapsible(False)
        # 强制等分：忽略 sizeHint 差异，半屏模式下两 shell 宽度相等
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.splitterMoved.connect(self._on_splitter_moved)
        root.addWidget(self._splitter, 1)
        self._set_view_mode(self._view_mode)

    def _on_cooldown_equipment(self) -> None:
        """汇总所有用户的冷却装备，并在属性窗口中提供维护入口。"""
        from .equip.cooldown_manager_dialog import CooldownEquipmentDialog

        dialog = CooldownEquipmentDialog(
            self._host.user_manager.list_users(),
            self._equipment._display_params,
            self,
        )
        dialog.exec()
        if dialog.changed:
            self.refresh()

    def _metric(
        self, parent: QHBoxLayout, name: str, *, yellow: bool, stretch: int,
    ) -> QLabel:
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
        parent.addWidget(card, stretch)
        return value

    def _attach_assumption_controls(self) -> None:
        """Move combat assumptions into the always-visible summary row."""
        combat = self._character._combat_attrs_tab
        controls: tuple[QCheckBox, ...] = (
            combat._chk_full_level,
            combat._chk_full_chengyin,
            combat._chk_full_dingyin,
            combat._chk_simulate_transmute,
        )
        for control in controls:
            self._assumption_layout.addWidget(control)
        self._assumption_layout.addStretch()

    def _attach_plan_controls(self) -> None:
        combat = self._character._combat_attrs_tab
        self._plan_row.addSpacing(8)
        self._plan_row.addWidget(combat._plan_scheme_field, 1)
        self._plan_row.addWidget(combat._plan_gongjue_field, 1)

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
        for key, button in self._view_buttons.items():
            button.setChecked(key == mode)
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
        self._subscribe_equipment_updates()
        # 隐藏期间不订阅、也不积压消息；再次进入页面时直接读取最新快照。
        if self._stale_while_hidden:
            self._schedule_equipment_refresh()
        self._apply_split_sizes()

    def hideEvent(self, event):
        # addTab 会对从未显示过的页面也发一次 hide：那时还没订阅过变更，
        # 构造期的快照仍然是最新的，不必标脏。
        if self._equipment_events_connected:
            self._stale_while_hidden = True
        self._unsubscribe_equipment_updates()
        self._equipment_refresh_timer.stop()
        super().hideEvent(event)

    def _subscribe_equipment_updates(self) -> None:
        """仅页面可见期间订阅逐件扫描结果。"""
        if self._equipment_events_connected:
            return
        self._event_hub.equipment_changed.connect(
            self._schedule_equipment_refresh)
        self._equipment_events_connected = True

    def _unsubscribe_equipment_updates(self) -> None:
        if not self._equipment_events_connected:
            return
        self._event_hub.equipment_changed.disconnect(
            self._schedule_equipment_refresh)
        self._equipment_events_connected = False

    def _schedule_equipment_refresh(self) -> None:
        """合并同一事件循环内的变更，不让刷新重入。"""
        if self.isVisible() and not self._equipment_refresh_timer.isActive():
            self._equipment_refresh_timer.start(0)

    def _refresh_visible_equipment(self) -> None:
        if not self.isVisible():
            return
        if self._refreshing:
            self._equipment_refresh_timer.start(0)
            return
        self._on_equipment_changed()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 半屏模式重施的是已记录的分割宽度（拖动时 splitterMoved 实时
        # 更新），不会覆盖用户手动调整的结果
        self._apply_split_sizes()

    def _current_repo(self):
        username = self._host.active_user_name()
        return LoadoutRepository(username) if username else None

    def refresh(self):
        # refresh 的语义是重新读取装备数据。必须在任何下拉框联动触发
        # _refresh_display 之前废弃旧穿戴快照，否则“刷新”仍会复用旧装备。
        combat = self._character._combat_attrs_tab
        combat.invalidate_equipment_snapshot()
        self._repo = self._current_repo()
        if self._repo is None:
            return
        username = self._host.active_user_name()
        # 本轮刷新只加载一次仓储快照，装备页与战斗属性页共用
        from ...core.combat.equipment import EquipmentInventory
        try:
            inventory = EquipmentInventory(username)
        except Exception as e:  # noqa: BLE001 - 与子页各自加载时的容错一致
            logger.error(f"加载装备失败: {e}")
            inventory = None
        state = inventory.state if inventory is not None else self._repo.load()
        self._refreshing = True
        self._plans.clear()
        for pid in state.ordered_plan_ids():
            plan = state.plans[pid]
            self._plans.addItem(plan.name, pid)
        self._plans.setCurrentIndex(self._plans.findData(state.active_plan_id))
        from ...config import get_game_config
        game_config = get_game_config()
        schools = game_config.get_schools()
        self._main_art.setText(state.active_plan.main_martial_art or "-")
        self._sub_art.setText(state.active_plan.sub_martial_art or "-")
        self._playstyle.setText(state.active_plan.playstyle or "-")
        school = state.active_school(schools)
        self._school.setText(school or tr("自定义"))
        for field in (self._school, self._main_art, self._sub_art,
                      self._playstyle):
            field.setToolTip(field.text())
        self._refreshing = False
        # 下游消费者（装备页/战斗属性页）已显式驱动，无需再 emit
        # equipment_changed：emit 会导致信号订阅者重复全量刷新
        if inventory is not None:
            self._equipment.refresh_from(inventory)
            combat.set_equipment_snapshot(username, inventory.equipped)
        else:
            self._equipment._refresh_all()
        combat._restore_selection(
            state.active_plan, school,
            prefs=self._repo.get_combat_prefs())
        combat._refresh_display()
        self._stale_while_hidden = False

    def _on_equipment_changed(self):
        """装备变更（UI 操作或工作流写入）：刷新方案展示。

        指标（DPS/毕业率）经异步毕业率计算完成后的
        graduation_updated 信号同步，无需在此处理。
        """
        if self._refreshing:
            return
        self.refresh()

    def _sync_metrics(self, result):
        """接收毕业率计算结果并更新 DPS / 毕业率展示。"""
        self._graduation_result = result
        if result is not None:
            dps_text = f"{result.dps:,.0f}"
            rate_text = f"{result.graduation_rate * 100:.2f}%"
            tooltip = (
                f"{tr('总伤害')}: {result.total_damage:,.0f}\n"
                f"{tr('基准DPS')}: {result.baseline_dps:,.2f}\n"
                f"{tr('战斗时间')}: {result.combat_time}s"
            )
        else:
            dps_text = "--"
            rate_text = "--"
            tooltip = ""
        self._metric_dps.setText(dps_text)
        self._metric_rate.setText(rate_text)
        self._metric_dps.setToolTip(tooltip)
        self._metric_rate.setToolTip(tooltip)

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
        from ...config import get_game_config
        game_config = get_game_config()
        dialog = PlanCreateDialog(game_config, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._repo.create_plan(
            dialog.plan_name, dialog.main_art, dialog.sub_art,
            playstyle=dialog.playstyle, combat_type=dialog.combat_type)
        self.refresh()

    def _manage_plans(self) -> None:
        dialog = PlanManagerDialog(
            self._host.user_manager.list_users(),
            self._host.active_user_name(),
            self._repo.users_dir if self._repo else None,
            self)
        dialog.exec()
        if self._host.active_user_name() in dialog.changed_users:
            self.refresh()
