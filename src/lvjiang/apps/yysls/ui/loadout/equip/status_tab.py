"""装备状态页：装备槽展示、筛选、编辑与分析入口。"""

from __future__ import annotations

import copy

from loguru import logger
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.users.user_toolbar import REFRESH_BTN_STYLE as _REFRESH_BTN_STYLE
from lvjiang.ui.users.user_toolbar import add_user_nav_buttons
from lvjiang.ui.widgets.button_styles import (
    apply_button_style,
)
from lvjiang.ui.widgets.combo_box import AutoWidthComboBox

from ......i18n import tr, tr_label
from ....core.equip_parser.dingyin_parser import (
    DINGYIN_NORMAL,
    DINGYIN_TYPE_KEY,
    DINGYIN_TYPES,
)
from ...events import EQUIPMENT_CHANGED, get_event_hub
from .batch_copy import BatchCopyMixin
from .cards import _CompactEquipCard, _SlotCard
from .mock_dialog import MockEquipDialog
from .status_dialogs import (
    _ACTION_BTN_STYLE,
    _FILTER_TOGGLE_STYLE,
)
from .status_dialogs import (
    _SLOT_LAYOUT as _SLOT_LAYOUT,
)
from .status_dialogs import (
    _affix_analysis_dependencies as _affix_analysis_dependencies,
)
from .status_dialogs import (
    _FilteredDeleteDialog as _FilteredDeleteDialog,
)
from .status_dialogs import (
    _fit_filter_combo as _fit_filter_combo,
)
from .status_dialogs import (
    _MultiSelectMenu as _MultiSelectMenu,
)
from .status_dialogs import (
    _route_weapon_slot as _route_weapon_slot,
)
from .status_dialogs import (
    _status_yellow_value_style as _status_yellow_value_style,
)
from .status_filters import _EquipFilterMixin

# 状态展示行样式（与角色详情毕业率卡片一致）
_STATUS_NAME_STYLE = "font-size: 13px; color: palette(mid);"
_STATUS_VALUE_STYLE = "font-size: 15px; font-weight: 600;"


class EquipStatusTab(_EquipFilterMixin, BatchCopyMixin, QWidget):
    """装备 Tab —— 装备背包统一视图。

    顶部 8 个可点击槽位（固定 2×4），下方全部装备网格。
    点击槽位触发部位筛选，再次点击取消选中。
    """

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._equipped: dict = {}
        self._bag_items: dict = {}
        self._mock_items: dict = {}
        self._inv = None
        #: 同一备战方案面板里的战斗属性页，由面板注入；假设栏从它取副本
        self._combat_tab = None
        #: 仅影响顶部八个装备槽的展示，不参与仓储写入。
        self._hypothesis_view_enabled = False
        self._display_params: dict = {}
        self._selected_slot: str | None = None
        self._batch_copy_mode = False
        self._filters_collapsed = False
        self._batch_selected_fps: set[str] = set()
        self._batch_target_users: set[str] = set()
        self._slot_cards: dict[str, _SlotCard] = {}
        #: 筛选待按当前用户重读；见 prepare_for_user_change。构造期就是脏的：
        #: 下面那次 _load_filter_settings 还没有 _inv，只能摆出默认值，真正
        #: 属于这个用户的筛选要等库存注入后才读得到。
        self._user_filters_stale = True
        self._setup_ui()
        # 构造期不读盘：装备数据由外层 LoadoutPanel 加载一次后经
        # refresh_from 注入，这里只按空库存摆好槽位卡与筛选条。
        self._reload_display_params()
        self._load_filter_settings()
        self._refresh_slots()
        self._rebuild_grid()
        # graduation_updated 只更新状态行。equipment_changed 由外层
        # LoadoutPanel 统一编排，避免父子同时订阅后重复重建整套装备卡。
        events = get_event_hub(self._host)
        events.graduation_updated.connect(self._update_status_row)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── Row A: 操作栏 ──
        self._action_widget = QWidget()
        action_row = QHBoxLayout(self._action_widget)
        action_row.setContentsMargins(0, 0, 0, 0)
        btn_refresh = QPushButton(tr("刷新"))
        btn_refresh.setMinimumWidth(60)
        btn_refresh.setToolTip(tr("刷新装备"))
        btn_refresh.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_refresh.clicked.connect(self._on_refresh)
        action_row.addWidget(btn_refresh)
        add_user_nav_buttons(action_row, self._host)
        action_row.addStretch()

        # 毕业率分析前三页的快捷入口；词条收益率只在对话框内开放。
        btn_optimal = QPushButton(tr("最优组合"))
        btn_optimal.setToolTip(tr("搜索最优毕业率装备组合"))
        btn_optimal.setMinimumWidth(96)
        btn_optimal.setStyleSheet(_ACTION_BTN_STYLE)
        btn_optimal.clicked.connect(self._on_optimal_combo)
        action_row.addWidget(btn_optimal)

        btn_transmute = QPushButton(tr("转律建议"))
        btn_transmute.setToolTip(tr("计算当前配装的联合转律方案"))
        btn_transmute.setMinimumWidth(96)
        btn_transmute.setStyleSheet(_ACTION_BTN_STYLE)
        btn_transmute.clicked.connect(self._on_transmute)
        action_row.addWidget(btn_transmute)

        btn_affix_impact = QPushButton(tr("培养建议"))
        btn_affix_impact.setToolTip(tr("分析当前配装的合法培养建议和词条敏感度"))
        btn_affix_impact.setMinimumWidth(96)
        btn_affix_impact.setStyleSheet(_ACTION_BTN_STYLE)
        btn_affix_impact.clicked.connect(self._on_affix_impact)
        action_row.addWidget(btn_affix_impact)

        btn_chengyin_merge = QPushButton(tr("承音装备"))
        btn_chengyin_merge.setToolTip(tr("查找承音或再次转律产生的同件装备历史版本"))
        btn_chengyin_merge.setMinimumWidth(112)
        btn_chengyin_merge.setStyleSheet(_ACTION_BTN_STYLE)
        btn_chengyin_merge.clicked.connect(self._on_chengyin_merge)
        action_row.addWidget(btn_chengyin_merge)

        btn_create = QPushButton(tr("模拟计算器"))
        btn_create.setToolTip(tr("基于当前用户基础属性调整出装搭配"))
        btn_create.setMinimumWidth(96)
        btn_create.setStyleSheet(_ACTION_BTN_STYLE)
        btn_create.clicked.connect(self._on_build_calculator)
        action_row.addWidget(btn_create)

        # 导出数据
        btn_export = QPushButton(tr("导出数据"))
        btn_export.setToolTip(tr("导出为 leoq7 格式"))
        btn_export.setMinimumWidth(96)
        btn_export.setStyleSheet(_ACTION_BTN_STYLE)
        btn_export.clicked.connect(self._on_export)
        action_row.addWidget(btn_export)
        layout.addWidget(self._action_widget)

        # ── Row B: 信息 + 筛选栏 ──
        self._info_widget = QWidget()
        info_layout = QVBoxLayout(self._info_widget)
        info_layout.setContentsMargins(8, 0, 8, 4)
        info_layout.setSpacing(6)

        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(12)

        # 左区：DPS + 毕业率
        dps_lbl = QLabel(tr("DPS"))
        self._status_dps_name = dps_lbl
        dps_lbl.setStyleSheet(_STATUS_NAME_STYLE)
        metrics_row.addWidget(dps_lbl)
        self._status_dps = QLabel("--")
        self._status_dps.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._status_dps.setMinimumWidth(80)
        self._status_dps.setStyleSheet(_STATUS_VALUE_STYLE)
        metrics_row.addWidget(self._status_dps)

        metrics_row.addSpacing(16)

        rate_lbl = QLabel(tr("毕业率"))
        self._status_graduation_name = rate_lbl
        rate_lbl.setStyleSheet(_STATUS_NAME_STYLE)
        metrics_row.addWidget(rate_lbl)
        self._status_graduation = QLabel("--")
        self._status_graduation.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._status_graduation.setMinimumWidth(80)
        self._status_graduation.setStyleSheet(_status_yellow_value_style())
        metrics_row.addWidget(self._status_graduation)

        metrics_row.addStretch()
        info_layout.addLayout(metrics_row)

        # 排序/来源/操作与具体筛选条件分成两行，窄窗口下仍保持完整可读。
        primary_filter_row = QHBoxLayout()
        primary_filter_row.setSpacing(8)

        # 右区：筛选下拉框
        _filter_lbl_style = "font-size: 12px; color: palette(mid);"

        lbl_source = QLabel(tr("类型"))
        lbl_source.setStyleSheet(_filter_lbl_style)
        primary_filter_row.addWidget(lbl_source)
        self._source_filter = AutoWidthComboBox()
        self._source_filter.addItem(tr("全部"), "all")
        self._source_filter.addItem(tr("背包"), "bag")
        self._source_filter.addItem(tr("模拟"), "mock")
        self._source_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        _fit_filter_combo(self._source_filter)
        self._source_filter.currentIndexChanged.connect(self._on_filter_changed)
        primary_filter_row.addWidget(self._source_filter)

        # 排序
        lbl_sort = QLabel(tr("排序"))
        lbl_sort.setStyleSheet(_filter_lbl_style)
        primary_filter_row.addWidget(lbl_sort)
        self._sort_filter = AutoWidthComboBox()
        self._sort_filter.addItem(tr("默认"), "default")
        self._sort_filter.addItem(tr("等级倒序"), "level_desc")
        self._sort_filter.addItem(tr("等级正序"), "level_asc")
        self._sort_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        _fit_filter_combo(self._sort_filter)
        self._sort_filter.currentIndexChanged.connect(self._on_filter_changed)
        primary_filter_row.addWidget(self._sort_filter)
        primary_filter_row.addStretch()

        self._btn_import_build = QPushButton(tr("导入出装搭配"))
        self._btn_import_build.setEnabled(False)
        self._btn_import_build.setToolTip(tr("请先选择用户、备战方案及玩法"))
        apply_button_style(self._btn_import_build, variant="action")
        self._btn_import_build.clicked.connect(self._on_build_import)
        primary_filter_row.addWidget(self._btn_import_build)

        self._btn_create_mock = QPushButton(tr("创建模拟装备"))
        self._btn_create_mock.setToolTip(tr("为当前用户创建一件模拟装备，不修改角色基础属性"))
        apply_button_style(self._btn_create_mock, variant="action")
        self._btn_create_mock.clicked.connect(self._on_mock_create)
        primary_filter_row.addWidget(self._btn_create_mock)

        self._btn_delete_filtered = QPushButton(tr("删除筛选装备"))
        self._btn_delete_filtered.setToolTip(tr(
            "只删除当前筛选出的背包装备，此处不支持删除模拟装备"))
        apply_button_style(self._btn_delete_filtered, variant="danger")
        self._btn_delete_filtered.clicked.connect(self._on_delete_filtered)
        primary_filter_row.addWidget(self._btn_delete_filtered)

        self._btn_batch_copy = QPushButton(tr("批量复制装备"))
        self._btn_batch_copy.setToolTip(tr("批量复制模拟装备到其他用户"))
        apply_button_style(self._btn_batch_copy)
        self._btn_batch_copy.clicked.connect(self._enter_batch_copy_mode)
        self._btn_batch_copy.setVisible(False)
        primary_filter_row.addWidget(self._btn_batch_copy)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self._advanced_filter_widget = QWidget()
        advanced_filter_row = QHBoxLayout(self._advanced_filter_widget)
        advanced_filter_row.setContentsMargins(0, 0, 0, 0)
        advanced_filter_row.setSpacing(8)

        lbl_part = QLabel(tr("部位"))
        lbl_part.setStyleSheet(_filter_lbl_style)
        advanced_filter_row.addWidget(lbl_part)
        self._type_filter = AutoWidthComboBox()
        self._type_filter.addItem(tr("全部"), "all")
        for sk, dn, _ in [
            ("main_weapon", tr("主武"), "weapon"),
            ("sub_weapon", tr("副武"), "weapon"),
            ("ring", tr("环"), "ring"),
            ("pendant", tr("佩"), "pendant"),
            ("head", tr("冠胄"), "head"),
            ("chest", tr("胸甲"), "chest"),
            ("leg", tr("胫甲"), "leg"),
            ("wrist", tr("腕甲"), "wrist"),
        ]:
            self._type_filter.addItem(dn, sk)
        self._type_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        _fit_filter_combo(self._type_filter)
        shared_filter_width = max(
            self._source_filter.minimumWidth(),
            self._type_filter.minimumWidth(),
        )
        self._source_filter.setFixedWidth(shared_filter_width)
        self._type_filter.setFixedWidth(shared_filter_width)
        self._type_filter.currentIndexChanged.connect(self._on_filter_changed)
        advanced_filter_row.addWidget(self._type_filter)

        lbl_quality = QLabel(tr("品阶"))
        lbl_quality.setStyleSheet(_filter_lbl_style)
        advanced_filter_row.addWidget(lbl_quality)
        self._quality_filter = AutoWidthComboBox()
        self._quality_filter.addItem(tr("全部"), "all")
        self._quality_filter.addItem(tr("金装"), "gold")
        self._quality_filter.addItem(tr("紫装"), "purple")
        self._quality_filter.addItem(tr("白装"), "other")
        self._quality_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        _fit_filter_combo(self._quality_filter)
        self._quality_filter.currentIndexChanged.connect(self._on_filter_changed)
        advanced_filter_row.addWidget(self._quality_filter)

        lbl_level = QLabel(tr("等级"))
        lbl_level.setStyleSheet(_filter_lbl_style)
        advanced_filter_row.addWidget(lbl_level)
        self._level_filter = AutoWidthComboBox()
        self._level_filter.addItem(tr("全部"), "all")
        from lvjiang.apps.yysls.config import get_game_config
        for lvl in sorted([c.level for c in get_game_config().get_level_configs()], reverse=True):
            self._level_filter.addItem(tr("≥{level}").format(level=lvl), str(lvl))
        self._level_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        _fit_filter_combo(self._level_filter)
        self._level_filter.currentIndexChanged.connect(self._on_filter_changed)
        advanced_filter_row.addWidget(self._level_filter)

        lbl_affix = QLabel(tr("词条"))
        lbl_affix.setStyleSheet(_filter_lbl_style)
        advanced_filter_row.addWidget(lbl_affix)
        self._affix_filter = AutoWidthComboBox()
        self._affix_filter.addItem(tr("全部"), "all")
        self._affix_filter.addItem(tr("已定音"), "dingyin")
        self._affix_filter.addItem(tr("满调律"), "full_tuning")
        self._affix_filter.addItem(tr("未满调律"), "not_full_tuning")
        _fit_filter_combo(self._affix_filter)
        self._affix_filter.currentIndexChanged.connect(self._on_filter_changed)
        advanced_filter_row.addWidget(self._affix_filter)

        lbl_status = QLabel(tr("状态"))
        lbl_status.setStyleSheet(_filter_lbl_style)
        advanced_filter_row.addWidget(lbl_status)
        self._status_filter = AutoWidthComboBox()
        self._status_filter.addItem(tr("全部"), "all")
        self._status_filter.addItem(tr("备战中"), "referenced")
        self._status_filter.addItem(tr("未备战"), "unreferenced")
        self._status_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        _fit_filter_combo(self._status_filter)
        self._status_filter.currentIndexChanged.connect(self._on_filter_changed)
        advanced_filter_row.addWidget(self._status_filter)

        lbl_scan_time = QLabel(tr("扫描时间"))
        lbl_scan_time.setStyleSheet(_filter_lbl_style)
        advanced_filter_row.addWidget(lbl_scan_time)
        self._scan_time_filter = AutoWidthComboBox()
        self._scan_time_filter.addItem(tr("全部"), "all")
        self._scan_time_filter.addItem(tr("超过 1 小时"), "1h")
        self._scan_time_filter.addItem(tr("超过 1 天"), "1d")
        self._scan_time_filter.addItem(tr("超过 3 天"), "3")
        self._scan_time_filter.addItem(tr("超过 7 天"), "7")
        self._scan_time_filter.addItem(tr("超过 14 天"), "14")
        self._scan_time_filter.addItem(tr("超过 30 天"), "30")
        self._scan_time_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        _fit_filter_combo(self._scan_time_filter)
        self._scan_time_filter.currentIndexChanged.connect(
            self._on_filter_changed)
        advanced_filter_row.addWidget(self._scan_time_filter)
        filter_row.addWidget(self._advanced_filter_widget)

        self._filter_collapse_button = QToolButton()
        self._scan_time_filter.ensurePolished()
        toggle_side = self._scan_time_filter.sizeHint().height()
        self._filter_collapse_button.setFixedSize(toggle_side, toggle_side)
        icon_side = max(8, round(toggle_side * 0.4))
        self._filter_collapse_button.setIconSize(QSize(icon_side, icon_side))
        self._filter_collapse_button.setStyleSheet(_FILTER_TOGGLE_STYLE)
        self._filter_collapse_button.setToolTip(tr("收起筛选条件"))
        self._filter_collapse_button.clicked.connect(
            self._toggle_advanced_filters)
        filter_row.addWidget(self._filter_collapse_button)

        filter_row.addStretch()
        self._filter_widget = QWidget()
        filter_layout = QVBoxLayout(self._filter_widget)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(6)
        filter_layout.addLayout(primary_filter_row)
        filter_layout.addLayout(filter_row)
        info_layout.addWidget(self._filter_widget)

        self._batch_copy_widget = QWidget()
        batch_row = QHBoxLayout(self._batch_copy_widget)
        batch_row.setContentsMargins(0, 0, 0, 0)
        batch_row.setSpacing(8)
        self._btn_batch_select_all = QPushButton(tr("全选"))
        apply_button_style(self._btn_batch_select_all, variant="neutral")
        self._btn_batch_select_all.clicked.connect(self._select_all_batch_cards)
        batch_row.addWidget(self._btn_batch_select_all)
        self._batch_selected_label = QLabel(
            tr("已选择 {count} 件").format(count=0))
        batch_row.addWidget(self._batch_selected_label)
        batch_row.addStretch()
        self._btn_copy_targets = QPushButton(tr("复制到…"))
        self._copy_targets_menu = _MultiSelectMenu(self._btn_copy_targets)
        self._btn_copy_targets.setMenu(self._copy_targets_menu)
        apply_button_style(self._btn_copy_targets, variant="neutral")
        batch_row.addWidget(self._btn_copy_targets)
        self._btn_confirm_batch_copy = QPushButton(tr("确认复制"))
        apply_button_style(self._btn_confirm_batch_copy)
        self._btn_confirm_batch_copy.clicked.connect(self._copy_selected_mocks)
        batch_row.addWidget(self._btn_confirm_batch_copy)
        btn_cancel_batch = QPushButton(tr("退出批量模式"))
        apply_button_style(btn_cancel_batch, variant="neutral")
        btn_cancel_batch.clicked.connect(self._exit_batch_copy_mode)
        batch_row.addWidget(btn_cancel_batch)
        self._batch_copy_widget.setVisible(False)
        info_layout.addWidget(self._batch_copy_widget)

        layout.addWidget(self._info_widget)

        # ── 滚动区域：槽位 + 背包网格统一滚动 ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 始终保留垂直滚动条槽位，筛选结果变少时页面宽度不跳动。
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(0)

        # 顶部：8 个可点击槽位（固定 2×4）
        self._slot_container = QWidget()
        # Expanding：跟随父容器宽度铺满，避免固定 4 列在宽度不足时溢出被截断
        self._slot_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        slot_grid = QGridLayout(self._slot_container)
        slot_grid.setSpacing(8)
        slot_grid.setContentsMargins(8, 8, 8, 8)

        for row, col, slot_key, display_name, _filter_type in _SLOT_LAYOUT:
            card = _SlotCard(slot_key, display_name, _filter_type)
            slot_grid.addWidget(card, row, col)
            self._slot_cards[slot_key] = card

        # 4 列等宽伸缩：宽度不足时各列均分收缩，保证第 4 列始终可见
        for c in range(4):
            slot_grid.setColumnStretch(c, 1)

        wrapper_layout.addWidget(self._slot_container)

        # 分割线 —— 区分可点击槽位区与背包区
        self._slot_separator = QFrame()
        self._slot_separator.setFrameShape(QFrame.Shape.HLine)
        self._slot_separator.setStyleSheet(
            "background-color: palette(midlight); max-height: 1px; margin: 4px 8px;")
        self._slot_separator.setFixedHeight(1)
        wrapper_layout.addWidget(self._slot_separator)

        # 背包网格
        self._grid_container = QWidget()
        self._grid_container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._grid = QGridLayout(self._grid_container)
        self._grid.setSpacing(8)
        self._grid.setContentsMargins(8, 8, 8, 8)

        wrapper_layout.addWidget(self._grid_container)
        wrapper_layout.addStretch()
        scroll.setWidget(wrapper)
        layout.addWidget(scroll, stretch=1)

    def set_embedded_mode(self, embedded: bool = True) -> None:
        """Hide duplicated chrome when hosted by LoadoutPanel."""
        self._action_widget.setVisible(not embedded)
        for widget in (
            self._status_dps_name, self._status_dps,
            self._status_graduation_name, self._status_graduation,
        ):
            widget.setVisible(not embedded)

    # ── 筛选 ──

    def _on_refresh(self):
        self._refresh_all()
        get_event_hub(self._host).publish(
            EQUIPMENT_CHANGED, self._host.active_user_name() or "")

    def _update_status_row(self, result=None):
        """更新状态展示行：从 LoadoutPanel 读取 DPS 和毕业率。"""
        if result is None:
            # 从 LoadoutPanel 读取（信号未携带结果时的兑底）
            from ..loadout_panel import LoadoutPanel
            widget = self.parent()
            while widget is not None:
                if isinstance(widget, LoadoutPanel):
                    result = widget._graduation_result
                    break
                widget = widget.parent()
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
        self._status_dps.setText(dps_text)
        self._status_graduation.setText(rate_text)
        self._status_dps.setToolTip(tooltip)
        self._status_graduation.setToolTip(tooltip)

    def bind_combat_tab(self, combat_tab) -> None:
        """由备战方案面板注入同级的战斗属性页；不再靠 findChildren 摸兄弟。"""
        self._combat_tab = combat_tab

    def set_hypothesis_view_enabled(self, enabled: bool) -> None:
        """只重绘装备槽；投影副本不进入库存、卡片网格或仓储。"""
        enabled = bool(enabled)
        if self._hypothesis_view_enabled == enabled:
            return
        self._hypothesis_view_enabled = enabled
        self._refresh_slots()

    def refresh_hypothesis_view(self) -> None:
        """假设选项变化后刷新已开启的假设视图。"""
        if self._hypothesis_view_enabled:
            self._refresh_slots()

    def _slot_view_equipment(self) -> tuple[dict, object | None]:
        """返回顶部装备槽使用的数据；开启时始终是一次性内存副本。"""
        if not self._hypothesis_view_enabled or self._combat_tab is None:
            return self._equipped, None
        try:
            assumptions = self._combat_tab.assumptions()
            return assumptions.project(copy.deepcopy(self._equipped)), assumptions
        except Exception as exc:  # noqa: BLE001 - 展示失败不能破坏真实装备页
            logger.error(f"生成装备假设视图失败，已回退真实装备: {exc}")
            return self._equipped, None

    def _reload_display_params(self) -> None:
        """刷新前重读装备卡片展示参数：设置页改过字号/列数，刷新即生效。"""
        from ....config.equip_display import load_equip_display

        self._display_params = load_equip_display()

    def refresh_from(self, inventory) -> None:
        """用面板本轮已加载的仓储快照刷新，不再自己重新读盘。"""
        self._reload_display_params()
        self._inv = inventory
        self._apply_pending_filters()
        self._sync_inv()
        self._update_status_row()

    def _clear_inventory(self) -> None:
        self._inv = None
        self._update_build_import_button()
        self._equipped = {}
        self._bag_items = {}
        self._mock_items = {}
        self._apply_pending_filters()
        self._refresh_slots()
        self._rebuild_grid()

    def _refresh_all(self):
        self._reload_display_params()

        user_name = self._host.active_user_name()
        if not user_name:
            self._clear_inventory()
            return

        try:
            from ....core.combat.equipment import EquipmentInventory
            self._inv = EquipmentInventory(user_name)
        except Exception as e:
            logger.error(f"加载装备失败: {e}")
            self._clear_inventory()
            self._update_status_row()
            return

        self._apply_pending_filters()
        self._sync_inv()
        self._update_status_row()

    def _sync_inv(self, *, notify: bool = False) -> None:
        """从 EquipmentInventory 同步本地缓存并刷新 UI。"""
        if self._inv is None:
            return
        self._equipped = self._inv.equipped
        self._bag_items = self._inv.bag_items
        self._mock_items = self._inv.mock_items
        self._update_build_import_button()
        self._refresh_slots()
        self._rebuild_grid()
        if notify:
            get_event_hub(self._host).publish(
                EQUIPMENT_CHANGED, self._host.active_user_name() or "")

    def _update_item_metadata(self, fp: str, key: str, value: str) -> None:
        """原地同步不影响指纹和属性的单装备字段。"""
        seen: set[int] = set()

        def update_mapping(items) -> None:
            for equip in items:
                if str(equip.get("_fp") or "") != fp:
                    continue
                identity = id(equip)
                if identity not in seen:
                    equip[key] = value
                    seen.add(identity)

        update_mapping(self._equipped.values())
        for grouped in (self._bag_items, self._mock_items):
            for items in grouped.values():
                update_mapping(items.values())

        def update_card(card) -> None:
            data = getattr(card, "_equip_data", {})
            if str(data.get("_fp") or "") != fp:
                return
            if key == "lock_status":
                card.update_lock_status(value)
            elif key == "cooldown_expires_at":
                card.update_cooldown(value)

        for card in self._slot_cards.values():
            update_card(card)
        for index in range(self._grid.count()):
            card = self._grid.itemAt(index).widget()
            if isinstance(card, _CompactEquipCard):
                update_card(card)

    def _require_inventory(self):
        """返回已加载的装备库存；不可用时给出统一提示。"""
        if self._inv is None:
            QMessageBox.warning(
                self, tr("提示"), tr("装备未加载，请刷新"))
            return None
        return self._inv

    def _refresh_slots(self):
        dp = self._display_params
        displayed, assumptions = self._slot_view_equipment()
        for _row, _col, slot_key, _display_name, _filter_type in _SLOT_LAYOUT:
            card = self._slot_cards[slot_key]
            card._name_fs = dp.get("name_font_size", 13)
            card._level_fs = dp.get("level_font_size", 12)
            card._affix_fs = dp.get("affix_font_size", 11)
            card._card_h = dp.get("card_min_height", 160)
            card.setFixedHeight(card._card_h)

            equip = displayed.get(slot_key)
            if equip:
                dingyin_kind = self._plan_dingyin_kind(slot_key)
                if assumptions is not None and assumptions.full_dingyin:
                    dingyin_kind = DINGYIN_NORMAL
                card.set_equip(
                    equip, dingyin_kind=dingyin_kind)
            else:
                card.set_empty()
            # 保持选中态
            card.set_selected(slot_key == self._selected_slot)

    # ── 装备操作 ──

    def _on_properties_requested(
        self, equip_data: dict, *, slot_key: str = "",
    ) -> None:
        """展示装备属性，并把冷却时间变更持久化。"""
        from .cards import _show_equipment_properties

        def update_cooldown(value: str) -> bool:
            fp = str(equip_data.get("_fp") or "")
            if not fp:
                QMessageBox.warning(
                    self, tr("修改失败"), tr("装备数据缺少 _fp 字段"))
                return False
            inv = self._require_inventory()
            if inv is None:
                return False
            try:
                inv.set_item_cooldown(fp, value)
                self._update_item_metadata(fp, "cooldown_expires_at", value)
                # 与仓储 set_item_cooldown 同步 kind/state，否则背包/模拟里
                # 同指纹的其他副本仍残留「冷却完成」，卡片文案要到重载才消失
                if value:
                    self._update_item_metadata(
                        fp, "cooldown_kind",
                        str(equip_data.get("cooldown_kind") or "transmute"))
                    self._update_item_metadata(fp, "cooldown_state", "cooling")
                else:
                    self._update_item_metadata(fp, "cooldown_kind", "")
                    self._update_item_metadata(fp, "cooldown_state", "")
                return True
            except Exception as exc:
                logger.error(f"修改装备冷却时间失败: {exc}")
                QMessageBox.critical(self, tr("修改失败"), str(exc))
                return False

        fp = str(equip_data.get("_fp") or "")
        loaded_inv = getattr(self, "_inv", None)
        state = getattr(loaded_inv, "state", None)
        referenced_plans = (
            state.referencing_plan_names(fp)
            if state is not None and fp else [])

        def switch_dingyin(kind: str) -> bool:
            return EquipStatusTab._on_dingyin_requested(
                self,
                equip_data, kind, slot_key=slot_key)

        def switch_equipment_set(set_key: str) -> bool:
            inv = self._require_inventory()
            if inv is None:
                return False
            try:
                if slot_key:
                    inv.set_plan_equipment_set(slot_key, set_key)
                else:
                    inv.set_item_equipment_set(fp, set_key)
                self._sync_inv()
                return True
            except Exception as exc:
                logger.error(f"修改装备套装失败: {exc}")
                QMessageBox.critical(self, tr("修改失败"), str(exc))
                return False

        def update_assume_chengyin(enabled: bool) -> bool:
            inv = self._require_inventory()
            if inv is None or not fp:
                return False
            try:
                inv.set_item_assume_chengyin(fp, enabled)
                # 计算设置会直接改变毕业率，通知战斗属性页重算。
                self._sync_inv(notify=True)
                return True
            except Exception as exc:
                logger.error(f"修改假设同级承音失败: {exc}")
                QMessageBox.critical(self, tr("修改失败"), str(exc))
                return False

        def dingyin_swap_candidates(kind: str) -> list[dict]:
            inv = self._require_inventory()
            if inv is None:
                return []
            try:
                return inv.dingyin_swap_candidates(fp, kind)
            except Exception as exc:
                logger.error(f"加载定音互换候选失败: {exc}")
                raise

        def swap_dingyin(kind: str, target_fp: str) -> dict | None:
            inv = self._require_inventory()
            if inv is None:
                return None
            try:
                inv.swap_item_dingyin(fp, target_fp, kind)
                updated = copy.deepcopy(inv.state.equipment_items.get(fp))
                self._sync_inv(notify=True)
                return updated
            except Exception as exc:
                logger.error(f"互换装备定音失败: {exc}")
                QMessageBox.critical(self, tr("互换失败"), str(exc))
                return None

        _show_equipment_properties(
            self.window(), equip_data, cooldown_changed=update_cooldown,
            dingyin_changed=switch_dingyin,
            equipment_set_changed=switch_equipment_set,
            assume_chengyin_changed=update_assume_chengyin,
            dingyin_swap_candidates=dingyin_swap_candidates,
            dingyin_swapped=swap_dingyin,
            dingyin_kind=(self._plan_dingyin_kind(slot_key)
                          if slot_key else ""),
            referenced_plans=referenced_plans)

    def _on_bag_dingyin_requested(
        self, equip_data: dict, kind: str,
    ) -> None:
        """背包卡片没有方案槽位上下文，只改装备自身展示态。"""
        self._on_dingyin_requested(equip_data, kind, slot_key="")

    def _on_dingyin_requested(
        self, equip_data: dict, kind: str, *, slot_key: str,
    ) -> bool:
        """定音切换的唯一 UI 写入出口。

        装备槽传入 slot_key，只写方案引用；背包传空字符串，
        只写装备 dingyin_type。不再按指纹反查装备是否正在穿戴。
        """
        fp = str(equip_data.get("_fp") or "")
        if not fp:
            QMessageBox.warning(
                self, tr("切换失败"), tr("装备数据缺少 _fp 字段"))
            return False
        inv = self._require_inventory()
        if inv is None:
            return False
        try:
            if slot_key:
                inv.set_plan_dingyin(slot_key, kind)
            else:
                inv.set_item_dingyin_type(fp, kind)
                self._update_item_metadata(fp, DINGYIN_TYPE_KEY, kind)
        except Exception as exc:
            logger.error(f"切换定音失败: {exc}")
            QMessageBox.critical(self, tr("切换失败"), str(exc))
            return False
        # 只重画受影响的卡片：方案选择只改装备栏，展示状态只改背包卡片。
        if slot_key:
            self._refresh_slots()
        else:
            self._refresh_dingyin_cards(fp)
        return True

    def _refresh_dingyin_cards(self, fp: str) -> None:
        """重画背包/模拟列表里同指纹的卡片，不重载整页。"""
        for index in range(self._grid.count()):
            item = self._grid.itemAt(index)
            card = item.widget() if item is not None else None
            if not isinstance(card, _CompactEquipCard):
                continue
            data = getattr(card, "_equip_data", {})
            if str(data.get("_fp") or "") == fp:
                card.refresh_affixes()

    def _plan_dingyin_kind(self, slot_key: str) -> str:
        """当前方案对槽位选择的定音；未记录固定按普通定音。"""
        inv = self._inv
        if inv is None or not slot_key:
            return ""
        plan = inv.state.plans.get(inv.state.active_plan_id)
        if plan is None:
            return DINGYIN_NORMAL
        kind = str(plan.dingyin.get(slot_key) or "")
        return kind if kind in DINGYIN_TYPES else DINGYIN_NORMAL

    def _on_lock_requested(self, equip_data: dict, locked: bool) -> None:
        """在不改变装备指纹的前提下修改锁定状态。"""
        fp = str(equip_data.get("_fp") or "")
        if not fp:
            QMessageBox.warning(
                self, tr("修改失败"), tr("装备数据缺少 _fp 字段"))
            return
        inv = self._require_inventory()
        if inv is None:
            return
        try:
            inv.set_item_lock_status(fp, locked)
            self._update_item_metadata(
                fp, "lock_status", "locked" if locked else "unlock")
            logger.info(
                "已{}装备: {}",
                "锁定" if locked else "解锁",
                equip_data.get("name") or fp,
            )
        except Exception as exc:
            logger.error(f"修改装备锁定状态失败: {exc}")
            QMessageBox.critical(self, tr("修改失败"), str(exc))

    def _on_clear_transmute_target(self, equip_data: dict) -> None:
        """删除装备上保存的模拟转律目标；目标属于公共装备，影响所有方案。"""
        fp = str(equip_data.get("_fp") or "")
        if not fp:
            QMessageBox.warning(
                self, tr("清除失败"), tr("装备数据缺少 _fp 字段"))
            return
        inv = self._require_inventory()
        if inv is None:
            return
        answer = QMessageBox.question(
            self, tr("清除转律目标"),
            tr("将删除「{name}」的转律目标，所有引用该装备的备战方案都会受影响。"
               "继续？").format(name=equip_data.get("name") or fp),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            inv.clear_transmute_target(fp)
            self._sync_inv(notify=True)
            logger.info("已清除装备转律目标: {}", equip_data.get("name") or fp)
        except Exception as exc:
            logger.error(f"清除转律目标失败: {exc}")
            QMessageBox.critical(self, tr("清除失败"), str(exc))

    def _on_equip_requested(self, equip_data: dict, group_key: str):
        """处理装备请求：将背包/模拟中的装备穿戴到对应槽位"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("装备失败"), tr("没有激活的用户"))
            return

        new_fp = equip_data.get("_fp", "")
        if not new_fp:
            QMessageBox.warning(self, tr("装备失败"), tr("装备数据缺少 _fp 字段"))
            return

        # 确定目标槽位
        target_slots = self._get_slots_for_group(group_key)
        if not target_slots:
            logger.error(f"无法找到 {group_key} 对应的槽位")
            return

        # 武器按方案两门武学派生的类型路由：主副武器不同型时直接生效，
        # 与主副武学均不匹配的武器禁止装备（需另建对应流派方案）
        if len(target_slots) > 1:
            main_type = self._get_plan_weapon_type("main_weapon") or ""
            sub_type = self._get_plan_weapon_type("sub_weapon") or ""
            route = _route_weapon_slot(equip_data.get("type", ""), main_type, sub_type)
            if route == "reject":
                QMessageBox.warning(
                    self, tr("无法装备"),
                    tr("武器【{type}】与当前方案的武学不匹配"
                       "（主武学武器：{main}，副武学武器：{sub}）。\n"
                       "如需使用该武器，请新建对应流派的方案。").format(
                        type=equip_data.get("type", tr("未知")),
                        main=main_type, sub=sub_type))
                return
            if route in ("main_weapon", "sub_weapon") and route in target_slots:
                target_slot = route
            else:
                # 主副武器同型或流派未绑定：仍需手动选择
                from PyQt6.QtWidgets import QInputDialog
                slot_names = [tr("主武器") if s == "main_weapon" else tr("副武器") for s in target_slots]
                choice, ok = QInputDialog.getItem(
                    self, tr("选择槽位"), tr_label('请选择要穿戴到的槽位'),
                    slot_names, 0, False
                )
                if not ok:
                    return
                target_slot = target_slots[slot_names.index(choice)]
        else:
            target_slot = target_slots[0]

        inv = self._require_inventory()
        if inv is None:
            return
        try:
            inv.equip_to_slot(target_slot, equip_data, group_key)
            self._sync_inv(notify=True)
            logger.info(f"已装备 {equip_data.get('name', '未知')} 到 {target_slot}")
        except Exception as e:
            logger.error(f"装备失败: {e}")
            QMessageBox.critical(self, tr("装备失败"), str(e))

    def _on_delete_requested(self, equip_data: dict, group_key: str):
        """处理删除请求：区分模拟/真实装备"""
        is_mock = equip_data.get("_extra", {}).get("is_mock", False)
        if is_mock:
            self._on_mock_delete_requested(equip_data, group_key)
        else:
            self._on_real_delete_requested(equip_data, group_key)

    def _on_real_delete_requested(self, equip_data: dict, group_key: str):
        """处理删除请求：从背包中删除装备"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("删除失败"), tr("没有激活的用户"))
            return

        fp = equip_data.get("_fp", "")
        if not fp:
            QMessageBox.warning(self, tr("删除失败"), tr("装备数据缺少 _fp 字段"))
            return

        # 二次确认
        equip_name = equip_data.get("name", tr("未知"))
        reply = QMessageBox.question(
            self,
            tr("确认删除"),
            tr("确定要从背包中删除【{name}】吗？\n此操作不可撤销。").format(name=equip_name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        inv = self._require_inventory()
        if inv is None:
            return
        try:
            inv.delete_from_bag(group_key, fp)
            self._sync_inv(notify=True)
            logger.info(f"已删除 {equip_name}")
        except Exception as e:
            logger.error(f"删除失败: {e}")
            QMessageBox.critical(self, tr("删除失败"), str(e))

    def _filter_summary(self) -> str:
        """生成批量删除确认框使用的当前筛选条件。"""
        return "\n".join((
            f"{tr('部位')}：{self._type_filter.currentText()}",
            f"{tr('品阶')}：{self._quality_filter.currentText()}",
            f"{tr('等级')}：{self._level_filter.currentText()}",
            f"{tr('词条')}：{self._affix_filter.currentText()}",
            f"{tr('状态')}：{self._status_filter.currentText()}",
            f"{tr('扫描时间')}：{self._scan_time_filter.currentText()}",
        ))

    def _on_delete_filtered(self) -> None:
        """删除筛选结果；默认及当前穿戴保护在此处明确收口。"""
        inv = self._require_inventory()
        if inv is None:
            return
        fingerprints = self._filtered_delete_fingerprints()
        if not fingerprints:
            QMessageBox.information(
                self, tr("提示"), tr("当前筛选条件下没有可删除的背包装备"))
            return

        last_seen_before = self._scan_time_delete_constraint()

        referenced = inv.referenced_plan_fps
        dialog = _FilteredDeleteDialog(
            self._filter_summary(), fingerprints, referenced, self,
            locked_fingerprints=inv.locked_item_fps,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            deleted = inv.delete_items(
                fingerprints,
                preserve_referenced=dialog.preserve_referenced,
                preserve_locked=dialog.preserve_locked,
                last_seen_before=last_seen_before,
            )
            self._sync_inv(notify=True)
            logger.info(
                "已删除筛选装备: 筛选 {} 件，保护 {} 件，实际删除 {} 件",
                len(fingerprints), len(fingerprints) - len(deleted),
                len(deleted),
            )
        except Exception as exc:
            logger.error(f"删除筛选装备失败: {exc}")
            QMessageBox.critical(self, tr("删除失败"), str(exc))

    def _filtered_delete_fingerprints(self) -> set[str]:
        """筛选删除只处理真实背包装备，模拟装备有独立删除入口。"""
        return {
            str(equip.get("_fp") or "")
            for equip, _part, _group, is_mock, _referenced
            in self._collect_filtered_cards()
            if equip.get("_fp")
            and not is_mock
        }

    def _on_chengyin_merge(self):
        """汇总全部用户候选，确认后按用户迁移引用并删除旧快照。"""
        from ....config import get_game_config
        from .chengyin_merge_dialog import (
            ChengyinMergeDialog,
            load_user_chengyin_candidates,
        )

        candidates = load_user_chengyin_candidates(
            self._host.user_manager.list_users(),
            get_game_config().get_level_configs(),
            min_chengyin_level=get_game_config().current_min_chengyin_level(),
        )
        dialog = ChengyinMergeDialog(
            candidates, self._display_params, self)
        dialog.merge_requested.connect(lambda: self._merge_chengyin_selection(dialog))
        dialog.exec()

    def _merge_chengyin_selection(self, dialog):
        """执行当前选择并重读候选，合并成功或失败都保留确认窗口。"""
        from ....config import get_game_config
        from ....core.loadout import LoadoutRepository
        from .chengyin_merge_dialog import load_user_chengyin_candidates

        selected = dialog.selected_candidates()
        if not selected:
            return
        replacements_by_user: dict[str, dict[str, str]] = {}
        for entry in selected:
            candidate = entry.candidate
            replacements = replacements_by_user.setdefault(entry.username, {})
            existing = replacements.get(candidate.old_fp)
            if existing is not None and existing != candidate.new_fp:
                QMessageBox.warning(
                    dialog, tr("合并失败"),
                    tr("选中项包含冲突关系，请确保同一旧装备只合并到一个新版本。"))
                return
            replacements[candidate.old_fp] = candidate.new_fp
        completed_users = []
        error = None
        try:
            for username, replacements in replacements_by_user.items():
                LoadoutRepository(username).merge_items(replacements)
                completed_users.append(username)
        except Exception as exc:
            logger.exception("承音装备合并失败")
            error = exc
        if completed_users:
            self._refresh_all()
        # 承音合并会写多个用户，逐个通知——只报当前用户会漏掉其余几个。
        hub = get_event_hub(self._host)
        for username in completed_users:
            hub.publish(EQUIPMENT_CHANGED, username)
        # 即使后一个用户失败，前面已成功的合并也不能留在旧候选中重复执行。
        dialog.set_candidates(load_user_chengyin_candidates(
            self._host.user_manager.list_users(),
            get_game_config().get_level_configs(),
            min_chengyin_level=get_game_config().current_min_chengyin_level(),
        ))
        count = sum(len(replacements_by_user[name]) for name in completed_users)
        if error is not None:
            dialog.result_label.setText(tr(
                "本次成功合并 {count} 份旧装备快照，其余操作未完成。请核对错误后重新选择；已完成的合并不会撤销。"
            ).format(count=count))
            QMessageBox.critical(dialog, tr("合并失败"), str(error))
        else:
            dialog.result_label.setText(tr(
                "已合并 {count} 份旧装备快照，方案引用已迁移。可继续选择其他候选；取消合并仅关闭窗口，不撤销已完成操作。"
            ).format(count=count))

    def _on_mock_delete_requested(self, equip_data: dict, group_key: str):
        """处理模拟装备删除请求"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("删除失败"), tr("没有激活的用户"))
            return

        fp = equip_data.get("_fp", "")
        if not fp:
            QMessageBox.warning(self, tr("删除失败"), tr("装备数据缺少 _fp 字段"))
            return

        equip_name = equip_data.get("name", tr("未知"))
        reply = QMessageBox.question(
            self,
            tr("确认删除"),
            tr("确定要删除模拟装备【{name}】吗？\n此操作不可撤销。").format(name=equip_name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        inv = self._require_inventory()
        if inv is None:
            return
        try:
            inv.delete_from_mock(group_key, fp)
            self._sync_inv(notify=True)
            logger.info(f"已删除模拟装备 {equip_name}")
        except Exception as e:
            logger.error(f"删除失败: {e}")
            QMessageBox.critical(self, tr("删除失败"), str(e))

    def _on_edit_requested(self, equip_data: dict, group_key: str):
        """处理模拟装备编辑或扫描装备养成请求。"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("编辑失败"), tr("没有激活的用户"))
            return

        old_fp = equip_data.get("_fp", "")
        dialog = MockEquipDialog(equip_data, parent=self, default_school=self._get_current_school())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.get_result()
        if not result:
            return

        inv = self._require_inventory()
        if inv is None:
            return
        is_mock = bool((equip_data.get("_extra") or {}).get("is_mock"))
        try:
            if is_mock:
                from ....config import get_game_config
                new_type = result.get("type", "")
                new_group_key = (
                    get_game_config().get_type_to_group()
                    .get(new_type, group_key)
                )
                inv.update_mock(group_key, old_fp, result, new_group_key)
            else:
                inv.update_real_development(old_fp, result)
            self._sync_inv(notify=True)
            operation = "编辑模拟装备" if is_mock else "养成扫描装备"
            logger.info(f"已{operation} {result.get('name', '未知')}")
        except Exception as e:
            operation = "编辑模拟装备" if is_mock else "养成扫描装备"
            logger.error(f"{operation}失败: {e}")
            QMessageBox.critical(self, tr("编辑失败"), str(e))

    def _get_plan_weapon_type(self, slot_key: str) -> str | None:
        """由方案对应位置的武学派生武器类型，不读取流派的主副顺序。"""
        user_name = self._host.active_user_name()
        if not user_name:
            return None
        try:
            from ....config import get_game_config
            from ....core.loadout import LoadoutRepository

            plan = LoadoutRepository(user_name).load().active_plan
            martial_art = (
                plan.main_martial_art
                if slot_key == "main_weapon"
                else plan.sub_martial_art
            )
            return get_game_config().get_martial_art_weapon(martial_art) or None
        except Exception as exc:  # noqa: BLE001 — 路由失败时退回人工选槽
            logger.debug(f"读取方案武学对应武器失败: {exc}")
            return None

    def _get_current_school(self) -> str:
        """当前激活方案的流派：由方案主副武学派生（唯一口径），不问兄弟页。"""
        if self._inv is not None:
            return self._inv.active_school
        user_name = self._host.active_user_name()
        if not user_name:
            return ""
        from ....config import get_game_config
        from ....core.loadout import LoadoutRepository
        try:
            return LoadoutRepository(user_name).load().active_school(
                get_game_config().get_schools())
        except Exception as e:  # noqa: BLE001 - 只影响候选过滤，不阻断
            logger.debug(f"解析当前流派失败: {e}")
            return ""

    def _on_build_calculator(self):
        from ..build_calculator import open_build_calculator
        open_build_calculator(self._host, self)

    def _update_build_import_button(self):
        from ....config.builds import BuildRepository
        reason = ""
        if self._inv is None or not self._host.active_user_name():
            reason = tr("请先选择用户和备战方案")
        elif not self._inv.active_plan.playstyle:
            reason = tr("请先为当前备战方案选择玩法")
        elif not BuildRepository().all(self._inv.active_plan.playstyle):
            reason = tr("当前玩法没有可导入的出装搭配")
        self._btn_import_build.setEnabled(not reason)
        self._btn_import_build.setToolTip(reason or tr("生成八件模拟装备并替换当前出装，原装备保留"))

    def _on_build_import(self):
        from ....core.loadout.repository import LoadoutRepository
        from ..build_import import BuildImportDialog
        user_name = self._host.active_user_name()
        if not user_name:
            return
        dialog = BuildImportDialog(user_name, LoadoutRepository(user_name), parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._refresh_all()
            get_event_hub(self._host).publish(EQUIPMENT_CHANGED, user_name)
            logger.info("出装搭配已导入，原装备保留")

    def _on_mock_create(self):
        """创建模拟装备"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("创建失败"), tr("没有激活的用户"))
            return

        dialog = MockEquipDialog(
            parent=self,
            default_school=self._get_current_school(),
            delete_all_mock=self._delete_all_mock,
        )
        dialog.setWindowTitle(f"{tr('创建模拟装备')} · {user_name}")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.get_result()
        if not result:
            return

        # 确定分组 key（使用全局映射）
        from ....config import get_game_config
        equip_type = result.get("type", "")
        group_key = get_game_config().get_type_to_group().get(equip_type, "ring")

        inv = self._require_inventory()
        if inv is None:
            return
        try:
            inv.add_to_mock(group_key, result)
            self._sync_inv()
            self._reset_filter_for_mock()
            logger.info(f"已创建模拟装备 {result.get('name', '未知')}")
        except Exception as e:
            logger.error(f"创建模拟装备失败: {e}")
            QMessageBox.critical(self, tr("创建失败"), str(e))

    def _delete_all_mock(self) -> int:
        """供模拟装备对话框调用：删除当前用户的全部模拟装备。"""
        inv = self._require_inventory()
        if inv is None:
            return 0
        count = inv.delete_all_mock()
        if count:
            self._sync_inv(notify=True)
            logger.info(f"已删除全部模拟装备: {count} 件")
        return count

    def _on_copy_requested(self, equip_data: dict, group_key: str):
        """复制装备数据到创建装备对话框，名称追加【复制】。"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("复制失败"), tr("没有激活的用户"))
            return

        # 深拷贝装备数据，名称追加【复制】
        copied = copy.deepcopy(equip_data)
        original_name = copied.get("name", "")
        if not original_name.endswith(tr("【复制】")):
            copied["name"] = original_name + tr("【复制】")
        copied.setdefault("_extra", {})["is_mock"] = True
        copied.pop("_fp", None)
        # 转律目标是针对原装备算出来的计划，复制件不默认携带。
        from ....core.loadout.transmute import strip_transmute_targets
        strip_transmute_targets(copied)

        dialog = MockEquipDialog(equip_data=copied, parent=self, default_school=self._get_current_school())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.get_result()
        if not result:
            return

        from ....config import get_game_config
        equip_type = result.get("type", "")
        gk = get_game_config().get_type_to_group().get(equip_type, "ring")

        inv = self._require_inventory()
        if inv is None:
            return
        try:
            inv.add_to_mock(gk, result)
            self._sync_inv()
            self._reset_filter_for_mock()
            logger.info(f"已复制创建模拟装备 {result.get('name', '未知')}")
        except Exception as e:
            logger.error(f"复制创建模拟装备失败: {e}")
            QMessageBox.critical(self, tr("复制失败"), str(e))

    def _get_slots_for_group(self, group_key: str) -> list[str]:
        """根据分组 key 获取对应的槽位 key 列表"""
        slots = []
        for _, _, slot_key, _, filter_type in _SLOT_LAYOUT:
            if filter_type == group_key:
                slots.append(slot_key)
        return slots

    # ── 导出 ──

    def _on_export(self):
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("导出失败"), tr("没有激活的用户"))
            return
        try:
            from ..leoq7_export import export_leoq7
            inv = self._require_inventory()
            if inv is None:
                return
            inv.reload()
            data = {
                "equipped": inv.equipped,
                "bag_items": inv.bag_items,
                "mock_items": inv.mock_items,
            }
            text = export_leoq7(
                data,
                user_name,
                level_threshold=self._get_level_threshold(),
                affix_filter=self._get_affix_filter(),
            )
        except Exception as e:
            logger.error(f"导出 leoq7 数据失败: {e}")
            QMessageBox.critical(self, tr("导出失败"), str(e))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("导出装备数据"),
            f"{user_name}_leoq7.txt",
            tr("文本文件 (*.txt)"),
        )
        if path:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            QMessageBox.information(
                self, tr("导出成功"),
                tr("已导出到\n{path}").format(path=path),
            )

    def _on_optimal_combo(self):
        """打开毕业率分析对话框的「最优组合」页。"""
        from ..graduation_analysis import TAB_OPTIMAL

        self._open_graduation_analysis(TAB_OPTIMAL)

    def _on_transmute(self):
        """打开毕业率分析对话框的「转律建议」页。"""
        from ..graduation_analysis import TAB_TRANSMUTE

        self._open_graduation_analysis(TAB_TRANSMUTE)

    def _on_affix_impact(self):
        """打开毕业率分析对话框的「培养建议」页。"""
        from ..graduation_analysis import TAB_SUGGESTION

        self._open_graduation_analysis(TAB_SUGGESTION)

    def _analysis_launcher(self):
        """分析对话框入口装配器（懒建）；装备页只交出自己掌握的协作者。"""
        launcher = getattr(self, "_launcher", None)
        if launcher is None:
            from ..analysis_launcher import GraduationAnalysisLauncher

            launcher = GraduationAnalysisLauncher(
                self._host, self,
                assumptions_source=lambda: self._combat_tab.assumptions(),
                level_threshold=self._get_level_threshold,
                affix_filter=self._get_affix_filter,
                display_params=lambda: self._display_params,
                apply_transmute=self._apply_transmute_result,
            )
            self._launcher = launcher
        return launcher

    def _open_graduation_analysis(self, initial_tab: int) -> None:
        if self._combat_tab is None:
            QMessageBox.warning(self, tr("提示"), tr("未找到角色详情面板"))
            return
        self._analysis_launcher().open(initial_tab)

    def _apply_transmute_result(self, result, *, user_name: str, plan_id: str) -> bool:
        """把转律建议写入公共装备；用户、方案或装备快照过期时拒绝。"""
        inv = self._require_inventory()
        if inv is None:
            return False
        if user_name != (self._host.active_user_name() or ""):
            raise ValueError(tr("用户已切换，请重新打开分析"))
        if plan_id != inv.active_plan_id:
            raise ValueError(tr("备战方案已切换，请重新计算"))
        moves = result.moves
        answer = QMessageBox.question(
            self, tr("应用转律目标"),
            tr("将写入 {count} 件装备的转律目标。目标属于公共装备，会影响所有"
               "引用这些装备的备战方案；此次结果只针对当前方案优化。继续？")
            .format(count=len(moves)),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        targets: dict[str, tuple[int, str, float] | None] = {
            status.fp: None for status in result.slots if status.fp
        }
        for move in moves:
            targets[move.fp] = (move.affix_index, move.to_name, move.to_value)
        inv.apply_transmute_targets(
            targets, expected_fps=set(result.equipped_fps))
        self._sync_inv(notify=True)
        logger.info("已写入 {} 件装备的转律目标", len(moves))
        return True
