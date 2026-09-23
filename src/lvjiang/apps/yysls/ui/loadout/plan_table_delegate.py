"""方案管理表格的逐列编辑。

改一个字段不该要求打开对话框重填一整套方案，所以每列在单元格里就地编辑：
选中行后再单击进入编辑，选完即提交。

联动发生在**提交时**而不是控件信号里：改完流派就按流派回填武学、重算玩法
候选，然后整行重读一次。规则本身来自 ``core.loadout.plan_fields``，与新建
对话框同一份；这里只负责把它接到表格上。
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QModelIndex
from PyQt6.QtWidgets import (
    QComboBox,
    QLineEdit,
    QStyledItemDelegate,
    QWidget,
)

from .....i18n import tr
from ...core.loadout import (
    LoadoutPlan,
    playstyle_options,
    resolve_school,
    school_arts,
)
from ...core.loadout.models import COMBAT_TYPE_PVE, COMBAT_TYPE_PVP
from ..domain_labels import combat_type_label

#: 列序与表头一致；只有这里列出的列可以就地编辑。
COL_NAME = 0
COL_SCHOOL = 1
COL_MAIN_ART = 2
COL_SUB_ART = 3
COL_PLAYSTYLE = 4
COL_COMBAT = 5

#: 自定义（即未绑定任何流派）在流派下拉里的稳定值。
CUSTOM_SCHOOL = ""


class PlanFieldDelegate(QStyledItemDelegate):
    """按列给出编辑器，并把结果交给宿主提交。

    ``plan_of`` 按行取当前方案，``commit`` 收 ``(plan, 列, 新值)``。委托不碰
    仓储，也不知道用户是谁——那些属于对话框。
    """

    def __init__(
        self,
        game_config,
        plan_of: Callable[[int], LoadoutPlan | None],
        commit: Callable[[LoadoutPlan, int, str], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._game_config = game_config
        self._plan_of = plan_of
        self._commit = commit

    # ── 编辑器 ──

    def createEditor(self, parent: QWidget | None, option,  # noqa: N802
                     index: QModelIndex) -> QWidget:
        plan = self._plan_of(index.row())
        column = index.column()
        if plan is None:
            return QWidget(parent)
        if column == COL_NAME:
            editor = QLineEdit(parent)
            editor.setText(plan.name)
            return editor
        combo = QComboBox(parent)
        for label, value in self._options(plan, column):
            combo.addItem(label, value)
        return combo

    def _options(self, plan: LoadoutPlan,
                 column: int) -> list[tuple[str, str]]:
        if column == COL_SCHOOL:
            schools = self._game_config.get_schools()
            return [(tr("自定义"), CUSTOM_SCHOOL),
                    *((name, name) for name in schools)]
        if column in (COL_MAIN_ART, COL_SUB_ART):
            return [(name, name)
                    for name in self._game_config.get_martial_arts()]
        if column == COL_PLAYSTYLE:
            school = resolve_school(
                plan.main_martial_art, plan.sub_martial_art,
                self._game_config.get_schools()) or ""
            return playstyle_options(
                self._game_config, plan.main_martial_art,
                plan.sub_martial_art, school=school, plan=plan)
        if column == COL_COMBAT:
            return [(combat_type_label(kind), kind)
                    for kind in (COMBAT_TYPE_PVE, COMBAT_TYPE_PVP)]
        return []

    def setEditorData(self, editor: QWidget | None,  # noqa: N802
                      index: QModelIndex) -> None:
        plan = self._plan_of(index.row())
        if plan is None:
            return
        if isinstance(editor, QLineEdit):
            editor.setText(plan.name)
            editor.selectAll()
            return
        if isinstance(editor, QComboBox):
            editor.setCurrentIndex(
                max(editor.findData(self._current_value(plan, index.column())),
                    0))

    @staticmethod
    def _current_value(plan: LoadoutPlan, column: int) -> str:
        return {
            COL_MAIN_ART: plan.main_martial_art,
            COL_SUB_ART: plan.sub_martial_art,
            COL_PLAYSTYLE: plan.playstyle,
            COL_COMBAT: plan.combat_type,
        }.get(column, "")

    def setModelData(self, editor: QWidget | None, model,  # noqa: N802
                     index: QModelIndex) -> None:
        """提交给宿主，不直接写进 model——整行要按联动结果重读。"""
        plan = self._plan_of(index.row())
        if plan is None:
            return
        if isinstance(editor, QLineEdit):
            value = editor.text().strip()
        elif isinstance(editor, QComboBox):
            value = str(editor.currentData() or "")
        else:
            return
        self._commit(plan, index.column(), value)


def plan_field_updates(schools: dict, plan: LoadoutPlan,
                       column: int, value: str) -> dict[str, str]:
    """一次列编辑最终要写回方案的字段，含联动。

    流派本身不是方案上的字段——选了流派就是选了它预置的那两门武学，所以这里
    把它翻译成武学更新。选「自定义」不动武学，只是让武学两列重新可编。
    """
    if column == COL_NAME:
        # 名称不允许空串：调用方据此放弃本次写入并回滚展示。
        return {"name": value} if value else {}
    if column == COL_SCHOOL:
        if not value:
            return {}
        main_art, sub_art = school_arts(schools, value)
        if not main_art or not sub_art:
            return {}
        return {"main_martial_art": main_art, "sub_martial_art": sub_art}
    if column == COL_MAIN_ART:
        return {"main_martial_art": value}
    if column == COL_SUB_ART:
        return {"sub_martial_art": value}
    if column == COL_PLAYSTYLE:
        return {"playstyle": value}
    if column == COL_COMBAT:
        return {"combat_type": value}
    return {}


def locked_columns(schools: dict, plan: LoadoutPlan) -> frozenset[int]:
    """当前不可编辑的列。

    绑定了有效流派时，两门武学由流派决定，不能单独改——要改先换流派，或者把
    流派切到「自定义」。
    """
    school = resolve_school(plan.main_martial_art, plan.sub_martial_art,
                            schools)
    return (frozenset({COL_MAIN_ART, COL_SUB_ART}) if school
            else frozenset())


def locked_reason() -> str:
    return tr("由流派决定；改流派，或把流派切到「自定义」后再单独编辑")


EDITABLE_COLUMNS = frozenset({
    COL_NAME, COL_SCHOOL, COL_MAIN_ART, COL_SUB_ART,
    COL_PLAYSTYLE, COL_COMBAT,
})


__all__ = [
    "COL_COMBAT",
    "COL_MAIN_ART",
    "COL_NAME",
    "COL_PLAYSTYLE",
    "COL_SCHOOL",
    "COL_SUB_ART",
    "CUSTOM_SCHOOL",
    "EDITABLE_COLUMNS",
    "PlanFieldDelegate",
    "locked_columns",
    "locked_reason",
    "plan_field_updates",
]
