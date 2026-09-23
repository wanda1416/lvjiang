"""燕云「装备」Tab —— 装备背包统一视图。

顶部 8 个可点击槽位（固定 2×4），下方全部装备网格（可配置列数）。
点击槽位触发部位筛选，再次点击取消选中。
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from loguru import logger
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.button_styles import (
    apply_button_style,
    apply_dialog_button_box_style,
)
from lvjiang.ui.user_toolbar import REFRESH_BTN_STYLE as _REFRESH_BTN_STYLE
from lvjiang.ui.user_toolbar import add_user_nav_buttons

from ......i18n import tr
from ....config.equipment_slots import SLOT_SPECS
from ....core.affix_cap import equip_affix_cap_pcts
from ....core.equip_parser.dingyin_parser import (
    DINGYIN_NORMAL,
    DINGYIN_TYPE_KEY,
    DINGYIN_TYPES,
    has_normal_dingyin,
)
from ....core.loadout.models import (
    EQUIPMENT_LAST_SEEN_AT,
    EQUIPMENT_UPDATED_AT,
)
from ...events import EQUIPMENT_CHANGED, get_event_hub
from .batch_copy import BatchCopyMixin
from .cards import _CompactEquipCard, _SlotCard
from .mock_dialog import MockEquipDialog

# 状态展示行样式（与角色详情毕业率卡片一致）
_STATUS_NAME_STYLE = "font-size: 13px; color: palette(mid);"
_STATUS_VALUE_STYLE = "font-size: 15px; font-weight: 600;"
_STATUS_YELLOW_VALUE_STYLE = "font-size: 15px; font-weight: 600; color: #D97706;"

# 右侧装备操作组统一样式
_ACTION_BTN_STYLE = (
    "QPushButton { border: 1px solid palette(highlight); "
    "color: palette(highlight); border-radius: 4px; padding: 5px 10px; "
    "font-weight: 600; font-size: 12px; }"
    "QPushButton:hover { background: palette(midlight); }"
)

_FILTER_TOGGLE_STYLE = (
    "QToolButton { background: transparent; color: palette(button-text); "
    "border: 1px solid palette(mid); border-radius: 2px; padding: 0; }"
    "QToolButton:hover { background-color: palette(midlight); }"
    "QToolButton:pressed { background-color: palette(mid); }"
)

# 顶部槽位布局（固定 2×4）
# (row, col, slot_key, display_name, filter_type)
# filter_type 对应 bag_items 的分组 key；主副武器共享 "weapon"
#: (行, 列, slot_key, 显示名, 背包筛选类型)；唯一定义见 config.equipment_slots
_SLOT_LAYOUT = [
    (spec.row, spec.col, spec.key, spec.label, spec.filter_type)
    for spec in SLOT_SPECS
]

# 部位显示名（bag_items 分组 key → 卡片标签）—— 使用 gc.get_group_to_part() 替代
# 保留此常量作为 fallback，实际运行时优先用 GameConfigManager
_GROUP_PART_LABELS: dict[str, str] = {}

_GRID_COLS = 4  # 默认值，实际从 settings.equip_display.grid_columns 读取


class _MultiSelectMenu(QMenu):
    """点击可勾选项后保持展开，便于一次选择多个目标用户。"""

    def mouseReleaseEvent(self, event):
        action = self.activeAction()
        if action is not None and action.isCheckable():
            action.trigger()
            event.accept()
            return
        super().mouseReleaseEvent(event)


def _fit_filter_combo(combo: QComboBox) -> int:
    """按主题边框、箭头和最长选项计算筛选框与弹出列表的宽度。"""
    from ...layout_helpers import fit_combo_to_contents

    return fit_combo_to_contents(combo)


class _FilteredDeleteDialog(QDialog):
    """确认批量删除，并清楚展示备战引用保护产生的实际结果。"""

    def __init__(
        self,
        filter_summary: str,
        candidate_fingerprints: set[str],
        referenced_fingerprints: set[str],
        parent=None,
        *,
        locked_fingerprints: set[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("确认删除"))
        self.setMinimumWidth(520)
        self._candidates = set(candidate_fingerprints)
        self._referenced = self._candidates & set(referenced_fingerprints)
        self._locked = self._candidates & set(locked_fingerprints or ())

        # 仅放大本确认框，保持应用全局字号和其他页面不变。
        font = self.font()
        point_size = font.pointSizeF()
        if point_size > 0:
            font.setPointSizeF(point_size + 2)
        elif font.pixelSize() > 0:
            font.setPixelSize(font.pixelSize() + 2)
        self.setFont(font)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        question = QLabel(tr("确定删除当前筛选出的背包装备吗？"))
        question.setStyleSheet("font-weight: 600;")
        layout.addWidget(question)

        summary = QLabel(f"{tr('当前筛选条件')}：\n{filter_summary}")
        summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(summary)

        # 类型行到提示之间明确空一行，避免限制说明被误当成类型的括注。
        layout.addSpacing(self.fontMetrics().height())
        self._source_hint_label = QLabel(tr(
            "只删除当前筛选出的背包装备，此处不支持删除模拟装备"))
        self._source_hint_label.setWordWrap(True)
        self._source_hint_label.setStyleSheet(
            "color: #D97706; font-weight: 600;")
        layout.addWidget(self._source_hint_label)

        self._preserve_checkbox = QCheckBox(tr("保留备战中的装备"))
        self._preserve_checkbox.setChecked(True)
        self._preserve_checkbox.setToolTip(tr(
            "保留当前用户任意现存备战方案正在引用的装备"))
        self._preserve_checkbox.toggled.connect(self._update_summary)
        layout.addWidget(self._preserve_checkbox)

        self._preserve_locked_checkbox = QCheckBox(tr("保留已锁定的装备"))
        self._preserve_locked_checkbox.setChecked(True)
        self._preserve_locked_checkbox.setToolTip(tr(
            "保留已标记为锁定的装备"))
        self._preserve_locked_checkbox.toggled.connect(self._update_summary)
        layout.addWidget(self._preserve_locked_checkbox)

        self._stats_label = QLabel()
        self._stats_label.setWordWrap(True)
        layout.addWidget(self._stats_label)

        self._reference_warning = QLabel()
        self._reference_warning.setWordWrap(True)
        self._reference_warning.setStyleSheet(
            "color: #c62828; font-weight: 600;")
        layout.addWidget(self._reference_warning)

        irreversible = QLabel(tr("此操作不可撤销。"))
        irreversible.setStyleSheet("color: #c62828; font-weight: 600;")
        layout.addWidget(irreversible)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        delete_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert isinstance(delete_button, QPushButton)
        self._delete_button = delete_button
        self._delete_button.setText(tr("删除"))
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        assert isinstance(cancel_button, QPushButton)
        cancel_button.setText(tr("取消"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        apply_dialog_button_box_style(buttons)
        apply_button_style(self._delete_button, variant="danger")
        layout.addWidget(buttons)
        self._update_summary()

    @property
    def preserve_referenced(self) -> bool:
        return self._preserve_checkbox.isChecked()

    @property
    def preserve_locked(self) -> bool:
        return self._preserve_locked_checkbox.isChecked()

    @property
    def effective_delete_count(self) -> int:
        remaining = set(self._candidates)
        if self.preserve_referenced:
            remaining.difference_update(self._referenced)
        if self.preserve_locked:
            remaining.difference_update(self._locked)
        return len(remaining)

    def _update_summary(self) -> None:
        referenced = len(self._referenced) if self.preserve_referenced else 0
        locked = len(self._locked) if self.preserve_locked else 0
        self._stats_label.setText(tr(
            "筛选命中 {matched} 件；备战保护 {referenced} 件；"
            "锁定保护 {locked} 件；"
            "实际将删除 {deleted} 件").format(
                matched=len(self._candidates),
                referenced=referenced,
                locked=locked,
                deleted=self.effective_delete_count,
            ))
        exposed = len(self._referenced)
        self._reference_warning.setText(
            tr("警告：{count} 件装备将从相关备战方案中移除。").format(
                count=exposed)
            if exposed and not self.preserve_referenced
            else "")
        self._reference_warning.setVisible(
            bool(exposed and not self.preserve_referenced))
        self._delete_button.setEnabled(self.effective_delete_count > 0)



def _affix_analysis_dependencies():
    """兼容入口：实现见 ``analysis_launcher.analysis_dependencies``。"""
    from ..analysis_launcher import analysis_dependencies

    return analysis_dependencies()


def _route_weapon_slot(eq_type: str, main_type: str, sub_type: str) -> str:
    """按流派武器类型路由武器槽位（纯函数，便于单测）。

    返回 "main_weapon"/"sub_weapon" 表示直接生效；"ask" 表示主副武器
    同型或流派未绑定，需手动选择；"reject" 表示武器与主副武学均不匹配。
    """
    if not main_type or not sub_type or main_type == sub_type:
        return "ask"
    if eq_type == main_type:
        return "main_weapon"
    if eq_type == sub_type:
        return "sub_weapon"
    return "reject"


# ── 主 Tab ──────────────────────────────────────────


class EquipStatusTab(BatchCopyMixin, QWidget):
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
        self._display_params: dict = {}
        self._selected_slot: str | None = None
        self._batch_copy_mode = False
        self._filters_collapsed = False
        self._batch_selected_fps: set[str] = set()
        self._batch_target_users: set[str] = set()
        self._slot_cards: dict[str, _SlotCard] = {}
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

        # 创建装备（原「模拟装备」，去掉菜单直接弹对话框）
        btn_create = QPushButton(tr("模拟装备"))
        btn_create.setToolTip(tr("创建模拟装备"))
        btn_create.setMinimumWidth(96)
        btn_create.setStyleSheet(_ACTION_BTN_STYLE)
        btn_create.clicked.connect(self._on_mock_create)
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
        self._status_graduation.setStyleSheet(_STATUS_YELLOW_VALUE_STYLE)
        metrics_row.addWidget(self._status_graduation)

        metrics_row.addStretch()
        info_layout.addLayout(metrics_row)

        # 排序/来源/操作与具体筛选条件分成两行，窄窗口下仍保持完整可读。
        primary_filter_row = QHBoxLayout()
        primary_filter_row.setSpacing(8)

        # 右区：筛选下拉框
        _filter_lbl_style = "font-size: 12px; color: palette(mid);"

        # 排序
        lbl_sort = QLabel(tr("排序"))
        lbl_sort.setStyleSheet(_filter_lbl_style)
        primary_filter_row.addWidget(lbl_sort)
        self._sort_filter = QComboBox()
        self._sort_filter.addItem(tr("默认"), "default")
        self._sort_filter.addItem(tr("等级倒序"), "level_desc")
        self._sort_filter.addItem(tr("等级正序"), "level_asc")
        self._sort_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        _fit_filter_combo(self._sort_filter)
        self._sort_filter.currentIndexChanged.connect(self._on_filter_changed)
        primary_filter_row.addWidget(self._sort_filter)

        lbl_source = QLabel(tr("类型"))
        lbl_source.setStyleSheet(_filter_lbl_style)
        primary_filter_row.addWidget(lbl_source)
        self._source_filter = QComboBox()
        self._source_filter.addItem(tr("全部"), "all")
        self._source_filter.addItem(tr("背包"), "bag")
        self._source_filter.addItem(tr("模拟"), "mock")
        self._source_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        _fit_filter_combo(self._source_filter)
        self._source_filter.currentIndexChanged.connect(self._on_filter_changed)
        primary_filter_row.addWidget(self._source_filter)
        primary_filter_row.addStretch()

        self._btn_delete_filtered = QPushButton(tr("删除筛选装备"))
        self._btn_delete_filtered.setToolTip(tr(
            "只删除当前筛选出的背包装备，此处不支持删除模拟装备"))
        self._btn_delete_filtered.setStyleSheet(_ACTION_BTN_STYLE)
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
        self._type_filter = QComboBox()
        self._type_filter.addItem(tr("全部"), "all")
        for sk, dn, _ in [
            ("main_weapon", tr("主武器"), "weapon"),
            ("sub_weapon", tr("副武器"), "weapon"),
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
        self._type_filter.currentIndexChanged.connect(self._on_filter_changed)
        advanced_filter_row.addWidget(self._type_filter)

        lbl_quality = QLabel(tr("品阶"))
        lbl_quality.setStyleSheet(_filter_lbl_style)
        advanced_filter_row.addWidget(lbl_quality)
        self._quality_filter = QComboBox()
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
        self._level_filter = QComboBox()
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
        self._affix_filter = QComboBox()
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
        self._status_filter = QComboBox()
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
        self._scan_time_filter = QComboBox()
        self._scan_time_filter.addItem(tr("全部"), "all")
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
            "background-color: #ccc; max-height: 1px; margin: 4px 8px;")
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

        # 订阅用户切换
        self._host.user_changed.connect(self._on_user_changed)

    def set_embedded_mode(self, embedded: bool = True) -> None:
        """Hide duplicated chrome when hosted by LoadoutPanel."""
        self._action_widget.setVisible(not embedded)
        for widget in (
            self._status_dps_name, self._status_dps,
            self._status_graduation_name, self._status_graduation,
        ):
            widget.setVisible(not embedded)

    # ── 筛选 ──

    def _on_user_changed(self, _name: str) -> None:
        self._exit_batch_copy_mode(rebuild=False)
        self._refresh_all()
        self._load_filter_settings()
        self._rebuild_grid()

    def _load_filter_settings(self):
        """按当前用户加载界面筛选状态并设置下拉框"""
        filters = self._load_user_filter()
        # 屏蔽信号，避免初始化时触发 _on_filter_changed
        self._sort_filter.blockSignals(True)
        self._type_filter.blockSignals(True)
        self._level_filter.blockSignals(True)
        self._affix_filter.blockSignals(True)
        self._source_filter.blockSignals(True)
        self._quality_filter.blockSignals(True)
        self._status_filter.blockSignals(True)
        self._scan_time_filter.blockSignals(True)
        try:
            # 排序
            sort_idx = self._sort_filter.findData(filters.get("sort", "default"))
            self._sort_filter.setCurrentIndex(sort_idx if sort_idx >= 0 else 0)
            # 类型筛选 → 联动槽位选中态
            type_data = filters.get("type", "all")
            type_idx = self._type_filter.findData(type_data)
            self._type_filter.setCurrentIndex(type_idx if type_idx >= 0 else 0)
            self._selected_slot = type_data if type_data != "all" else None
            for key, card in self._slot_cards.items():
                card.set_selected(key == self._selected_slot)
            # 等级筛选
            level_idx = self._level_filter.findData(filters.get("level", "all"))
            self._level_filter.setCurrentIndex(level_idx if level_idx >= 0 else 0)
            # 词条筛选
            affix_idx = self._affix_filter.findData(filters.get("affix", "all"))
            self._affix_filter.setCurrentIndex(affix_idx if affix_idx >= 0 else 0)
            # 来源筛选
            source_idx = self._source_filter.findData(filters.get("source", "all"))
            self._source_filter.setCurrentIndex(source_idx if source_idx >= 0 else 0)
            # 品阶筛选
            quality_idx = self._quality_filter.findData(
                filters.get("quality", "all"))
            self._quality_filter.setCurrentIndex(
                quality_idx if quality_idx >= 0 else 0)
            # 备战方案引用状态筛选
            status_idx = self._status_filter.findData(
                filters.get("status", "all"))
            self._status_filter.setCurrentIndex(
                status_idx if status_idx >= 0 else 0)
            scan_time_idx = self._scan_time_filter.findData(
                filters.get("scan_time", "all"))
            self._scan_time_filter.setCurrentIndex(
                scan_time_idx if scan_time_idx >= 0 else 0)
        finally:
            self._sort_filter.blockSignals(False)
            self._type_filter.blockSignals(False)
            self._level_filter.blockSignals(False)
            self._affix_filter.blockSignals(False)
            self._source_filter.blockSignals(False)
            self._quality_filter.blockSignals(False)
            self._status_filter.blockSignals(False)
            self._scan_time_filter.blockSignals(False)
        self._set_advanced_filters_collapsed(
            bool(filters.get("filters_collapsed", False)))
        self._update_source_actions()

    def _save_filter_settings(self):
        """按当前用户保存界面筛选状态"""
        filters = {
            "sort": self._sort_filter.currentData(),
            "type": self._type_filter.currentData(),
            "level": self._level_filter.currentData(),
            "affix": self._affix_filter.currentData(),
            "source": self._source_filter.currentData(),
            "quality": self._quality_filter.currentData(),
            "status": self._status_filter.currentData(),
            "scan_time": self._scan_time_filter.currentData(),
            "filters_collapsed": self._filters_collapsed,
        }
        self._save_user_filter(filters)

    def _load_user_filter(self) -> dict:
        """读取当前用户的筛选状态。"""
        if self._inv is not None:
            return self._inv._repo.get_ui_state("equip_filter")
        return {}

    def _save_user_filter(self, filters: dict) -> None:
        """保存当前用户的界面偏好，不修改任务使用的装备数据"""
        if self._inv is not None:
            self._inv._repo.set_ui_state("equip_filter", filters)

    def _on_filter_changed(self):
        """筛选下拉框变化时触发"""
        # 类型下拉框变化 → 联动槽位选中态
        type_data = self._type_filter.currentData()
        new_slot = type_data if type_data != "all" else None
        if new_slot != self._selected_slot:
            self._selected_slot = new_slot
            for key, card in self._slot_cards.items():
                card.set_selected(key == self._selected_slot)
        self._save_filter_settings()
        self._update_source_actions()
        self._rebuild_grid()

    def _toggle_advanced_filters(self) -> None:
        self._set_advanced_filters_collapsed(
            not self._filters_collapsed)
        self._save_filter_settings()

    def _set_advanced_filters_collapsed(self, collapsed: bool) -> None:
        self._filters_collapsed = collapsed
        self._advanced_filter_widget.setVisible(not collapsed)
        action_text = (
            tr("展开筛选条件") if collapsed
            else tr("收起筛选条件"))
        icon_type = (
            QStyle.StandardPixmap.SP_ArrowRight if collapsed
            else QStyle.StandardPixmap.SP_ArrowLeft)
        self._filter_collapse_button.setText("")
        self._filter_collapse_button.setIcon(
            self._filter_collapse_button.style().standardIcon(icon_type))
        self._filter_collapse_button.setToolTip(action_text)
        self._filter_collapse_button.setAccessibleName(action_text)

    def _update_source_actions(self) -> None:
        is_mock = (self._source_filter.currentData() or "all") == "mock"
        self._btn_batch_copy.setVisible(is_mock)
        self._btn_delete_filtered.setVisible(not is_mock)

    def _get_level_threshold(self) -> int:
        """获取等级筛选阈值，0 表示不筛选"""
        level_str = self._level_filter.currentData()
        return int(level_str) if level_str != "all" else 0

    def _get_affix_filter(self) -> str:
        """获取词条筛选类型。"""
        return self._affix_filter.currentData()

    @staticmethod
    def _effective_scan_time(equip: dict) -> datetime | None:
        """读取扫描时间；历史装备缺失独立字段时回退更新时间。"""
        value = equip.get(EQUIPMENT_LAST_SEEN_AT) or equip.get(
            EQUIPMENT_UPDATED_AT)
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _passes_scan_time_filter(
        self,
        equip: dict,
        *,
        is_mock: bool,
        now: datetime | None = None,
    ) -> bool:
        """模拟装备不受扫描时间约束；真实装备按最后观察时间筛选。"""
        if is_mock:
            return True
        mode = str(self._scan_time_filter.currentData() or "all")
        if mode == "all":
            return True
        scanned_at = EquipStatusTab._effective_scan_time(equip)
        if scanned_at is None:
            return True
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return scanned_at < current.astimezone(timezone.utc) - timedelta(
            days=int(mode))

    def _scan_time_delete_constraint(self) -> str | None:
        """把界面筛选转换成仓储层的原子复核条件。"""
        mode = str(self._scan_time_filter.currentData() or "all")
        if mode == "all":
            return None
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(mode))
        return cutoff.isoformat(timespec="milliseconds")

    def _reset_filter_for_mock(self):
        """创建/复制模拟装备后，自动切换筛选以便新装备可见。

        将来源切换为「模拟」，并清除部位、品阶、词条和状态筛选，确保新建
        装备不会被当前筛选条件隐藏。
        """
        self._source_filter.blockSignals(True)
        self._type_filter.blockSignals(True)
        self._quality_filter.blockSignals(True)
        self._affix_filter.blockSignals(True)
        self._status_filter.blockSignals(True)
        try:
            # 来源切换到「模拟」
            mock_idx = self._source_filter.findData("mock")
            if mock_idx >= 0:
                self._source_filter.setCurrentIndex(mock_idx)
            # 部位筛选清除（回到「全部」）
            all_type_idx = self._type_filter.findData("all")
            if all_type_idx >= 0 and self._type_filter.currentData() != "all":
                self._type_filter.setCurrentIndex(all_type_idx)
                self._selected_slot = None
                for _key, card in self._slot_cards.items():
                    card.set_selected(False)
            # 词条筛选清除（回到「全部」）
            all_affix_idx = self._affix_filter.findData("all")
            if all_affix_idx >= 0 and self._affix_filter.currentData() != "all":
                self._affix_filter.setCurrentIndex(all_affix_idx)
            for combo in (self._quality_filter, self._status_filter):
                all_idx = combo.findData("all")
                if all_idx >= 0:
                    combo.setCurrentIndex(all_idx)
        finally:
            self._source_filter.blockSignals(False)
            self._type_filter.blockSignals(False)
            self._quality_filter.blockSignals(False)
            self._affix_filter.blockSignals(False)
            self._status_filter.blockSignals(False)
        self._save_filter_settings()
        self._update_source_actions()
        self._rebuild_grid()

    def _equip_passes_filter(
        self,
        equip: dict,
        *,
        is_referenced: bool = False,
    ) -> bool:
        """检查装备是否通过筛选条件"""
        # 等级筛选
        level_threshold = self._get_level_threshold()
        if level_threshold > 0:
            equip_level = equip.get("level") or 0
            if isinstance(equip_level, str):
                try:
                    equip_level = int(equip_level)
                except (ValueError, TypeError):
                    equip_level = 0
            if equip_level < level_threshold:
                return False

        # 品阶筛选：白装按产品语义兜底为一切非金、非紫装备。
        quality_filter = self._quality_filter.currentData() or "all"
        quality = str(equip.get("quality") or "")
        if quality_filter in ("gold", "purple") and quality != quality_filter:
            return False
        if quality_filter == "other" and quality in ("gold", "purple"):
            return False

        # 词条筛选
        affix_filter = self._get_affix_filter()
        if affix_filter == "dingyin":
            # 只认普通定音，与毕业率候选池同一口径：止戈目前不算用户要的定音。
            if not has_normal_dingyin(equip):
                return False
        elif affix_filter == "full_tuning":
            # 满调律：5 条非定音词条（affix_1 到 affix_5 都有）
            if not all(equip.get(f"affix_{i}", {}).get("name")
                       for i in range(1, 6)):
                return False
        elif affix_filter == "not_full_tuning":
            affix_count = sum(
                bool(equip.get(f"affix_{i}", {}).get("name"))
                for i in range(1, 6)
            )
            if affix_count > 4:
                return False

        # 状态按是否被任意备战方案引用判断。
        status_filter = self._status_filter.currentData() or "all"
        if status_filter == "referenced" and not is_referenced:
            return False
        if status_filter == "unreferenced" and is_referenced:
            return False
        return True

    # ── 槽位点击 ──

    def _on_slot_clicked(self, slot_key: str):
        if self._selected_slot == slot_key:
            # 再次点击同一槽位 → 取消选中
            self._selected_slot = None
        else:
            self._selected_slot = slot_key

        # 更新所有槽位的选中态
        for key, card in self._slot_cards.items():
            card.set_selected(key == self._selected_slot)

        # 联动类型下拉框
        self._type_filter.blockSignals(True)
        try:
            idx = self._type_filter.findData(slot_key if self._selected_slot else "all")
            self._type_filter.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._type_filter.blockSignals(False)

        self._save_filter_settings()
        self._rebuild_grid()

    def _on_slot_unequip(self, slot_key: str):
        """卸载槽位装备：从 equipped 移回 bag_items 或 mock_items"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("卸载失败"), tr("没有激活的用户"))
            return
        equip = self._equipped.get(slot_key)
        if not equip:
            return
        inv = self._require_inventory()
        if inv is None:
            return
        try:
            inv.unequip(slot_key)
            self._sync_inv(notify=True)
        except Exception as e:
            logger.error(f"卸载装备失败: {e}")
            QMessageBox.critical(self, tr("卸载失败"), str(e))

    def _on_slot_edit(self, slot_key: str):
        """编辑槽位模拟装备，或记录扫描装备的受限养成。"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("编辑失败"), tr("没有激活的用户"))
            return
        equip = self._equipped.get(slot_key)
        if not equip:
            return
        dialog = MockEquipDialog(equip, parent=self, default_school=self._get_current_school())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.get_result()
        if not result:
            return
        inv = self._require_inventory()
        if inv is None:
            return
        try:
            old_fp = equip.get("_fp", "")
            is_mock = bool((equip.get("_extra") or {}).get("is_mock"))
            if is_mock:
                # 携带旧指纹走「先写新、后清旧」链路，避免遗留孤儿装备。
                inv.replace_equipped_mock(slot_key, old_fp, result)
            else:
                # 真实装备变化会改变指纹，仓储层负责迁移全部方案引用。
                inv.update_real_development(old_fp, result)
            self._sync_inv(notify=True)
        except Exception as e:
            logger.error(f"编辑或养成槽位装备失败: {e}")
            QMessageBox.critical(self, tr("编辑失败"), str(e))

    # ── 背包网格 ──

    def _collect_filtered_cards(
        self,
    ) -> list[tuple[dict, str, str, bool, bool]]:
        """返回当前筛选后的未穿戴装备，作为展示与批量删除的唯一口径。"""
        filter_type = None
        batch_copy = getattr(self, "_batch_copy_mode", False)
        if self._selected_slot and not batch_copy:
            for _, _, slot_key, _, group_key in _SLOT_LAYOUT:
                if slot_key == self._selected_slot:
                    filter_type = group_key
                    break

        from ....config import get_game_config
        group_to_part = get_game_config().get_group_to_part()
        referenced_fps = (
            self._inv.referenced_plan_fps if self._inv is not None else set())
        # 当前方案已穿戴装备永远不进入下方列表，也永远不进入筛选删除集合。
        equipped_fps = (
            self._inv.active_plan_fps if self._inv is not None else set())
        source_mode = self._source_filter.currentData() or "all"
        cards: list[tuple[dict, str, str, bool, bool]] = []

        def append_grouped_items(
            grouped: dict,
            *,
            is_mock: bool,
        ) -> None:
            for group_key, items in grouped.items():
                if filter_type is not None and group_key != filter_type:
                    continue
                part_label = group_to_part.get(group_key, group_key)
                for fp, equip in items.items():
                    if fp in equipped_fps and not batch_copy:
                        continue
                    is_referenced = fp in referenced_fps
                    if (not batch_copy
                            and not self._equip_passes_filter(
                        equip, is_referenced=is_referenced,
                    )):
                        continue
                    if (not batch_copy
                            and not self._passes_scan_time_filter(
                                equip, is_mock=is_mock)):
                        continue
                    cards.append((
                        equip, part_label, group_key, is_mock, is_referenced,
                    ))

        if source_mode in ("all", "bag") and not batch_copy:
            append_grouped_items(self._bag_items, is_mock=False)
        if source_mode in ("all", "mock"):
            append_grouped_items(self._mock_items, is_mock=True)
        return cards

    def _rebuild_grid(self):
        cols = self._display_params.get("grid_columns", _GRID_COLS)

        # 清空
        while self._grid.count() > 0:
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for c in range(self._grid.columnCount()):
            self._grid.setColumnStretch(c, 0)
        for c in range(cols):
            self._grid.setColumnStretch(c, 1)

        cards = self._collect_filtered_cards()

        # 排序模式
        sort_mode = self._sort_filter.currentData() or "default"

        def _level_cap_sum(
            item: tuple[dict, str, str, bool, bool],
        ) -> tuple[int, float]:
            equip = item[0]
            level = equip.get("level") or 0
            if isinstance(level, str):
                try:
                    level = int(level)
                except (ValueError, TypeError):
                    level = 0
            return level, sum(equip_affix_cap_pcts(equip))

        # 排序 + 武器分组逻辑
        if sort_mode == "level_desc":
            def _sk(item):
                lv, cs = _level_cap_sum(item)
                return (-lv, -cs)
            ordered = sorted(cards, key=_sk)
        elif sort_mode == "level_asc":
            def _sk(item):
                lv, cs = _level_cap_sum(item)
                return (lv, -cs)
            ordered = sorted(cards, key=_sk)
        else:
            # 默认：保持 bag_items → mock_items 原始顺序
            ordered = cards

        # 武器槽位严格分组：同类型武器归为一组，组内保持排序顺序
        if self._selected_slot in ("main_weapon", "sub_weapon"):
            weapon_type_for_slot = self._get_plan_weapon_type(self._selected_slot)
            if weapon_type_for_slot:
                same_slot_cards = [c for c in ordered if c[0].get("type") == weapon_type_for_slot]
                other_cards = [c for c in ordered if c[0].get("type") != weapon_type_for_slot]
            else:
                same_slot_cards = ordered
                other_cards = []
        else:
            same_slot_cards = ordered
            other_cards = []

        # 填充
        pos = 0
        for equip, part_label, group_key, is_mock, is_loadout in same_slot_cards:
            card = _CompactEquipCard(self._display_params)
            card.set_equip(
                equip, part_label, group_key,
                is_mock=is_mock, is_loadout=is_loadout,
            )
            card.equip_requested.connect(self._on_equip_requested)
            card.edit_requested.connect(self._on_edit_requested)
            card.delete_requested.connect(self._on_delete_requested)
            card.copy_requested.connect(self._on_copy_requested)
            card.lock_requested.connect(self._on_lock_requested)
            card.properties_requested.connect(self._on_properties_requested)
            if self._batch_copy_mode:
                fp = str(equip.get("_fp") or "")
                card.set_selection_mode(
                    True, selected=fp in self._batch_selected_fps)
                card.selection_changed.connect(self._toggle_batch_card)
            self._grid.addWidget(card, pos // cols, pos % cols)
            pos += 1

        # 同槽位装备未填满行时，跳到下一行再放其他装备
        if other_cards and pos % cols != 0:
            pos = (pos // cols + 1) * cols

        for equip, part_label, group_key, is_mock, is_loadout in other_cards:
            card = _CompactEquipCard(self._display_params)
            card.set_equip(
                equip, part_label, group_key,
                is_mock=is_mock, is_loadout=is_loadout,
            )
            card.equip_requested.connect(self._on_equip_requested)
            card.edit_requested.connect(self._on_edit_requested)
            card.delete_requested.connect(self._on_delete_requested)
            card.copy_requested.connect(self._on_copy_requested)
            card.lock_requested.connect(self._on_lock_requested)
            card.properties_requested.connect(self._on_properties_requested)
            if self._batch_copy_mode:
                fp = str(equip.get("_fp") or "")
                card.set_selection_mode(
                    True, selected=fp in self._batch_selected_fps)
                card.selection_changed.connect(self._toggle_batch_card)
            self._grid.addWidget(card, pos // cols, pos % cols)
            pos += 1

        if not cards:
            placeholder = QLabel(tr("暂无数据"))
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(
                "color: palette(mid); font-size: 14px; padding: 40px;")
            self._grid.addWidget(placeholder, 0, 0, 1, cols)

    # ── 数据刷新 ──

    def _on_refresh(self):
        self._refresh_all()
        get_event_hub(self._host).publish(EQUIPMENT_CHANGED)

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

    def _reload_display_params(self) -> None:
        """刷新前重读装备卡片展示参数：设置页改过字号/列数，刷新即生效。"""
        from ....config.equip_display import load_equip_display

        self._display_params = load_equip_display()

    def refresh_from(self, inventory) -> None:
        """用面板本轮已加载的仓储快照刷新，不再自己重新读盘。"""
        self._reload_display_params()
        self._inv = inventory
        self._sync_inv()
        self._update_status_row()

    def _refresh_all(self):
        self._reload_display_params()

        user_name = self._host.active_user_name()
        if not user_name:
            self._inv = None
            self._equipped = {}
            self._bag_items = {}
            self._mock_items = {}
            self._refresh_slots()
            self._rebuild_grid()
            return

        try:
            from ....core.combat.equipment import EquipmentInventory
            self._inv = EquipmentInventory(user_name)
            self._sync_inv()
            self._update_status_row()
            return
        except Exception as e:
            logger.error(f"加载装备失败: {e}")
            self._inv = None
            self._equipped = {}
            self._bag_items = {}
            self._mock_items = {}

        self._refresh_slots()
        self._rebuild_grid()
        self._update_status_row()

    def _sync_inv(self, *, notify: bool = False) -> None:
        """从 EquipmentInventory 同步本地缓存并刷新 UI。"""
        if self._inv is None:
            return
        self._equipped = self._inv.equipped
        self._bag_items = self._inv.bag_items
        self._mock_items = self._inv.mock_items
        self._refresh_slots()
        self._rebuild_grid()
        if notify:
            get_event_hub(self._host).publish(EQUIPMENT_CHANGED)

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
        for _row, _col, slot_key, _display_name, _filter_type in _SLOT_LAYOUT:
            card = self._slot_cards[slot_key]
            card._name_fs = dp.get("name_font_size", 13)
            card._level_fs = dp.get("level_font_size", 12)
            card._affix_fs = dp.get("affix_font_size", 11)
            card._card_h = dp.get("card_min_height", 160)
            card.setFixedHeight(card._card_h)

            equip = self._equipped.get(slot_key)
            if equip:
                card.set_equip(
                    equip, dingyin_kind=self._plan_dingyin_kind(slot_key))
            else:
                card.set_empty()
            # 保持选中态
            card.set_selected(slot_key == self._selected_slot)

    # ── 装备操作 ──

    def _on_properties_requested(self, equip_data: dict) -> None:
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

        slot_key = self._slot_of_equipped(equip_data)
        fp = str(equip_data.get("_fp") or "")
        loaded_inv = getattr(self, "_inv", None)
        state = getattr(loaded_inv, "state", None)
        referenced_plans = (
            state.referencing_plan_names(fp)
            if state is not None and fp else [])

        def switch_dingyin(kind: str) -> bool:
            """装备栏里切的是这套方案的选择，背包里切的是装备自身的展示态。

            两个入口各写各的：在装备栏切音不该改掉背包里这件装备的默认展示，
            反过来也一样。
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

        _show_equipment_properties(
            self.window(), equip_data, cooldown_changed=update_cooldown,
            dingyin_changed=switch_dingyin,
            dingyin_kind=(self._plan_dingyin_kind(slot_key)
                          if slot_key else ""),
            referenced_plans=referenced_plans)

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

    def _slot_of_equipped(self, equip_data: dict) -> str:
        """这件装备正占着当前方案的哪个槽位；不在装备栏里返回空串。"""
        fp = str(equip_data.get("_fp") or "")
        if not fp:
            return ""
        return next(
            (key for key, value in self._equipped.items()
             if str((value or {}).get("_fp") or "") == fp),
            "")

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
                    self, tr("选择槽位"), tr("请选择要穿戴到的槽位:"),
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
            f"{tr('类型')}：{self._deletion_source_summary()}",
        ))

    def _deletion_source_summary(self) -> str:
        return tr("背包")

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
        from ....core.loadout import LoadoutRepository
        from .chengyin_merge_dialog import (
            ChengyinMergeDialog,
            load_user_chengyin_candidates,
        )

        candidates = load_user_chengyin_candidates(
            self._host.user_manager.list_users(),
            get_game_config().get_level_configs(),
        )
        dialog = ChengyinMergeDialog(
            candidates, self._display_params, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.selected_candidates()
        replacements_by_user: dict[str, dict[str, str]] = {}
        for entry in selected:
            candidate = entry.candidate
            replacements = replacements_by_user.setdefault(entry.username, {})
            existing = replacements.get(candidate.old_fp)
            if existing is not None and existing != candidate.new_fp:
                QMessageBox.warning(
                    self, tr("合并失败"),
                    tr("选中项包含冲突关系，请确保同一旧装备只合并到一个新版本。"))
                return
            replacements[candidate.old_fp] = candidate.new_fp
        try:
            for username, replacements in replacements_by_user.items():
                LoadoutRepository(username).merge_items(replacements)
        except Exception as exc:
            logger.exception("承音装备合并失败")
            QMessageBox.critical(self, tr("合并失败"), str(exc))
            return
        self._refresh_all()
        get_event_hub(self._host).publish(EQUIPMENT_CHANGED)
        QMessageBox.information(
            self, tr("合并完成"),
            tr("已合并 {count} 组装备。此操作保留右侧版本，并迁移所有备战方案引用。")
            .format(count=len(selected)))

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
            with open(path, "w", encoding="utf-8") as f:
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
