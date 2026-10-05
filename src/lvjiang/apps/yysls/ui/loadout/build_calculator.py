"""出装搭配编辑与角色模拟共用的对话框；保存只触及独立搭配配置。"""
from __future__ import annotations

import copy
from uuid import uuid4

from PyQt6.QtCore import QMimeData, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QPainter, QPixmap, QStandardItemModel
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.combo_box import AutoWidthComboBox, ComboWidthMode

from .....i18n import tr
from .....ui.button_styles import apply_button_style, apply_compact_button_style
from .....ui.layout_helpers import fit_combo_to_contents
from .....ui.widgets import strip_focus_rect
from ...config import get_game_config
from ...config.builds import BuildDefinition, BuildRepository, check_requirements
from ...config.equipment_slots import SLOT_SPECS
from ...core.affix_cap import affix_cap_value
from ...core.combat.affix_rules import normal_affix_candidates
from ...core.combat.combat_attrs import apply_hypothetical_caps
from ...core.combat.equipment_sets import LEFT_SET_SLOTS
from ...core.graduation.context import PlanScoringContext, gongjue_attrs
from ...core.graduation.scoring import LoadoutScorer
from ...core.loadout.affix_distribution import (
    TOTAL_AFFIXES_MAX,
    DistributionResult,
    distribute_affixes,
    distribution_counts,
    native_name,
)
from ...core.loadout.affix_swap import swap_affixes
from ...core.tuning_rules import get_tuning_rule_manager
from ...core.tuning_rules.models import dynamic_affix_map

PRIORITY_LABELS = {"required": "强制要求", "optimal": "最佳要求", "recommended": "推荐要求"}
# 仅改变此表的展示顺序，不改变全局装备槽位或分配引擎。
BUILD_DISPLAY_SLOTS = tuple(sorted(SLOT_SPECS, key=lambda spec: spec.key not in LEFT_SET_SLOTS))


class AffixDistributionTable(QTableWidget):
    """拖拽开始就计算全部目标，放下时再次验证。"""

    def __init__(self, editor):
        super().__init__(0, 7, editor)
        self.editor = editor
        self.setHorizontalHeaderLabels([tr(s) for s in ["位置", "宫", "商", "角", "徵", "羽", "套装"]])
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        header = self.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        vertical = self.verticalHeader()
        assert vertical is not None
        vertical.hide()
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        # 首次填充时列宽尚未展开；单行词条不能按隐藏窗口的窄列宽折成多行。
        self.setWordWrap(False)
        strip_focus_rect(self)
        self._source: tuple[str, int] | None = None
        self._press_position: QPoint | None = None

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self._press_position = event.position().toPoint() if event.button() == Qt.MouseButton.LeftButton else None
        item = self.itemAt(event.position().toPoint())
        if item is not None:
            self.setCurrentItem(item)

    def mouseMoveEvent(self, event):
        if (self._press_position is not None and event.buttons() & Qt.MouseButton.LeftButton
                and (event.position().toPoint() - self._press_position).manhattanLength()
                >= QApplication.startDragDistance()):
            self._press_position = None
            self.startDrag(Qt.DropAction.MoveAction)
            return
        super().mouseMoveEvent(event)

    def startDrag(self, supportedActions):
        if self.editor._timer.isActive():
            self.editor.recalculate()
        item = self.currentItem()
        if item is None or not 1 <= item.column() <= 5 or not self.editor.result.feasible:
            return
        source = (BUILD_DISPLAY_SLOTS[item.row()].key, item.column())
        if not self.editor.result.equipment[source[0]].get(f"affix_{source[1]}"):
            return
        text = item.text()
        self._source = source
        for row, spec in enumerate(BUILD_DISPLAY_SLOTS):
            for index in range(1, 6):
                changed, reason = swap_affixes(self.editor.result.equipment, source,
                                              (spec.key, index), self.editor.gc)
                cell = self.item(row, index)
                if cell is not None:
                    cell.setBackground(self.palette().highlight() if changed is not None
                                       else self.palette().alternateBase())
                    cell.setToolTip(tr("可交换") if changed is not None else tr(reason))
        item.setText("")
        item.setBackground(QColor("#d49a36"))
        item.setToolTip(tr("正在拖动"))
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData("application/x-lvjiang-affix-swap", b"swap")
        drag.setMimeData(mime)
        metrics = self.fontMetrics()
        preview = QPixmap(metrics.horizontalAdvance(text) + 24, metrics.height() + 16)
        preview.fill(Qt.GlobalColor.transparent)
        painter = QPainter(preview)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self.font())
        painter.setBrush(self.palette().base())
        painter.setPen(self.palette().highlight().color())
        painter.drawRoundedRect(preview.rect().adjusted(1, 1, -1, -1), 6, 6)
        painter.setPen(self.palette().text().color())
        painter.drawText(preview.rect(), Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
        drag.setPixmap(preview)
        drag.setHotSpot(QPoint(preview.width() // 2, preview.height() // 2))
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self._source = None
            QToolTip.hideText()
            self.editor.recalculate()

    def dragEnterEvent(self, event):
        if event.source() is self and self._source is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if event.source() is self and self._source is not None and item is not None and 1 <= item.column() <= 5:
            changed, reason = swap_affixes(self.editor.result.equipment, self._source,
                                     (BUILD_DISPLAY_SLOTS[item.row()].key, item.column()), self.editor.gc)
            QToolTip.showText(self.viewport().mapToGlobal(event.position().toPoint()),
                              tr("可交换") if changed is not None else tr(reason), self)
            if changed is not None:
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if event.source() is self and self._source is not None and item is not None and 1 <= item.column() <= 5:
            changed, _ = swap_affixes(self.editor.result.equipment, self._source,
                                     (BUILD_DISPLAY_SLOTS[item.row()].key, item.column()), self.editor.gc)
            if changed is not None:
                self.editor._templates = copy.deepcopy(changed)
                self.editor.result.equipment = changed
                self.editor._dirty = True
                event.acceptProposedAction()
                return
        event.ignore()


class AffixCounter(QWidget):
    """数量只通过减/加调整，边界按钮禁用，不接收滚轮改值。"""

    valueChanged = pyqtSignal(int)

    def __init__(self, value=0, parent=None):
        super().__init__(parent)
        self._value = 0
        self._total_full = False
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(6)
        self.minus = QPushButton("−")
        self.plus = QPushButton("+")
        self.number = QLabel()
        self.number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.number.setMinimumWidth(
            self.fontMetrics().horizontalAdvance(str(TOTAL_AFFIXES_MAX)) + 8)
        for button in (self.minus, self.plus):
            apply_compact_button_style(button, variant="neutral" if button is self.minus else "action")
            button.setAutoDefault(False)
            side = max(28, button.sizeHint().height())
            button.setFixedSize(side, side)
            button.setStyleSheet(button.styleSheet() + f" QPushButton {{border-radius: {side // 2}px; padding: 0;}}")
        self.plus.setStyleSheet(self.plus.styleSheet() +
                                " QPushButton:enabled {background: palette(highlight); color: palette(highlighted-text);}")
        row.addWidget(self.minus)
        row.addWidget(self.number)
        row.addWidget(self.plus)
        self.minus.clicked.connect(lambda: self.setValue(self._value - 1))
        self.plus.clicked.connect(lambda: self.setValue(self._value + 1))
        self.setValue(value)

    def value(self) -> int:
        return self._value

    def set_total_full(self, full: bool) -> None:
        """全套词条已达上限，再加任何一条都会越界。

        总量由所有计数器共享，单个计数器看不到别的列，所以由所属编辑器注入。
        """
        if full == self._total_full:
            return
        self._total_full = full
        self._sync_buttons()

    def setValue(self, value: int):
        value = max(0, min(TOTAL_AFFIXES_MAX, value))
        changed = value != self._value
        self._value = value
        self.number.setText(str(value))
        self._sync_buttons()
        if changed:
            self.valueChanged.emit(value)

    def _sync_buttons(self) -> None:
        self.minus.setEnabled(self._value > 0)
        self.plus.setEnabled(
            self._value < TOTAL_AFFIXES_MAX and not self._total_full)
        self.plus.setToolTip(
            tr("全套最多 {count} 条词条，请先减少其他词条").format(
                count=TOTAL_AFFIXES_MAX) if self._total_full else "")


class RequirementEditor(QFrame):
    """适合左侧窄栏的两行词条约束编辑卡片。"""

    changed = pyqtSignal()
    deleteRequested = pyqtSignal()

    def __init__(self, names: list[str], data: dict, parent=None):
        super().__init__(parent)
        self.setProperty("surface", "card")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        affix_row = QHBoxLayout()
        affix_row.setSpacing(6)
        affix_row.addWidget(QLabel(tr("词条")))
        self.affix = AutoWidthComboBox(content_width_cap=250)
        self.affix.addItems(list(dict.fromkeys([
            *names, str(data.get("affix") or ""),
        ])))
        self.affix.setCurrentText(str(data.get("affix") or ""))
        affix_row.addWidget(self.affix, 1)
        remove = QPushButton(tr("删除"))
        apply_compact_button_style(remove, variant="danger")
        remove.clicked.connect(lambda: self.deleteRequested.emit())
        affix_row.addWidget(remove)
        root.addLayout(affix_row)

        range_row = QHBoxLayout()
        range_row.setSpacing(6)
        self.priority = AutoWidthComboBox(width_mode=ComboWidthMode.FULL)
        for key, label in PRIORITY_LABELS.items():
            self.priority.addItem(tr(label), key)
        self.priority.setCurrentIndex(max(
            0, self.priority.findData(data.get("priority", "required"))))
        range_row.addWidget(self.priority)
        range_row.addStretch()
        range_row.addWidget(QLabel(tr("最少")))
        self.minimum = QSpinBox()
        self.minimum.setRange(0, TOTAL_AFFIXES_MAX)
        self.minimum.setKeyboardTracking(False)
        self.minimum.setValue(int(data.get("minimum", 0)))
        range_row.addWidget(self.minimum)
        range_row.addWidget(QLabel(tr("最多")))
        self.maximum = QSpinBox()
        self.maximum.setRange(0, TOTAL_AFFIXES_MAX)
        self.maximum.setKeyboardTracking(False)
        self.maximum.setValue(int(data.get("maximum", TOTAL_AFFIXES_MAX)))
        range_row.addWidget(self.maximum)
        root.addLayout(range_row)

        self.minimum.valueChanged.connect(self._minimum_changed)
        self.maximum.valueChanged.connect(self._maximum_changed)
        self.affix.currentTextChanged.connect(lambda _text: self.changed.emit())
        self.priority.currentIndexChanged.connect(lambda _index: self.changed.emit())
        self.minimum.valueChanged.connect(lambda _value: self.changed.emit())
        self.maximum.valueChanged.connect(lambda _value: self.changed.emit())
        self._minimum_changed(self.minimum.value())

    def _minimum_changed(self, value: int) -> None:
        self.maximum.setMinimum(value)

    def _maximum_changed(self, value: int) -> None:
        self.minimum.setMaximum(value)

    def to_dict(self) -> dict:
        return {
            "affix": self.affix.currentText(),
            "priority": self.priority.currentData(),
            "minimum": self.minimum.value(),
            "maximum": self.maximum.value(),
        }


def _table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels([tr(text) for text in headers])
    vertical, horizontal = table.verticalHeader(), table.horizontalHeader()
    assert vertical is not None and horizontal is not None
    vertical.hide()
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.setTextElideMode(Qt.TextElideMode.ElideNone)
    strip_focus_rect(table)
    horizontal.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    horizontal.setStretchLastSection(True)
    return table


def _cell(table: QTableWidget, row: int, column: int, text: object) -> None:
    item = QTableWidgetItem(str(text))
    item.setToolTip(str(text))
    table.setItem(row, column, item)


def template_for_playstyle(playstyle: str, game_config) -> dict[str, dict]:
    cfg = game_config.get_playstyle(playstyle) or {}
    return {
        spec.key: {"type": str(cfg.get("main_weapon" if spec.key == "main_weapon" else "sub_weapon") or "")
                   if spec.is_weapon else spec.part}
        for spec in SLOT_SPECS
    }


class BuildEditor(QWidget):
    def __init__(self, playstyle: str, *, repository=None, context=None,
                 equipped=None, initial=None, host=None, parent=None):
        super().__init__(parent)
        self.gc = get_game_config()
        self.repository = repository or BuildRepository()
        self.context: PlanScoringContext | None = context
        self._host = host
        self.playstyle = playstyle
        self.attribute = str((self.gc.get_playstyle(playstyle) or {}).get("attr") or "")
        self._loading = True
        self._dirty = False
        self._expected: dict | None = None
        self._build: BuildDefinition | None = None
        self._initial = copy.deepcopy(initial)
        self._original = copy.deepcopy(equipped or {})
        self._templates = template_for_playstyle(playstyle, self.gc)
        self._templates.update(copy.deepcopy(self._original))
        self.result = DistributionResult(errors=["尚未计算"])
        self._counts: dict[str, AffixCounter] = {}
        self._count_caps: dict[str, QLabel] = {}
        self._sets: dict[str, QComboBox] = {}
        self._baseline_rate: float | None = None
        self._baseline_attrs = None
        if context is not None:
            scorer = context.scorer(game_config=self.gc)
            self._baseline_attrs = context.base_attrs + scorer.equipment_attrs(self._original)
            self._baseline_rate = scorer.rate(self._original)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self.recalculate)
        self._setup()
        self._refresh_builds()
        if initial is not None:
            self.load_build(initial)
        else:
            self._load_current()

    def _setup(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        toolbar = QFrame()
        toolbar.setProperty("surface", "card")
        toolbar_layout = QVBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 6, 10, 6)
        toolbar_layout.setSpacing(6)
        root.addWidget(toolbar)
        selection = QHBoxLayout()
        selection.addWidget(QLabel(tr("出装搭配")))
        self.build_combo = AutoWidthComboBox()
        self.build_combo.setMinimumContentsLength(18)
        self.build_combo.currentIndexChanged.connect(self._switch_build)
        selection.addWidget(self.build_combo, 1)
        selection.addWidget(QLabel(tr("保存位置")))
        self.save_location = AutoWidthComboBox(width_mode=ComboWidthMode.FULL)
        self.save_location.addItem(tr("本地"), "local")
        self.save_location.addItem(tr("系统预置"), "system")
        if not self.repository.resolver.is_dev_mode():
            model = self.save_location.model()
            assert isinstance(model, QStandardItemModel)
            model.item(1).setEnabled(False)
            model.item(1).setToolTip(tr("普通用户不能写入系统预置"))
        self.save_location.setToolTip(tr("保存到其他位置会另存一份，原搭配保留"))
        self.save_location.currentIndexChanged.connect(self._changed)
        selection.addWidget(self.save_location)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(tr("出装搭配名称"))
        self.name_edit.textChanged.connect(self._changed)
        selection.addWidget(self.name_edit, 1)
        for label, callback in [("保存出装搭配", lambda: self.save(False)),
                                ("另存为新搭配", lambda: self.save(True))]:
            button = QPushButton(tr(label))
            apply_button_style(button, variant="action" if label == "保存出装搭配" else "neutral")
            button.clicked.connect(callback)
            selection.addWidget(button)
            if label == "保存出装搭配":
                self.save_button = button
            else:
                self.save_as_button = button
        toolbar_layout.addLayout(selection)
        settings = QHBoxLayout()
        settings.addWidget(QLabel(tr("装备等级")))
        self.level = AutoWidthComboBox(width_mode=ComboWidthMode.FULL)
        for cfg in self.gc.get_level_configs():
            self.level.addItem(str(cfg.level), cfg.level)
        self.level.currentIndexChanged.connect(self._level_changed)
        settings.addWidget(self.level)
        self.chengyin = QCheckBox(tr("承音数值"))
        self.chengyin.setToolTip(tr("取消后按普通词条满值计算；装备基础属性和定音仍按所选等级"))
        self.chengyin.toggled.connect(self._changed)
        settings.addWidget(QLabel(tr("弓玦套装")))
        self.gongjue = AutoWidthComboBox()
        self.gongjue.addItem(tr("无"), "")
        # 与现有战斗属性页共用候选，而非另写游戏事实。
        from .combat.attrs_tab import _GONGJUE_TYPES
        for name in _GONGJUE_TYPES[1:]:
            self.gongjue.addItem(tr(name), name)
        fit_combo_to_contents(self.gongjue, minimum=96)
        self.gongjue.currentIndexChanged.connect(self._changed)
        settings.addWidget(self.gongjue)
        self.gongjue_level = QSpinBox()
        self.gongjue_level.setRange(1, 999)
        self.gongjue_level.valueChanged.connect(self._changed)
        settings.addWidget(self.gongjue_level)
        settings.addSpacing(12)
        settings.addWidget(self.chengyin)
        settings.addStretch()
        self.metrics = QLabel()
        self.metrics.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.metrics.setStyleSheet("font-size: 14px; font-weight: 600;")
        settings.addWidget(self.metrics)
        toolbar_layout.addLayout(settings)
        self.status = QLabel()
        self.status.setWordWrap(True)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)
        left_tabs = QTabWidget()
        self._left_tabs = left_tabs
        left_tabs.setMinimumWidth(350)
        equipment_page = QWidget()
        left_layout = QVBoxLayout(equipment_page)
        left_layout.setContentsMargins(8, 8, 8, 8)
        heading_row = QHBoxLayout()
        heading_row.addStretch()
        self.count_summary = QLabel()
        self.count_summary.setProperty("tone", "muted")
        heading_row.addWidget(self.count_summary)
        left_layout.addLayout(heading_row)
        left_layout.addWidget(self.status)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._count_container = QWidget()
        self._count_layout = QVBoxLayout(self._count_container)
        self._count_layout.setContentsMargins(0, 0, 0, 0)
        self._count_layout.setSpacing(4)
        self._count_layout.addStretch()
        scroll.setWidget(self._count_container)
        left_layout.addWidget(scroll, 1)
        add = QPushButton(tr("添加可用词条"))
        apply_button_style(add, variant="neutral")
        add.clicked.connect(self._add_affix)
        left_layout.addWidget(add)
        left_tabs.addTab(equipment_page, tr("装备词条"))

        req_page = QWidget()
        req_layout = QVBoxLayout(req_page)
        req_layout.setContentsMargins(8, 8, 8, 8)
        req_scroll = QScrollArea()
        req_scroll.setWidgetResizable(True)
        req_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._requirement_container = QWidget()
        self._requirement_layout = QVBoxLayout(self._requirement_container)
        self._requirement_layout.setContentsMargins(0, 0, 0, 0)
        self._requirement_layout.setSpacing(6)
        self._requirement_layout.addStretch()
        req_scroll.setWidget(self._requirement_container)
        req_layout.addWidget(req_scroll, 1)
        self._requirement_editors: list[RequirementEditor] = []
        add_requirement = QPushButton(tr("添加约束"))
        apply_button_style(add_requirement, variant="action")
        add_requirement.clicked.connect(self._add_requirement)
        req_layout.addWidget(add_requirement)
        self.requirement_status = QLabel()
        self.requirement_status.setWordWrap(True)
        self.requirement_status.setProperty("tone", "muted")
        req_layout.addWidget(self.requirement_status)
        left_tabs.addTab(req_page, tr("词条约束"))
        splitter.addWidget(left_tabs)

        result_page = QWidget()
        self._result_page = result_page
        result_layout = QVBoxLayout(result_page)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(6)
        self.distribution_table = AffixDistributionTable(self)
        self.distribution_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.distribution_table.setMinimumHeight(380)
        header = self.distribution_table.horizontalHeader()
        vertical = self.distribution_table.verticalHeader()
        assert header is not None and vertical is not None
        header.setStretchLastSection(False)
        for index in range(1, 6):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
        vertical.setMinimumSectionSize(42)
        result_layout.addWidget(self.distribution_table)
        self._attrs_preview = None
        if self.context is not None and self._host is not None:
            from .combat.attrs_tab import CombatAttrsTab
            self._attrs_preview = CombatAttrsTab(self._host, preview=True)
            self._attrs_preview.set_embedded_mode("full")
            result_layout.addWidget(self._attrs_preview, 1)
        splitter.addWidget(result_page)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([390, 930])
        note = QLabel(tr("装备分布优先保证合法并保留原位置，不代表毕业率全局最优；保存不会修改当前用户装备或基础属性。"))
        note.setWordWrap(True)
        note.setProperty("tone", "muted")
        root.addWidget(note)

    def _refresh_builds(self, selected: str = ""):
        self.build_combo.blockSignals(True)
        self.build_combo.clear()
        self.build_combo.addItem(tr("从当前方案开始"), "")
        for build in self.repository.all(self.playstyle):
            self.build_combo.addItem(build.name, build.id)
        self.build_combo.setCurrentIndex(max(0, self.build_combo.findData(selected)))
        self.build_combo.blockSignals(False)

    def _load_current(self):
        level = self.context.world_level if self.context else self.gc.current_equip_level()
        build = BuildDefinition.create(tr("新出装搭配"), self.playstyle, level)
        build.equipment = copy.deepcopy(self._templates if self._build is None else self._original)
        if self.context:
            build.gongjue = self.context.gongjue
            build.gongjue_level = self.context.gongjue_level
        self.load_build(build, persisted=False)

    def load_build(self, build: BuildDefinition, *, persisted=True):
        self._timer.stop()
        self._loading = True
        self._build = copy.deepcopy(build)
        self._expected = build.to_dict() if persisted else None
        self.save_location.setCurrentIndex(max(0, self.save_location.findData(build.storage)))
        self.name_edit.setText(build.name)
        index = self.level.findData(build.level)
        if index < 0:
            self.level.addItem(str(build.level), build.level)
            index = self.level.count() - 1
        self.level.setCurrentIndex(index)
        self.chengyin.setChecked(build.chengyin)
        self.gongjue.setCurrentIndex(max(0, self.gongjue.findData(build.gongjue)))
        self.gongjue_level.setValue(build.gongjue_level or self.gc.gongjue_level_for(build.level))
        self._templates = template_for_playstyle(self.playstyle, self.gc)
        self._templates.update(copy.deepcopy(build.equipment))
        counts = distribution_counts(build.equipment, self.attribute, self.gc)
        self._populate_counts(counts)
        self._populate_sets()
        for editor in self._requirement_editors:
            self._requirement_layout.removeWidget(editor)
            editor.deleteLater()
        self._requirement_editors.clear()
        for requirement in build.requirements:
            self._append_requirement(requirement)
        self._refresh_builds(build.id if persisted else "")
        self._loading = False
        self._dirty = False
        self.recalculate()

    def _legal_names(self) -> list[str]:
        level = int(self.level.currentData() or 0)
        names: list[str] = []
        for equip in self._templates.values():
            for name in normal_affix_candidates({**equip, "level": level}, self.gc):
                value = native_name(name, self.attribute, self.gc)
                if value not in names:
                    names.append(value)
        return names

    def _populate_counts(self, counts: dict[str, int]):
        allowed = set(counts)
        aliases = dynamic_affix_map(self.attribute, game_config=self.gc)
        for rule in get_tuning_rule_manager().get_rules().values():
            if self.playstyle in rule.playstyles:
                for name in self._legal_names():
                    if name in rule.pool_set or aliases.get(name) in rule.pool_set:
                        allowed.add(name)
        cfg = self.gc.get_playstyle(self.playstyle) or {}
        allowed.update(n for n in (cfg.get("main_damage"), cfg.get("sub_damage")) if n)
        names = [n for n in self._legal_names() if n in allowed]
        names.extend(n for n in counts if n not in names)
        while self._count_layout.count() > 1:
            item = self._count_layout.takeAt(0)
            if item is not None and (widget := item.widget()) is not None:
                widget.deleteLater()
        self._counts.clear()
        self._count_caps.clear()
        for name in names:
            self._append_count(name, counts.get(name, 0))
        self._sync_count_limits()

    def _sync_count_limits(self) -> None:
        """全套 40 条上限由所有计数器共同承担：总数到顶，谁都不能再加。

        单列自身的边界由 AffixCounter 判断，这里只回答「再加一条会不会越界」，
        这样点加号之前按钮就已经不可用，而不是等引擎报错。
        """
        total = sum(spin.value() for spin in self._counts.values())
        full = total >= TOTAL_AFFIXES_MAX
        for spin in self._counts.values():
            spin.set_total_full(full)

    def _append_count(self, name: str, count=0):
        if name in self._counts:
            return
        card = QFrame()
        card.setProperty("surface", "card")
        row = QHBoxLayout(card)
        row.setContentsMargins(8, 2, 4, 2)
        row.setSpacing(6)
        label = QLabel(name)
        label.setStyleSheet("font-weight: 600;")
        row.addWidget(label)
        cap = QLabel()
        cap.setProperty("tone", "muted")
        row.addWidget(cap)
        row.addStretch()
        spin = AffixCounter(count)
        spin.minus.setAccessibleName(f"{name} −")
        spin.plus.setAccessibleName(f"{name} +")
        spin.valueChanged.connect(self._changed)
        row.addWidget(spin)
        self._count_layout.insertWidget(self._count_layout.count() - 1, card)
        self._counts[name] = spin
        self._count_caps[name] = cap

    def _refresh_count_caps(self):
        level = int(self.level.currentData() or 0)
        for name, label in self._count_caps.items():
            real_names = [n for n in self.gc.get_normal_affix_names()
                          if native_name(n, self.attribute, self.gc) == name]
            values = sorted({value for n in real_names if (
                value := affix_cap_value(level, n, chengyin=self.chengyin.isChecked(), game_config=self.gc)) is not None})
            unit = str((self.gc.get_affix_caps(level, real_names[0]) or {}).get("unit") or "") if real_names else ""
            value_text = (f"{values[0]:g}{unit}" if len(values) == 1 else
                          f"{values[0]:g}～{values[-1]:g}{unit}" if values else "—")
            label.setText(f"{tr('满值')}：{value_text}")

    def _populate_sets(self):
        self.distribution_table.setRowCount(len(SLOT_SPECS))
        self._sets.clear()
        for row, spec in enumerate(BUILD_DISPLAY_SLOTS):
            _cell(self.distribution_table, row, 0, spec.label)
            combo = AutoWidthComboBox()
            combo.addItem(tr("无套装"), "")
            for key, cfg in self.gc.get_equipment_sets("left" if spec.key in LEFT_SET_SLOTS else "right").items():
                combo.addItem(str(cfg.get("name") or key), key)
            current = str((self._templates.get(spec.key) or {}).get("equipment_set") or "")
            if current and combo.findData(current) < 0:
                combo.addItem(current, current)
            combo.setCurrentIndex(max(0, combo.findData(current)))
            combo.currentIndexChanged.connect(self._changed)
            fit_combo_to_contents(combo)
            self.distribution_table.setCellWidget(row, 6, combo)
            self._sets[spec.key] = combo

    def _add_affix(self):
        names = [n for n in self._legal_names() if n not in self._counts]
        if not names:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("添加可用词条"))
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        layout.addWidget(QLabel(tr("词条")))
        combo = AutoWidthComboBox(width_mode=ComboWidthMode.FULL)
        combo.addItems(names)
        layout.addWidget(combo)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(max(460, dialog.sizeHint().width()), max(190, dialog.sizeHint().height()))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._append_count(combo.currentText())
            self._changed()

    def _append_requirement(self, data: dict):
        names = list(dict.fromkeys([*self._counts, *self._legal_names()]))
        editor = RequirementEditor(names, data)
        editor.changed.connect(self._changed)
        editor.deleteRequested.connect(
            lambda item=editor: self._delete_requirement(item))
        self._requirement_layout.insertWidget(
            self._requirement_layout.count() - 1, editor)
        self._requirement_editors.append(editor)

    def _add_requirement(self):
        self._append_requirement({"affix": next(iter(self._counts), ""), "priority": "optimal"})
        self._changed()

    def _delete_requirement(self, editor: RequirementEditor):
        if editor not in self._requirement_editors:
            return
        self._requirement_editors.remove(editor)
        self._requirement_layout.removeWidget(editor)
        editor.deleteLater()
        self._changed()

    def _requirement_rows(self) -> list[dict]:
        return [editor.to_dict() for editor in self._requirement_editors]

    def _level_changed(self):
        if not self._loading:
            counts = self.counts()
            self._populate_counts(counts)
            self._changed()

    def _changed(self, *_args):
        if not self._loading:
            self._sync_count_limits()
            self._dirty = True
            self.metrics.setText(tr("正在重新计算…"))
            self.save_button.setEnabled(False)
            self.save_as_button.setEnabled(False)
            self._timer.start()

    def counts(self) -> dict[str, int]:
        return {name: spin.value() for name, spin in self._counts.items() if spin.value()}

    def recalculate(self):
        self._timer.stop()
        counts = self.counts()
        templates = copy.deepcopy(self._templates)
        for slot, combo in self._sets.items():
            templates[slot]["equipment_set"] = combo.currentData() or ""
        self.result = distribute_affixes(
            counts, templates, attribute=self.attribute,
            level=int(self.level.currentData() or 0), chengyin=self.chengyin.isChecked(), game_config=self.gc)
        # 分配器保留部位偏好；普通槽位顺序由当前草稿拥有，保存/重算不能打乱。
        for slot, equip in self.result.equipment.items():
            remaining = [equip.pop(f"affix_{i}", None) for i in range(2, 6)]
            for i in range(2, 6):
                name = (templates[slot].get(f"affix_{i}") or {}).get("name")
                match = next((n for n, affix in enumerate(remaining)
                              if affix and affix["name"] == name), None)
                if match is not None:
                    equip[f"affix_{i}"] = remaining[match]
                    remaining[match] = None
            for affix in remaining:
                if affix:
                    index = next(i for i in range(2, 6) if f"affix_{i}" not in equip)
                    equip[f"affix_{index}"] = affix
        if self.result.feasible:
            self._templates = copy.deepcopy(self.result.equipment)
        requirements = self._requirement_rows()
        evaluated = check_requirements(counts, requirements)
        labels = [f"{tr(PRIORITY_LABELS[row['priority']])} · {row['affix']}：{row['actual']} / "
                  f"{row.get('minimum', 0)}～{row.get('maximum', TOTAL_AFFIXES_MAX)}"
                  f" {'✓' if row['satisfied'] else '未满足'}"
                  for row in evaluated]
        self.requirement_status.setText("\n".join(labels) or tr("未设置额外要求"))
        required_ok = all(row["satisfied"] for row in evaluated if row["priority"] == "required")
        complete = sum(counts.values()) == 40 and required_ok and self.result.feasible
        state = tr("完整且满足强制要求") if complete else tr("草稿／要求未满足")
        self.status.setText("；".join([state, *self.result.errors]))
        self.status.setVisible(not complete)
        for row, spec in enumerate(BUILD_DISPLAY_SLOTS):
            equip = self.result.equipment.get(spec.key) or {}
            for index in range(1, 6):
                _cell(self.distribution_table, row, index, (equip.get(f"affix_{index}") or {}).get("name", "—"))
        self.count_summary.setText(f"{sum(counts.values())} / 40")
        self.count_summary.setToolTip(state)
        self._refresh_count_caps()
        self.distribution_table.resizeRowsToContents()
        if self._attrs_preview is not None:
            self._attrs_preview.setEnabled(self.result.feasible)
        self.metrics.setText(tr("无法分配，请调整词条数量") if not self.result.feasible
                             else tr("从备战方案进入可加载角色基础属性并计算毕业率"))
        if self.result.feasible and self.context:
            try:
                self._show_calculation()
            except (ValueError, KeyError, ArithmeticError) as exc:
                self.metrics.setText(tr("计算失败：") + str(exc))
        valid_requirements = all(
            row.get("minimum", 0) <= row.get("maximum", TOTAL_AFFIXES_MAX)
            for row in requirements)
        can_save = self.result.feasible and valid_requirements and bool(self.name_edit.text().strip())
        target = self.save_location.currentData()
        writable = target == "local" or self.repository.resolver.is_dev_mode()
        self.save_button.setEnabled(can_save and writable)
        self.save_button.setToolTip("" if writable else tr("系统预置只读，请选择本地保存位置或另存为新搭配"))
        self.save_as_button.setEnabled(can_save)

    def _show_calculation(self):
        context = self.context
        assert context is not None
        base = context.base_attrs_without_gongjue + gongjue_attrs(
            self.gongjue.currentData(), self.gc, world_level=context.world_level,
            gongjue_level=self.gongjue_level.value())
        equipped = apply_hypothetical_caps(self.result.equipment, full_dingyin=True, playstyle=self.playstyle)
        scorer = LoadoutScorer(context.calculator, base, context.school, self.gc, attr_context=context.attr_context)
        rate = scorer.rate(equipped)
        self.metrics.setText(f"{tr('毕业率')} {rate:.2%}  ·  {tr('当前方案')} {self._baseline_rate:.2%}  ·  "
                             f"{tr('变化')} {(rate - self._baseline_rate) * 100:+.2f}%")
        self.metrics.setToolTip(f"{tr('毕业率方案')}：{context.scheme} · "
                               f"{tr('模型等级')} {context.model_level} · {tr('模型版本')} {context.model_version}")
        if self._attrs_preview is not None:
            self._attrs_preview.setEnabled(True)
            self._attrs_preview.show_preview(
                equipped,
                gongjue=str(self.gongjue.currentData() or ""),
                world_level=context.world_level,
                gongjue_level=self.gongjue_level.value(),
                base_attrs=context.base_attrs_without_gongjue,
                school=context.school,
            )

    def save(self, as_new=False):
        self.recalculate()
        if not (self.save_as_button if as_new else self.save_button).isEnabled() or self._build is None:
            return
        build = copy.deepcopy(self._build)
        target = str(self.save_location.currentData() or "local")
        if as_new and not self.repository.resolver.is_dev_mode():
            target = "local"
        copy_to_location = self._expected is not None and target != self._build.storage
        if as_new:
            name, ok = QInputDialog.getText(self, tr("另存为新搭配"), tr("名称"), text=self.name_edit.text())
            if not ok or not name.strip():
                return
            build.id, build.name = uuid4().hex, name.strip()
        else:
            build.name = self.name_edit.text().strip()
            if copy_to_location:
                build.id = uuid4().hex
        build.storage = target
        if as_new or copy_to_location:
            build.content_version = 1
        build.level = int(self.level.currentData())
        build.chengyin = self.chengyin.isChecked()
        build.combat_type = "pve"
        build.gongjue, build.gongjue_level = self.gongjue.currentData(), self.gongjue_level.value()
        build.equipment = copy.deepcopy(self.result.equipment)
        build.requirements = self._requirement_rows()
        try:
            self.repository.save(build, expected=None if as_new or copy_to_location else self._expected)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, tr("保存失败"), str(exc))
            return
        self.load_build(build)

    def confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        return QMessageBox.question(
            self, tr("未保存的修改"), tr("放弃当前出装搭配的未保存修改？"),
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Discard

    def _switch_build(self):
        if self._loading:
            return
        selected = self.build_combo.currentData()
        if not self.confirm_discard():
            self._refresh_builds(self._build.id if self._expected and self._build else "")
            return
        build = next((b for b in self.repository.all(self.playstyle) if b.id == selected), None)
        if build:
            self.load_build(build)
        else:
            self._load_current()


class BuildCalculatorDialog(QDialog):
    def __init__(self, playstyle: str, *, user_label="", base_label="", repository=None,
                 context=None, equipped=None, initial=None, host=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("模拟计算器") if context else tr("编辑出装搭配"))
        self.resize(1400, 960)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(6)
        if context:
            label = QLabel(f"{user_label} · {context.plan_name}  |  {tr('角色基础属性')}：{base_label}")
            label.setToolTip(f"{context.school} · {context.playstyle} · "
                             f"{tr('毕业率方案')}：{context.scheme} · "
                             f"{tr('模型等级')} {context.model_level} · {tr('模型版本')} {context.model_version}")
            label.setWordWrap(True)
            label.setProperty("tone", "muted")
            layout.addWidget(label)
        self.editor = BuildEditor(playstyle, repository=repository, context=context,
                                  equipped=equipped, initial=initial, host=host,
                                  parent=self)
        layout.addWidget(self.editor)
        close = QPushButton(tr("关闭"))
        apply_button_style(close, variant="neutral")
        close.clicked.connect(self.reject)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(close)
        layout.addLayout(footer)

    def reject(self):
        if self.editor.confirm_discard():
            self.editor._timer.stop()
            super().reject()

    def closeEvent(self, event):
        if self.editor.confirm_discard():
            self.editor._timer.stop()
            event.accept()
        else:
            event.ignore()


class BuildListPanel(QWidget):
    """玩法页的轻量入口；编辑与模拟使用同一个 BuildEditor。"""

    def __init__(self, parent=None, *, repository=None):
        super().__init__(parent)
        self.repository = repository or BuildRepository()
        self.playstyle = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._toggle = QToolButton()
        self._toggle.setText(tr("出装搭配"))
        self._toggle.setCheckable(True)
        self._toggle.setChecked(True)
        self._toggle.setAutoRaise(True)
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(Qt.ArrowType.DownArrow)
        layout.addWidget(self._toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        self._content = QWidget()
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(8, 0, 8, 8)
        content_layout.setSpacing(6)
        layout.addWidget(self._content)
        self._toggle.toggled.connect(self._toggle_content)
        self.table = _table(["名称", "等级", "词条数", "弓玦套装", "保存位置"])
        header = self.table.horizontalHeader()
        assert header is not None
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(170)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit())
        content_layout.addWidget(self.table)
        row = QHBoxLayout()
        self._buttons: dict[str, QPushButton] = {}
        for label, callback in [("新建出装搭配", self.create), ("编辑", self.edit), ("复制", self.duplicate), ("删除", self.delete)]:
            button = QPushButton(tr(label))
            apply_button_style(button, variant="danger" if label == "删除" else "action" if label == "新建出装搭配" else "neutral")
            button.clicked.connect(callback)
            row.addWidget(button)
            self._buttons[label] = button
        row.addStretch()
        content_layout.addLayout(row)
        self._items: list[BuildDefinition] = []
        self.table.itemSelectionChanged.connect(self._update_actions)
        self._update_actions()

    def _toggle_content(self, expanded: bool) -> None:
        self._content.setVisible(expanded)
        self._toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def set_playstyle(self, playstyle: str):
        self.playstyle = playstyle
        self.setEnabled(bool(playstyle))
        self.refresh()

    def refresh(self):
        self._items = self.repository.all(self.playstyle) if self.playstyle else []
        self.table.setRowCount(len(self._items))
        for row, build in enumerate(self._items):
            count = sum(1 for e in build.equipment.values() for i in range(1, 6) if e.get(f"affix_{i}"))
            for col, text in enumerate([build.name, build.level, f"{count}/40", build.gongjue or tr("无"),
                                        tr("系统预置") if build.storage == "system" else tr("本地")]):
                _cell(self.table, row, col, text)
        self._update_actions()

    def _update_actions(self):
        build = self.selected()
        for label in ("编辑", "复制", "删除"):
            self._buttons[label].setEnabled(build is not None)
        if build and not self.repository.can_delete(build):
            self._buttons["删除"].setEnabled(False)
            self._buttons["删除"].setToolTip(tr("系统预设不能删除，请复制后编辑"))
        else:
            self._buttons["删除"].setToolTip("")

    def selected(self):
        row = self.table.currentRow()
        return self._items[row] if 0 <= row < len(self._items) else None

    def create(self):
        name, ok = QInputDialog.getText(self, tr("新建出装搭配"), tr("名称"))
        if ok and name.strip():
            build = BuildDefinition.create(name.strip(), self.playstyle, get_game_config().current_equip_level())
            try:
                self.repository.save(build)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, tr("保存失败"), str(exc))
                return
            self.refresh()
            BuildCalculatorDialog(self.playstyle, initial=build, repository=self.repository, parent=self).exec()
            self.refresh()

    def edit(self):
        if build := self.selected():
            BuildCalculatorDialog(self.playstyle, initial=build, repository=self.repository, parent=self).exec()
            self.refresh()

    def duplicate(self):
        if build := self.selected():
            name, ok = QInputDialog.getText(self, tr("复制出装搭配"), tr("名称"), text=build.name)
            if ok and name.strip():
                build = copy.deepcopy(build)
                build.id, build.name = uuid4().hex, name.strip()
                build.storage = "local"
                build.content_version = 1
                try:
                    self.repository.save(build)
                except (ValueError, OSError) as exc:
                    QMessageBox.warning(self, tr("保存失败"), str(exc))
                    return
                self.refresh()

    def delete(self):
        build = self.selected()
        if build is None:
            return
        if QMessageBox.question(self, tr("删除出装搭配"), f"{tr('确定删除')}「{build.name}」？",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.repository.delete(build)
            self.refresh()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, tr("删除失败"), str(exc))


def open_build_calculator(host, parent):
    """只在打开时读取角色上下文；后续模拟不跟随外部用户/方案切换。"""
    from ...core.combat.equipment import EquipmentInventory
    from ...core.graduation.context import PlanContextError

    user = host.active_user_name()
    if not user:
        QMessageBox.information(parent, tr("模拟计算器"), tr("请先选择当前用户和备战方案"))
        return
    try:
        inventory = EquipmentInventory(user)
        plan = inventory.active_plan
        if not plan.playstyle:
            raise PlanContextError("请先为当前备战方案选择玩法")
        gc = get_game_config()
        context = PlanScoringContext.from_plan(
            plan, game_config=gc, world_level=inventory.state.effective_world_level(gc.current_equip_level()))
        dialog = BuildCalculatorDialog(plan.playstyle, user_label=user, base_label=plan.base_attribute, context=context,
                                        equipped=inventory.equipped, host=host,
                                        parent=parent)
        dialog.exec()
    except (PlanContextError, ValueError, OSError) as exc:
        QMessageBox.warning(parent, tr("无法打开模拟计算器"), str(exc))
