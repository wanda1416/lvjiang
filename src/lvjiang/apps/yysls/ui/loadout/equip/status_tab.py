"""燕云「装备」Tab —— 装备背包统一视图。

顶部 8 个可点击槽位（固定 2×4），下方全部装备网格（可配置列数）。
点击槽位触发部位筛选，再次点击取消选中。
"""
from __future__ import annotations

import copy

from loguru import logger
from PyQt6.QtCore import Qt
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
    QVBoxLayout,
    QWidget,
)

from ......i18n import tr
from ...profile.tab import REFRESH_BTN_STYLE as _REFRESH_BTN_STYLE
from ...profile.tab import add_user_nav_buttons
from .cards import _CompactEquipCard, _SlotCard
from .mock_dialog import MockEquipDialog

# 状态展示行样式（与角色详情毕业率卡片一致）
_STATUS_NAME_STYLE = "font-size: 13px; color: palette(mid);"
_STATUS_VALUE_STYLE = "font-size: 15px; font-weight: 600;"
_STATUS_YELLOW_VALUE_STYLE = "font-size: 15px; font-weight: 600; color: #D97706;"

# 操作按钮样式
_PRIMARY_BTN_STYLE = (
    "QPushButton { background: #1976D2; color: white; "
    "border: none; border-radius: 4px; padding: 5px 10px; "
    "font-weight: 600; font-size: 12px; }"
    "QPushButton:hover { background: #1565C0; }"
)

# 顶部槽位布局（固定 2×4）
# (row, col, slot_key, display_name, filter_type)
# filter_type 对应 bag_items 的分组 key；主副武器共享 "weapon"
_SLOT_LAYOUT = [
    (0, 0, "main_weapon", tr("主武器"), "weapon"),
    (0, 1, "sub_weapon", tr("副武器"), "weapon"),
    (0, 2, "head", tr("冠胄"), "head"),
    (0, 3, "chest", tr("胸甲"), "chest"),
    (1, 0, "ring", tr("环"), "ring"),
    (1, 1, "pendant", tr("佩"), "pendant"),
    (1, 2, "leg", tr("胫甲"), "leg"),
    (1, 3, "wrist", tr("腕甲"), "wrist"),
]

# 部位显示名（bag_items 分组 key → 卡片标签）—— 使用 gc.get_group_to_part() 替代
# 保留此常量作为 fallback，实际运行时优先用 GameConfigManager
_GROUP_PART_LABELS: dict[str, str] = {}

_GRID_COLS = 4  # 默认值，实际从 settings.equip_display.grid_columns 读取


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


class EquipStatusTab(QWidget):
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
        self._display_params: dict = {}
        self._selected_slot: str | None = None
        self._slot_cards: dict[str, _SlotCard] = {}
        self._setup_ui()
        self._refresh_all()
        # 订阅装备变更信号（UI 操作与工作流写入均会触发），完整刷新展示
        self._host.equipment_changed.connect(self._refresh_all)
        self._host.graduation_updated.connect(self._update_status_row)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── Row A: 操作栏 ──
        self._action_widget = QWidget()
        action_row = QHBoxLayout(self._action_widget)
        action_row.setContentsMargins(0, 0, 0, 0)
        btn_refresh = QPushButton(tr("刷新"))
        btn_refresh.setFixedWidth(60)
        btn_refresh.setToolTip(tr("刷新装备"))
        btn_refresh.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_refresh.clicked.connect(self._on_refresh)
        action_row.addWidget(btn_refresh)
        add_user_nav_buttons(action_row, self._host)
        action_row.addStretch()

        # 最优组合
        btn_optimal = QPushButton(tr("计算最优组合"))
        btn_optimal.setToolTip(tr("搜索最优毕业率装备组合"))
        btn_optimal.setFixedWidth(80)
        btn_optimal.setStyleSheet(_PRIMARY_BTN_STYLE)
        btn_optimal.clicked.connect(self._on_optimal_combo)
        action_row.addWidget(btn_optimal)

        # 创建装备（原「模拟装备」，去掉菜单直接弹对话框）
        btn_create = QPushButton(tr("创建模拟装备"))
        btn_create.setToolTip(tr("创建模拟装备"))
        btn_create.setFixedWidth(80)
        btn_create.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_create.clicked.connect(self._on_mock_create)
        action_row.addWidget(btn_create)

        btn_clear_real = QPushButton(tr("清空真实装备"))
        btn_clear_real.setToolTip(tr("删除全部真实装备，保留模拟装备"))
        btn_clear_real.clicked.connect(self._on_clear_real)
        action_row.addWidget(btn_clear_real)

        # 导出数据
        btn_export = QPushButton(tr("导出数据"))
        btn_export.setToolTip(tr("导出为 leoq7 格式"))
        btn_export.setFixedWidth(80)
        btn_export.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_export.clicked.connect(self._on_export)
        action_row.addWidget(btn_export)
        layout.addWidget(self._action_widget)

        # ── Row B: 信息 + 筛选栏 ──
        self._info_widget = QWidget()
        info_row = QHBoxLayout(self._info_widget)
        info_row.setContentsMargins(8, 0, 8, 0)
        info_row.setSpacing(12)

        # 左区：DPS + 毕业率
        dps_lbl = QLabel(tr("DPS"))
        self._status_dps_name = dps_lbl
        dps_lbl.setStyleSheet(_STATUS_NAME_STYLE)
        info_row.addWidget(dps_lbl)
        self._status_dps = QLabel("--")
        self._status_dps.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._status_dps.setMinimumWidth(80)
        self._status_dps.setStyleSheet(_STATUS_VALUE_STYLE)
        info_row.addWidget(self._status_dps)

        info_row.addSpacing(16)

        rate_lbl = QLabel(tr("毕业率"))
        self._status_graduation_name = rate_lbl
        rate_lbl.setStyleSheet(_STATUS_NAME_STYLE)
        info_row.addWidget(rate_lbl)
        self._status_graduation = QLabel("--")
        self._status_graduation.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._status_graduation.setMinimumWidth(80)
        self._status_graduation.setStyleSheet(_STATUS_YELLOW_VALUE_STYLE)
        info_row.addWidget(self._status_graduation)

        info_row.addStretch()

        # 右区：筛选下拉框
        _filter_lbl_style = "font-size: 12px; color: #555;"

        # 排序
        lbl_sort = QLabel(tr("排序"))
        lbl_sort.setStyleSheet(_filter_lbl_style)
        info_row.addWidget(lbl_sort)
        self._sort_filter = QComboBox()
        self._sort_filter.addItem(tr("默认"), "default")
        self._sort_filter.addItem(tr("等级倒序"), "level_desc")
        self._sort_filter.addItem(tr("等级正序"), "level_asc")
        self._sort_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._sort_filter.currentIndexChanged.connect(self._on_filter_changed)
        info_row.addWidget(self._sort_filter)

        lbl_part = QLabel(tr("部位"))
        lbl_part.setStyleSheet(_filter_lbl_style)
        info_row.addWidget(lbl_part)
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
        self._type_filter.currentIndexChanged.connect(self._on_filter_changed)
        info_row.addWidget(self._type_filter)

        lbl_level = QLabel(tr("等级"))
        lbl_level.setStyleSheet(_filter_lbl_style)
        info_row.addWidget(lbl_level)
        self._level_filter = QComboBox()
        self._level_filter.addItem(tr("全部"), "all")
        from lvjiang.apps.yysls.config import get_game_config
        for lvl in sorted([c.level for c in get_game_config().get_level_configs()], reverse=True):
            self._level_filter.addItem(tr("≥{level}").format(level=lvl), str(lvl))
        self._level_filter.setMinimumWidth(70)
        self._level_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._level_filter.currentIndexChanged.connect(self._on_filter_changed)
        info_row.addWidget(self._level_filter)

        lbl_affix = QLabel(tr("词条"))
        lbl_affix.setStyleSheet(_filter_lbl_style)
        info_row.addWidget(lbl_affix)
        self._affix_filter = QComboBox()
        self._affix_filter.addItem(tr("全部"), "all")
        self._affix_filter.addItem(tr("已定音"), "dingyin")
        self._affix_filter.addItem(tr("满调律"), "full_tuning")
        self._affix_filter.setMinimumWidth(70)
        self._affix_filter.currentIndexChanged.connect(self._on_filter_changed)
        info_row.addWidget(self._affix_filter)

        # 数据来源筛选：全部/背包/模拟
        lbl_source = QLabel(tr("类型"))
        lbl_source.setStyleSheet(_filter_lbl_style)
        info_row.addWidget(lbl_source)
        self._source_filter = QComboBox()
        self._source_filter.addItem(tr("全部"), "all")
        self._source_filter.addItem(tr("背包"), "bag")
        self._source_filter.addItem(tr("模拟"), "mock")
        self._source_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._source_filter.currentIndexChanged.connect(self._on_filter_changed)
        info_row.addWidget(self._source_filter)

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
        self._slot_container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        slot_grid = QGridLayout(self._slot_container)
        slot_grid.setSpacing(8)
        slot_grid.setContentsMargins(8, 8, 8, 8)

        for row, col, slot_key, display_name, _filter_type in _SLOT_LAYOUT:
            card = _SlotCard(slot_key, display_name, _filter_type)
            slot_grid.addWidget(card, row, col)
            self._slot_cards[slot_key] = card

        wrapper_layout.addWidget(self._slot_container)

        # 分割线 —— 区分可点击槽位区与背包区
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(
            "background-color: #ccc; max-height: 1px; margin: 4px 8px;")
        sep.setFixedHeight(1)
        wrapper_layout.addWidget(sep)

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
        self._host.user_changed.connect(lambda _name: self._refresh_all())

        # 加载筛选配置
        self._load_filter_settings()

    def set_embedded_mode(self, embedded: bool = True) -> None:
        """Hide duplicated chrome when hosted by LoadoutPanel."""
        self._action_widget.setVisible(not embedded)
        for widget in (
            self._status_dps_name, self._status_dps,
            self._status_graduation_name, self._status_graduation,
        ):
            widget.setVisible(not embedded)

    # ── 筛选 ──

    def _load_filter_settings(self):
        """从用户级 loadout 存储加载筛选配置并设置下拉框"""
        filters = self._load_user_filter()
        # 屏蔽信号，避免初始化时触发 _on_filter_changed
        self._sort_filter.blockSignals(True)
        self._type_filter.blockSignals(True)
        self._level_filter.blockSignals(True)
        self._affix_filter.blockSignals(True)
        self._source_filter.blockSignals(True)
        try:
            # 排序
            sort_idx = self._sort_filter.findData(filters.get("sort", "default"))
            self._sort_filter.setCurrentIndex(sort_idx if sort_idx >= 0 else 0)
            # 类型筛选 → 联动槽位选中态
            type_data = filters.get("type", "all")
            type_idx = self._type_filter.findData(type_data)
            self._type_filter.setCurrentIndex(type_idx if type_idx >= 0 else 0)
            if type_data != "all":
                self._selected_slot = type_data
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
        finally:
            self._sort_filter.blockSignals(False)
            self._type_filter.blockSignals(False)
            self._level_filter.blockSignals(False)
            self._affix_filter.blockSignals(False)
            self._source_filter.blockSignals(False)

    def _save_filter_settings(self):
        """保存筛选配置到用户级 loadout 存储"""
        filters = {
            "sort": self._sort_filter.currentData(),
            "type": self._type_filter.currentData(),
            "level": self._level_filter.currentData(),
            "affix": self._affix_filter.currentData(),
            "source": self._source_filter.currentData(),
        }
        self._save_user_filter(filters)

    def _load_user_filter(self) -> dict:
        """从当前用户的 loadout 存储读取筛选配置"""
        if self._inv is not None:
            return self._inv._repo.get_ui_state("equip_filter")
        return {}

    def _save_user_filter(self, filters: dict) -> None:
        """将筛选配置写入当前用户的 loadout 存储"""
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
        self._rebuild_grid()

    def _get_level_threshold(self) -> int:
        """获取等级筛选阈值，0 表示不筛选"""
        level_str = self._level_filter.currentData()
        return int(level_str) if level_str != "all" else 0

    def _get_affix_filter(self) -> str:
        """获取词条筛选类型: all/dingyin/full_tuning"""
        return self._affix_filter.currentData()

    def _reset_filter_for_mock(self):
        """创建/复制模拟装备后，自动切换筛选以便新装备可见。

        将来源切换为「模拟」、清除部位和词条筛选，确保新创建的模拟装备
        不会被当前筛选条件隐藏。
        """
        self._source_filter.blockSignals(True)
        self._type_filter.blockSignals(True)
        self._affix_filter.blockSignals(True)
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
        finally:
            self._source_filter.blockSignals(False)
            self._type_filter.blockSignals(False)
            self._affix_filter.blockSignals(False)
        self._save_filter_settings()
        self._rebuild_grid()

    def _equip_passes_filter(self, equip: dict) -> bool:
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

        # 词条筛选
        affix_filter = self._get_affix_filter()
        if affix_filter == "all":
            return True
        elif affix_filter == "dingyin":
            # 有定音词条（包含满调律）
            dingyin = equip.get("dingyin")
            return bool(dingyin and dingyin.get("name"))
        elif affix_filter == "full_tuning":
            # 满调律：5 条非定音词条（affix_1 到 affix_5 都有）
            return all(equip.get(f"affix_{i}", {}).get("name") for i in range(1, 6))
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
            self._sync_inv()
        except Exception as e:
            logger.error(f"卸载装备失败: {e}")
            QMessageBox.critical(self, tr("卸载失败"), str(e))

    def _on_slot_edit(self, slot_key: str):
        """编辑槽位中的模拟装备"""
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
            # 携带旧指纹走「先写新、后清旧」链路，避免遗留同名孤儿装备
            old_fp = equip.get("_fp", "")
            inv.replace_equipped_mock(slot_key, old_fp, result)
            self._sync_inv()
        except Exception as e:
            logger.error(f"编辑槽位模拟装备失败: {e}")
            QMessageBox.critical(self, tr("编辑失败"), str(e))

    # ── 背包网格 ──

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

        # 确定筛选部位
        filter_type = None
        if self._selected_slot:
            for _, _, sk, _, ft in _SLOT_LAYOUT:
                if sk == self._selected_slot:
                    filter_type = ft
                    break

        # 收集装备（bag_items + mock_items）
        from ....config import get_game_config
        group_to_part = get_game_config().get_group_to_part()
        cards: list[tuple[dict, str, str, bool, bool]] = []
        standby_fps = self._inv.standby_plan_fps if self._inv is not None else set()
        # 当前方案已穿戴的指纹不再出现在下方未装备区
        equipped_fps = self._inv.active_plan_fps if self._inv is not None else set()
        # 来源筛选：all=全部, bag=仅背包, mock=仅模拟
        source_mode = self._source_filter.currentData() or "all"
        show_bag = source_mode in ("all", "bag")
        show_mock = source_mode in ("all", "mock")
        if show_bag:
            for group_key, items in self._bag_items.items():
                if filter_type is not None and group_key != filter_type:
                    continue
                part_label = group_to_part.get(group_key, group_key)
                for _fp, equip in items.items():
                    if _fp in equipped_fps:
                        continue
                    # 应用等级/词条筛选
                    if not self._equip_passes_filter(equip):
                        continue
                    cards.append((equip, part_label, group_key, False, _fp in standby_fps))

        # 合并模拟装备
        if show_mock:
            for group_key, items in self._mock_items.items():
                if filter_type is not None and group_key != filter_type:
                    continue
                part_label = group_to_part.get(group_key, group_key)
                for _fp, equip in items.items():
                    if _fp in equipped_fps:
                        continue
                    if not self._equip_passes_filter(equip):
                        continue
                    cards.append((equip, part_label, group_key, True, _fp in standby_fps))

        # 排序模式
        sort_mode = self._sort_filter.currentData() or "default"

        def _level_cap_sum(
            item: tuple[dict, str, str, bool, bool],
        ) -> tuple[int, int]:
            equip = item[0]
            level = equip.get("level") or 0
            if isinstance(level, str):
                try:
                    level = int(level)
                except (ValueError, TypeError):
                    level = 0
            cap_sum = 0
            for i in range(1, 6):
                affix = equip.get(f"affix_{i}")
                if affix and affix.get("name") and affix.get("cap_pct") is not None:
                    cap_sum += affix["cap_pct"]
            return level, cap_sum

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

        # 武器槽位严格分组：同类型武器归为一组，组内按等级降序
        if self._selected_slot in ("main_weapon", "sub_weapon"):
            weapon_type_for_slot = self._get_school_weapon_type(self._selected_slot)
            if weapon_type_for_slot and filter_type == "weapon":
                same_type = [c for c in ordered if c[0].get("type") == weapon_type_for_slot]
                other_type = [c for c in ordered if c[0].get("type") != weapon_type_for_slot]
                # 组内始终按等级降序
                def _weapon_sk(item):
                    lv, cs = _level_cap_sum(item)
                    return (-lv, -cs)
                same_slot_cards = sorted(same_type, key=_weapon_sk)
                other_cards = sorted(other_type, key=_weapon_sk)
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
            card.edit_requested.connect(self._on_mock_edit_requested)
            card.delete_requested.connect(self._on_delete_requested)
            card.copy_requested.connect(self._on_copy_requested)
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
            card.edit_requested.connect(self._on_mock_edit_requested)
            card.delete_requested.connect(self._on_delete_requested)
            card.copy_requested.connect(self._on_copy_requested)
            self._grid.addWidget(card, pos // cols, pos % cols)
            pos += 1

        if not cards:
            placeholder = QLabel(tr("暂无数据"))
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(
                "color: #999; font-size: 14px; padding: 40px;")
            self._grid.addWidget(placeholder, 0, 0, 1, cols)

    # ── 数据刷新 ──

    def _on_refresh(self):
        self._refresh_all()

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

    def _refresh_all(self):
        from lvjiang.core.config import load_equip_display

        self._display_params = load_equip_display()

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
        except Exception as e:
            logger.error(f"加载装备失败: {e}")
            self._inv = None
            self._equipped = {}
            self._bag_items = {}
            self._mock_items = {}

        self._refresh_slots()
        self._rebuild_grid()
        self._update_status_row()

    def _sync_inv(self) -> None:
        """从 EquipmentInventory 同步本地缓存并刷新 UI。"""
        if self._inv is None:
            return
        self._equipped = self._inv.equipped
        self._bag_items = self._inv.bag_items
        self._mock_items = self._inv.mock_items
        self._refresh_slots()
        self._rebuild_grid()

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
                card.set_equip(equip)
            else:
                card.set_empty()
            # 保持选中态
            card.set_selected(slot_key == self._selected_slot)

    # ── 装备操作 ──

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

        # 武器按流派武器类型路由：主副武器不同型时直接生效，
        # 与主副武学均不匹配的武器禁止装备（需另建对应流派方案）
        if len(target_slots) > 1:
            main_type = self._get_school_weapon_type("main_weapon") or ""
            sub_type = self._get_school_weapon_type("sub_weapon") or ""
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
            self._sync_inv()
            self._host.equipment_changed.emit()
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
            self._sync_inv()
            logger.info(f"已删除 {equip_name}")
        except Exception as e:
            logger.error(f"删除失败: {e}")
            QMessageBox.critical(self, tr("删除失败"), str(e))

    def _on_clear_real(self):
        """UI-owned bulk deletion; mock_ items are always preserved."""
        user_name = self._host.active_user_name()
        if not user_name:
            return
        reply = QMessageBox.question(
            self, tr("确认清空"),
            tr("确定删除全部真实装备吗？模拟装备会被保留，方案中的相关槽位会置空。"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from ....core.loadout import LoadoutRepository
        LoadoutRepository(user_name).delete_all_real()
        self._refresh_all()
        self._host.equipment_changed.emit()

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
            self._sync_inv()
            logger.info(f"已删除模拟装备 {equip_name}")
        except Exception as e:
            logger.error(f"删除失败: {e}")
            QMessageBox.critical(self, tr("删除失败"), str(e))

    def _on_mock_edit_requested(self, equip_data: dict, group_key: str):
        """处理模拟装备编辑请求"""
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

        # 确定新类型和分组（使用全局映射）
        from ....config import get_game_config
        new_type = result.get("type", "")
        new_group_key = get_game_config().get_type_to_group().get(new_type, group_key)

        inv = self._require_inventory()
        if inv is None:
            return
        try:
            inv.update_mock(group_key, old_fp, result, new_group_key)
            self._sync_inv()
            logger.info(f"已编辑模拟装备 {result.get('name', '未知')}")
        except Exception as e:
            logger.error(f"编辑模拟装备失败: {e}")
            QMessageBox.critical(self, tr("编辑失败"), str(e))

    def _get_school_weapon_type(self, slot_key: str) -> str | None:
        """从当前流派配置获取指定武器槽的武器类型（如 '剑'/'枪'。"""
        from ..combat.attrs_tab import CombatAttrsTab
        for child in self._host.findChildren(QWidget):
            if isinstance(child, CombatAttrsTab):
                ctx = child.get_graduation_context()
                if ctx and ctx.school:
                    from ....config import get_game_config
                    school_cfg = get_game_config().get_schools().get(ctx.school, {})
                    hand = "main" if slot_key == "main_weapon" else "sub"
                    return (school_cfg.get(hand) or {}).get("weapon")
                break
        return None

    def _get_current_school(self) -> str:
        """获取当前角色配置的流派名称"""
        from ..combat.attrs_tab import CombatAttrsTab
        for child in self._host.findChildren(QWidget):
            if isinstance(child, CombatAttrsTab):
                ctx = child.get_graduation_context()
                if ctx and ctx.school:
                    return ctx.school
                break
        return ""

    def _on_mock_create(self):
        """创建模拟装备"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("创建失败"), tr("没有激活的用户"))
            return

        dialog = MockEquipDialog(parent=self, default_school=self._get_current_school())
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
        """打开最优组合搜索对话框。"""
        # 从角色详情 Tab 读取流派/方案/基础属性
        from ..combat.attrs_tab import CombatAttrsTab
        combat_tab = None
        for child in self._host.findChildren(QWidget):
            if isinstance(child, CombatAttrsTab):
                combat_tab = child
                break
        if combat_tab is None:
            QMessageBox.warning(self, tr("提示"), tr("未找到角色详情面板"))
            return

        context = combat_tab.get_graduation_context()
        if context is None:
            QMessageBox.warning(
                self, tr("提示"), tr("请先在角色详情页选择流派和毕业率方案"))
            return

        from ..optimal_combo import OptimalComboDialog
        dlg = OptimalComboDialog(
            self._host, context.school, context.scheme, context.base_attrs,
            level_threshold=self._get_level_threshold(),
            affix_filter=self._get_affix_filter(),
            gongjue=context.gongjue,
            parent=self,
        )
        dlg.exec()
