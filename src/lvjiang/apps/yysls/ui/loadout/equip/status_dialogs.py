"""装备状态页的筛选菜单、批量删除确认框与槽位路由辅助。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
)

from lvjiang.ui.widgets.button_styles import (
    apply_button_style,
    apply_dialog_button_box_style,
)
from lvjiang.ui.widgets.combo_box import AutoWidthComboBox
from lvjiang.ui.widgets.theme import current_tokens

from ......i18n import tr
from ....config.equipment_slots import SLOT_SPECS
from ...theme import yellow_stat_color

# 状态展示行样式（与角色详情毕业率卡片一致）
_STATUS_NAME_STYLE = "font-size: 13px; color: palette(mid);"
_STATUS_VALUE_STYLE = "font-size: 15px; font-weight: 600;"

def _status_yellow_value_style() -> str:
    return f"font-size: 15px; font-weight: 600; color: {yellow_stat_color()};"

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


def _fit_filter_combo(combo: AutoWidthComboBox) -> int:
    """按主题边框、箭头和最长选项计算筛选框与弹出列表的宽度。"""
    from lvjiang.ui.widgets.layout_helpers import fit_combo_to_contents

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
        self.setWindowTitle(tr("删除背包装备确认"))
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
            f"color: {yellow_stat_color()}; font-weight: 600;")
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

        danger = current_tokens().danger
        self._reference_warning = QLabel()
        self._reference_warning.setWordWrap(True)
        self._reference_warning.setStyleSheet(
            f"color: {danger}; font-weight: 600;")
        layout.addWidget(self._reference_warning)

        irreversible = QLabel(tr("此操作不可撤销。"))
        irreversible.setStyleSheet(f"color: {danger}; font-weight: 600;")
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


