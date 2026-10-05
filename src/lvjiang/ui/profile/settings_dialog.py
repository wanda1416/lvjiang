"""用户 Profile 数据模型定义对话框

编辑 profile.yaml，按四种数据模型（配额/再生/库存/备注）分区管理 key 定义。
每个模型 Tab 内展示该类型的 key 列表，支持新增/删除/上移/下移。
新增/编辑通过弹出对话框完成，表单根据模型类型动态切换。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics, QIntValidator
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.combo_box import AutoWidthComboBox

from ...core.profile.models import (
    ALL_MODELS,
    DEFAULT_KEY_GROUP,
    DIR_BOTH,
    DIRECTION_LABELS,
    MODEL_LABELS,
    MODEL_NOTE,
    MODEL_QUOTA,
    MODEL_REGEN,
    MODEL_STOCK,
    KeyDef,
    NoteKeyDef,
    QuotaKeyDef,
    RegenKeyDef,
    StepDef,
    StockKeyDef,
    SyncTargetDef,
    format_sync_label,
    group_key_definitions,
    normalize_key_group,
)
from ...core.profile.periods import get_profile_period, list_profile_periods
from ...i18n import tr
from ..button_styles import apply_button_style, fit_button_width
from ..tag_input import TagInputWidget

# 模型 TAB 顺序
_MODEL_ORDER = [MODEL_QUOTA, MODEL_STOCK, MODEL_REGEN, MODEL_NOTE]


class _ChangeScriptFileField(QWidget):
    """Profile 变更脚本选择器，只保存 workflows 内的相对路径。"""

    def __init__(self, value: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._input = QLineEdit(value)
        self._input.setReadOnly(True)
        self._input.setPlaceholderText(tr("未选择"))
        self._input.setToolTip(tr("数值实际变化后异步执行；路径相对于 workflows 目录"))
        layout.addWidget(self._input, 1)

        self._select_button = QPushButton(tr("选择..."))
        self._validate_button = QPushButton(tr("校验"))
        self._clear_button = QPushButton(tr("清除"))
        for button in (
            self._select_button, self._validate_button, self._clear_button,
        ):
            button.setAutoDefault(False)
            apply_button_style(button, variant="neutral")
            layout.addWidget(button)

        self._select_button.clicked.connect(self._select_file)
        self._validate_button.clicked.connect(self._validate_file)
        self._clear_button.clicked.connect(self._input.clear)

    def text(self) -> str:
        return self._input.text().strip()

    def _select_file(self) -> None:
        from ...core.config import get_resolver

        resolver = get_resolver()
        # write_dir 按开发模式路由：开发者 -> system，普通用户 -> local。
        default_root = resolver.write_dir("workflows")
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("选择变更脚本"),
            str(default_root),
            tr("工作流文件 (*.wf)"),
        )
        if not path:
            return

        chosen = Path(path).resolve()
        for layer_root in (resolver.local_dir, resolver.system_dir):
            base = (layer_root / "workflows").resolve()
            try:
                relative = chosen.relative_to(base).as_posix()
            except ValueError:
                continue
            self._input.setText(relative)
            return
        QMessageBox.warning(
            self,
            tr("选择变更脚本"),
            tr("请选择 config/system/workflows 或 config/local/workflows 目录下的 .wf 文件"),
        )

    def _validate_file(self) -> None:
        from ...workflows.discovery import resolve_workflow_path
        from ...workflows.grammar import parse_file

        script = self.text()
        if not script:
            QMessageBox.warning(self, tr("校验失败"), tr("请先选择变更脚本"))
            return
        path, _ = resolve_workflow_path(script)
        if path is None:
            QMessageBox.warning(
                self,
                tr("校验失败"),
                tr("变更脚本不存在: workflows/{path}").format(path=script),
            )
            return
        try:
            parse_file(path)
        except Exception as exc:  # noqa: BLE001 - 需将 DSL 解析错误展示给用户
            QMessageBox.warning(self, tr("校验失败"), str(exc))
            return
        QMessageBox.information(self, tr("校验通过"), tr("工作流语法正确"))


def _format_cap(kd: KeyDef) -> str:
    """上限列显示：硬上限 [x]，软上限 (x)，无上限空"""
    if kd.cap is None:
        return ""
    if kd.soft:
        return f"({kd.cap})"
    return f"[{kd.cap}]"


def _format_period(kd: KeyDef) -> str:
    """周期列显示（Quota 用 period，Regen 按恢复类型显示）"""
    if isinstance(kd, QuotaKeyDef):
        period = get_profile_period(kd.period)
        return tr(period.label) if period is not None else kd.period
    if isinstance(kd, RegenKeyDef):
        regen_labels = {"minute": tr("分钟"), "hour": tr("小时"), "day": tr("每天"), "week": tr("每周")}
        if kd.regen_type == "realtime":
            unit = regen_labels.get(kd.regen_rate_unit, kd.regen_rate_unit)
            return tr("实时/{unit}").format(unit=unit)
        return tr("准点/{unit}").format(unit=regen_labels.get(kd.regen_period, kd.regen_period))
    return ""


def _format_sync_summary(kd: KeyDef) -> str | None:
    """sync_targets 摘要片段（三种模型通用），无同步目标时返回 None"""
    if not kd.sync_targets:
        return None
    sync_parts = []
    for t in kd.sync_targets:
        label_text = format_sync_label(t.key)
        ratio_text = f"x{t.ratio:g}" if t.ratio != 1.0 else ""
        dir_text = f"[{DIRECTION_LABELS[t.direction]}]" if t.direction != DIR_BOTH else ""
        sync_parts.append(f"{label_text}{ratio_text}{dir_text}")
    return tr("同步:{sync}").format(sync=','.join(sync_parts))

class _SyncTargetsWidget(QWidget):
    """同步目标动态列表编辑器

    每行一个 SyncTargetDef：目标 key 下拉框 + 倍率 spinbox + 来源输入 + 删除按钮。
    exclude_key_input: 指向正在编辑的 key 输入框，下拉框排除自身，防止自环。
    """

    def __init__(self, exclude_key_input: QLineEdit | None = None, parent=None):
        super().__init__(parent)
        self._exclude_key_input = exclude_key_input
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels([tr("目标"), tr("倍率"), tr("方向"), tr("来源"), ""])
        v_header = self._table.verticalHeader()
        if v_header is not None:
            v_header.setVisible(False)
        self._table.setAlternatingRowColors(True)

        header = self._table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(1, 120)
        self._table.setColumnWidth(2, 90)
        self._table.setColumnWidth(3, 120)
        self._table.setColumnWidth(4, 44)
        _fit_table_to_rows(self._table)

        layout.addWidget(self._table)

        btn_add = QPushButton("+ " + tr("添加同步目标"))
        btn_add.setFixedWidth(120)
        btn_add.setAutoDefault(False)
        apply_button_style(btn_add)
        btn_add.clicked.connect(lambda: self.add_row())
        layout.addWidget(btn_add)

    def add_row(
        self,
        target: SyncTargetDef | None = None,
    ) -> None:
        """添加一行同步目标"""
        row = self._table.rowCount()
        self._table.setRowCount(row + 1)

        # 目标 key 下拉框（所有模型类型的 key）
        from ...core.profile import get_profile_config
        config = get_profile_config()

        combo = AutoWidthComboBox()
        combo.addItem(tr("（请选择）"), "")
        exclude = (
            self._exclude_key_input.text().strip()
            if self._exclude_key_input else ""
        )
        for mt in ALL_MODELS:
            model_label = MODEL_LABELS.get(mt, mt)
            for kd in config.get_keys_by_model(mt):
                if kd.key == exclude:
                    continue  # 排除自身，防止自环
                sync_key = f"{mt}:{kd.key}"
                combo.addItem(f"{model_label}：{kd.label}", sync_key)

        if target:
            idx = combo.findData(target.key)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        self._table.setCellWidget(row, 0, combo)

        # 倍率
        ratio_spin = QDoubleSpinBox()
        # 数据模型和同步引擎都允许任意负数/小数倍率，这里不应
        # 额外用 UI 的人为上限截断如 -3000 这样的合法配置。
        ratio_spin.setRange(float("-inf"), float("inf"))
        ratio_spin.setDecimals(2)
        ratio_spin.setSingleStep(0.5)
        ratio_spin.setValue(target.ratio if target else 1.0)
        self._table.setCellWidget(row, 1, ratio_spin)

        # 方向限定
        direction_combo = AutoWidthComboBox()
        for val, text in DIRECTION_LABELS.items():
            direction_combo.addItem(text, val)
        dir_idx = direction_combo.findData(target.direction if target else DIR_BOTH)
        if dir_idx >= 0:
            direction_combo.setCurrentIndex(dir_idx)
        self._table.setCellWidget(row, 2, direction_combo)

        # 来源（可选）
        source_input = QLineEdit(target.source if target else "")
        self._table.setCellWidget(row, 3, source_input)

        # 删除按钮（点击时按 widget 反查行号，避免删行后行号错位）
        btn_remove = QPushButton("×")
        # 36 而不是 30：套上带边框+内边距的统一样式后 30 会把「×」挤掉
        btn_remove.setFixedWidth(36)
        btn_remove.setAutoDefault(False)
        apply_button_style(btn_remove, variant="danger")
        btn_remove.clicked.connect(
            lambda _checked, b=btn_remove: self._remove_row(self._row_of_widget(b))
        )
        self._table.setCellWidget(row, 4, btn_remove)
        _fit_table_to_rows(self._table)

    def _row_of_widget(self, widget: QWidget) -> int:
        """反查指定 cell widget 所在行（QTableWidget.row 只接受 QTableWidgetItem）"""
        for r in range(self._table.rowCount()):
            if self._table.cellWidget(r, 4) is widget:
                return r
        return -1

    def _remove_row(self, row: int) -> None:
        if row >= 0:
            self._table.removeRow(row)
            _fit_table_to_rows(self._table)

    def get_sync_targets(self) -> list[SyncTargetDef]:
        """收集所有有效的同步目标"""
        targets: list[SyncTargetDef] = []
        for row in range(self._table.rowCount()):
            combo = self._table.cellWidget(row, 0)
            if not isinstance(combo, AutoWidthComboBox):
                continue
            key = combo.currentData()
            if not key:
                continue
            ratio_spin = self._table.cellWidget(row, 1)
            direction_combo = self._table.cellWidget(row, 2)
            source_input = self._table.cellWidget(row, 3)
            ratio = ratio_spin.value() if isinstance(ratio_spin, QDoubleSpinBox) else 1.0
            direction = (
                direction_combo.currentData()
                if isinstance(direction_combo, AutoWidthComboBox) else DIR_BOTH
            )
            source = source_input.text().strip() if isinstance(source_input, QLineEdit) else ""
            targets.append(SyncTargetDef(key=key, ratio=ratio, direction=direction, source=source))
        return targets


class _AmountTagInputWidget(TagInputWidget):
    """一行内可录入多个正整数快捷数量。"""

    _COMPACT_HEIGHT = 36

    def __init__(self, amounts: list[int], parent=None) -> None:
        super().__init__([str(amount) for amount in amounts if amount > 0], parent)
        # 普通词条输入框独占表单行，52px 的高度合理；快捷数量
        # 却是嵌在表格单元格内，复用该高度会把每条规则撑到近两行。
        self.setFixedHeight(self._COMPACT_HEIGHT)
        self._row.setContentsMargins(4, 1, 4, 1)
        # 数量较多时仍可横向滚动；窄滚动条避免在紧凑行内挤压输入框。
        self._scroll.setStyleSheet("QScrollBar:horizontal { height: 7px; }")
        self._input.setValidator(QIntValidator(1, 999999, self._input))
        self._input.setMinimumWidth(170)

    def amounts(self) -> list[int]:
        return [int(value) for value in self.tags()]


class _RuleTermTagInputWidget(TagInputWidget):
    """表格单元格内的多来源/用途输入。"""

    def __init__(self, terms: list[str], parent=None) -> None:
        super().__init__(terms, parent)
        self.setFixedHeight(_AmountTagInputWidget._COMPACT_HEIGHT)
        self._row.setContentsMargins(4, 1, 4, 1)
        self._scroll.setStyleSheet("QScrollBar:horizontal { height: 7px; }")
        self._input.setMinimumWidth(140)


class _ChangeRulesTable(QTableWidget):
    """类型和删除列固定，来源与数量按 6:4 分配剩余空间。"""

    _TYPE_WIDTH = 132
    _REMOVE_WIDTH = 40

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        viewport = self.viewport()
        if viewport is None:
            return
        available = max(
            0, viewport.width() - self._TYPE_WIDTH - self._REMOVE_WIDTH
        )
        name_width = available * 6 // 10
        self.setColumnWidth(0, self._TYPE_WIDTH)
        self.setColumnWidth(1, name_width)
        self.setColumnWidth(2, available - name_width)
        self.setColumnWidth(3, self._REMOVE_WIDTH)


def _fit_table_to_rows(table: QTableWidget) -> None:
    """表格默认显示实际数据行加一行留白。"""
    header = table.horizontalHeader()
    vertical = table.verticalHeader()
    header_height = header.sizeHint().height() if header is not None else 0
    default_row_height = (
        vertical.defaultSectionSize() if vertical is not None else 30
    )
    rows_height = sum(table.rowHeight(row) for row in range(table.rowCount()))
    table.setFixedHeight(
        header_height + rows_height + default_row_height + table.frameWidth() * 2
    )
    table.updateGeometry()


def _standalone_terms(vocabulary: list[str], steps: list[StepDef], *, positive: bool) -> list[str]:
    """仅返回没有被变动规则隐式提供的独立词条。"""
    bound = {
        step.source
        for step in steps
        if step.source and ((step.value > 0) if positive else (step.value < 0))
    }
    return list(dict.fromkeys(value for value in vocabulary if value not in bound))


def _merge_terms(explicit: list[str], steps: list[StepDef], *, positive: bool) -> list[str]:
    """保存时把规则名称自动并入来源/用途词表。"""
    result = list(dict.fromkeys(value for value in explicit if value))
    for step in steps:
        matches = (step.value > 0) if positive else (step.value < 0)
        if matches and step.source and step.source not in result:
            result.append(step.source)
    return result


class _ChangeRulesWidget(QWidget):
    """只编辑绑定了快捷数量的规则；独立词条由 TagInputWidget 管理。"""

    _KIND_USE = "use"
    _KIND_SOURCE = "source"

    def __init__(self, steps: list[StepDef], parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._table = _ChangeRulesTable()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(
            [tr("类型"), tr("来源/用途"), tr("快捷数量"), ""]
        )
        self._table.setAlternatingRowColors(True)
        vertical_header = self._table.verticalHeader()
        if vertical_header is not None:
            vertical_header.setVisible(False)

        header = self._table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._table.setColumnWidth(3, _ChangeRulesTable._REMOVE_WIDTH)
        _fit_table_to_rows(self._table)
        layout.addWidget(self._table)

        buttons = QHBoxLayout()
        add_use = QPushButton("+ " + tr("添加用途"))
        add_source = QPushButton("+ " + tr("添加来源"))
        add_use.setAutoDefault(False)
        add_source.setAutoDefault(False)
        apply_button_style(add_use, add_source)
        fit_button_width(add_use, add_source, minimum=96)
        add_use.clicked.connect(lambda: self.add_row(self._KIND_USE))
        add_source.clicked.connect(lambda: self.add_row(self._KIND_SOURCE))
        buttons.addWidget(add_use)
        buttons.addWidget(add_source)
        buttons.addStretch()
        layout.addLayout(buttons)

        # 展示顺序与快捷菜单一致：用途在上、来源在下。
        # 只还原配置中本来就是 values/sources 的组合行；
        # 普通 value/source 每条保持独立，等用户自己编辑和删除。
        ordered_steps = [s for s in steps if s.value < 0]
        ordered_steps.extend(s for s in steps if s.value > 0)
        seen_groups: set[tuple[str, str]] = set()
        for step in ordered_steps:
            kind = self._KIND_USE if step.value < 0 else self._KIND_SOURCE
            if step._group_id:
                group_key = (kind, step._group_id)
                if group_key in seen_groups:
                    continue
                seen_groups.add(group_key)
            self.add_row(kind, [step.source], [abs(step.value)])

        # 上面先为组合规则放置了占位行，在原位合并其余成员。
        group_rows: dict[tuple[str, str], int] = {}
        row = 0
        for step in ordered_steps:
            kind = self._KIND_USE if step.value < 0 else self._KIND_SOURCE
            if not step._group_id:
                row += 1
                continue
            group_key = (kind, step._group_id)
            if group_key not in group_rows:
                group_rows[group_key] = row
                row += 1
                continue
            target_row = group_rows[group_key]
            name_input = self._table.cellWidget(target_row, 1)
            amount_input = self._table.cellWidget(target_row, 2)
            if isinstance(name_input, _RuleTermTagInputWidget):
                name_input.add_tag(step.source)
            if isinstance(amount_input, _AmountTagInputWidget):
                amount_input.add_tag(str(abs(step.value)))

    def add_row(
        self,
        kind: str,
        names: list[str] | None = None,
        amounts: list[int] | None = None,
    ) -> None:
        row = self._table.rowCount()
        self._table.setRowCount(row + 1)

        kind_combo = AutoWidthComboBox()
        kind_combo.addItem(tr("用途（减少）"), self._KIND_USE)
        kind_combo.addItem(tr("来源（增加）"), self._KIND_SOURCE)
        index = kind_combo.findData(kind)
        if index >= 0:
            kind_combo.setCurrentIndex(index)
        self._table.setCellWidget(row, 0, kind_combo)

        name_input = _RuleTermTagInputWidget(names or [])
        self._table.setCellWidget(row, 1, name_input)

        amount_tags = _AmountTagInputWidget(amounts or [])
        self._table.setCellWidget(row, 2, amount_tags)
        self._table.resizeRowToContents(row)

        remove_button = QPushButton("×")
        remove_button.setFixedWidth(36)
        remove_button.setAutoDefault(False)
        apply_button_style(remove_button, variant="danger")
        remove_button.clicked.connect(
            lambda _checked, button=remove_button: self._remove_widget_row(button)
        )
        self._table.setCellWidget(row, 3, remove_button)
        _fit_table_to_rows(self._table)

    def _remove_widget_row(self, widget: QWidget) -> None:
        for row in range(self._table.rowCount()):
            if self._table.cellWidget(row, 3) is widget:
                self._table.removeRow(row)
                _fit_table_to_rows(self._table)
                return

    def get_steps(self) -> list[StepDef]:
        """用途规则在前、来源规则在后返回，方向由类型而非用户输入的符号决定。"""
        use_steps: list[StepDef] = []
        source_steps: list[StepDef] = []
        for row in range(self._table.rowCount()):
            kind_combo = self._table.cellWidget(row, 0)
            name_input = self._table.cellWidget(row, 1)
            amount_tags = self._table.cellWidget(row, 2)
            if not (
                isinstance(kind_combo, AutoWidthComboBox)
                and isinstance(name_input, _RuleTermTagInputWidget)
                and isinstance(amount_tags, _AmountTagInputWidget)
            ):
                continue
            kind = kind_combo.currentData()
            target = use_steps if kind == self._KIND_USE else source_steps
            names = name_input.tags()
            amounts = amount_tags.amounts()
            group_id = f"editor:{row}" if len(names) * len(amounts) > 1 else ""
            for name in names:
                for amount in amounts:
                    target.append(
                        StepDef(
                            value=-amount if kind == self._KIND_USE else amount,
                            source=name,
                            _group_id=group_id,
                        )
                    )
        return use_steps + source_steps

    def validation_error(self) -> str:
        """检查每条快捷规则都绑定了名称和数量。"""
        for row in range(self._table.rowCount()):
            kind_combo = self._table.cellWidget(row, 0)
            name_input = self._table.cellWidget(row, 1)
            amount_tags = self._table.cellWidget(row, 2)
            if not (
                isinstance(kind_combo, AutoWidthComboBox)
                and isinstance(name_input, _RuleTermTagInputWidget)
                and isinstance(amount_tags, _AmountTagInputWidget)
            ):
                continue
            names = name_input.tags()
            if not names:
                return tr("变动规则第 {row} 行设置了快捷数量，请填写来源或用途").format(
                    row=row + 1
                )
            if not amount_tags.amounts():
                return tr("变动规则第 {row} 行至少添加一个快捷数量").format(row=row + 1)
        return ""


# QTableWidgetItem.UserRole key：在表格首列存储完整 KeyDef 对象
_ROLE_KEYDEF = Qt.ItemDataRole.UserRole

# 周几选项（isoweekday: 1=周一 ... 7=周日）
_WEEKDAY_NAMES = [tr("周一"), tr("周二"), tr("周三"), tr("周四"), tr("周五"), tr("周六"), "周日"]  # runtime tr()


class _ModelTab(QWidget):
    """单个模型类型的 key 编辑页"""

    def __init__(self, model_type: str, parent=None):
        super().__init__(parent)
        self._model_type = model_type
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 工具栏
        toolbar = QHBoxLayout()
        self._group_tabs = QTabBar()
        self._group_tabs.setExpanding(False)
        self._group_tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self._group_tabs.setUsesScrollButtons(True)
        self._group_tabs.currentChanged.connect(self._group_changed)
        # 占满操作按钮左侧空间；分组总宽度超出后 QTabBar 自动显示滚动按钮。
        toolbar.addWidget(self._group_tabs, 1)

        btn_add = QPushButton("+ " + tr("新增"))
        btn_add.clicked.connect(self._add_key)
        toolbar.addWidget(btn_add)

        btn_del = QPushButton("- " + tr("删除"))
        btn_del.clicked.connect(self._delete_key)
        toolbar.addWidget(btn_del)

        btn_up = QPushButton("↑ " + tr("上移"))
        btn_up.clicked.connect(self._move_up)
        toolbar.addWidget(btn_up)

        btn_down = QPushButton("↓ " + tr("下移"))
        btn_down.clicked.connect(self._move_down)
        toolbar.addWidget(btn_down)

        apply_button_style(btn_add)
        apply_button_style(btn_del, variant="danger")
        apply_button_style(btn_up, btn_down, variant="neutral")
        # 定宽放在套样式之后：padding 参与 sizeHint，且宽度随字体自适应，
        # 写死 70 在 Windows 的 Segoe UI 下会把文字切掉。
        fit_button_width(btn_add, btn_del, btn_up, btn_down, minimum=70)

        layout.addLayout(toolbar)

        # key 表格 — 根据模型类型决定列结构
        # Quota: Key | 分组 | 标签 | 上限 | 周期 | 来源 | 详情摘要
        # Regen: Key | 分组 | 标签 | 上限 | 周期 | 用途 | 详情摘要
        # Stock: Key | 分组 | 标签 | 上限 | 来源 | 用途 | 详情摘要
        # Note:  Key | 分组 | 标签 | 上限 | 来源/用途 | 详情摘要
        self._table = QTableWidget()
        if self._model_type == MODEL_QUOTA:
            self._table.setColumnCount(7)
            self._table.setHorizontalHeaderLabels(["Key", tr("分组"), tr("标签"), tr("上限"), tr("周期"), tr("来源"), tr("详情摘要")])
        elif self._model_type == MODEL_REGEN:
            self._table.setColumnCount(7)
            self._table.setHorizontalHeaderLabels(["Key", tr("分组"), tr("标签"), tr("上限"), tr("周期"), tr("用途"), tr("详情摘要")])
        elif self._model_type == MODEL_NOTE:
            self._table.setColumnCount(6)
            self._table.setHorizontalHeaderLabels(["Key", tr("分组"), tr("标签"), tr("上限"), tr("来源/用途"), tr("详情摘要")])
        else:
            self._table.setColumnCount(7)
            self._table.setHorizontalHeaderLabels(["Key", tr("分组"), tr("标签"), tr("上限"), tr("来源"), tr("用途"), tr("详情摘要")])
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.doubleClicked.connect(self._edit_key)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)

        self._group_context_menu = QMenu(self._table)
        self._change_group_action = self._group_context_menu.addAction(tr("更改分组"))
        self._change_group_action.triggered.connect(self._change_selected_groups)

        # 表头加粗
        header_font = self._table.horizontalHeader().font()
        header_font.setBold(True)
        self._table.horizontalHeader().setFont(header_font)

        # Key/标签/上限: 最小宽度 4 个汉字，超出自适应
        fm = QFontMetrics(header_font)
        min_col_width = fm.horizontalAdvance("测") * 4

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setMinimumSectionSize(min_col_width)
        if self._model_type == MODEL_NOTE:
            # Note: 来源/用途: 固定, 详情摘要: 拉伸
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
            header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
            self._table.setColumnWidth(4, 200)
        elif self._model_type == MODEL_STOCK:
            # 来源/用途: 固定, 详情摘要: 拉伸
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
            header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
            header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
            self._table.setColumnWidth(4, 150)
            self._table.setColumnWidth(5, 150)
        else:
            # Quota/Regen: 周期: 自适应, 来源或用途: 固定, 详情摘要: 拉伸
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
            header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
            self._table.setColumnWidth(5, 150)

        layout.addWidget(self._table)

    @property
    def table(self) -> QTableWidget:
        return self._table

    @property
    def model_type(self) -> str:
        return self._model_type

    @property
    def current_group(self) -> str:
        index = self._group_tabs.currentIndex()
        return normalize_key_group(
            self._group_tabs.tabData(index) if index >= 0 else None
        )

    def set_groups(self, groups: list[str], selected: str) -> None:
        """刷新派生分组列表，并保持当前选择。"""
        self._group_tabs.blockSignals(True)
        while self._group_tabs.count():
            self._group_tabs.removeTab(0)
        for group in groups:
            label = tr("默认") if group == DEFAULT_KEY_GROUP else group
            index = self._group_tabs.addTab(label)
            self._group_tabs.setTabData(index, group)
        selected_index = next(
            (
                index
                for index in range(self._group_tabs.count())
                if self._group_tabs.tabData(index) == selected
            ),
            0,
        )
        self._group_tabs.setCurrentIndex(selected_index)
        self._group_tabs.blockSignals(False)

    def _get_parent(self) -> "ProfileDefinitionDialog | None":
        parent = self.parent()
        while parent and not isinstance(parent, ProfileDefinitionDialog):
            parent = parent.parent()
        return parent if isinstance(parent, ProfileDefinitionDialog) else None

    def _add_key(self):
        dialog = self._get_parent()
        if dialog:
            dialog._add_key(self._model_type)

    def _edit_key(self, index=None):
        if index is not None and index.column() == 1:
            return
        dialog = self._get_parent()
        if dialog:
            row = self._table.currentRow()
            if row >= 0:
                dialog._edit_key(self._model_type, row)

    def _show_context_menu(self, position) -> None:
        index = self._table.indexAt(position)
        if not index.isValid():
            return
        selected_rows = {
            selected.row() for selected in self._table.selectionModel().selectedRows()
        }
        if index.row() not in selected_rows:
            self._table.clearSelection()
            self._table.selectRow(index.row())
        self._group_context_menu.exec(self._table.viewport().mapToGlobal(position))

    def _change_selected_groups(self) -> None:
        dialog = self._get_parent()
        if dialog:
            rows = sorted(
                index.row()
                for index in self._table.selectionModel().selectedRows()
            )
            if rows:
                dialog._edit_key_groups(self._model_type, rows)

    def _group_changed(self):
        dialog = self._get_parent()
        if dialog:
            dialog._refresh_model_tab(self._model_type, self.current_group)

    def _delete_key(self):
        dialog = self._get_parent()
        if dialog:
            row = self._table.currentRow()
            if row >= 0:
                dialog._delete_key(self._model_type, row)

    def _move_up(self):
        dialog = self._get_parent()
        if dialog:
            row = self._table.currentRow()
            if row > 0:
                dialog._swap_keys(self._model_type, row, row - 1)
                self._table.setCurrentCell(row - 1, 0)

    def _move_down(self):
        dialog = self._get_parent()
        if dialog:
            row = self._table.currentRow()
            if 0 <= row < self._table.rowCount() - 1:
                dialog._swap_keys(self._model_type, row, row + 1)
                self._table.setCurrentCell(row + 1, 0)


def _rename_unavailable_reason(parent: QWidget) -> str:
    from ...core.access import is_readonly
    from ...core.profile.triggers import _runner
    if is_readonly():
        return tr("只读实例不可以重命名 key")
    if _runner is not None and _runner.is_busy:
        return tr("Profile 变更脚本仍在执行，请等待队列完成后重命名")
    ancestor: QWidget | None = parent
    while ancestor is not None:
        manager = getattr(ancestor, "_run_manager", None)
        if manager is not None and manager.is_any_running():
            return tr("请停止所有任务后再重命名 Profile key")
        ancestor = ancestor.parentWidget()
    return ""


class ProfileDefinitionDialog(QDialog):
    """用户 Profile 数据模型定义对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("用户数据模型定义"))
        self.setMinimumSize(800, 550)
        self._drafts: dict[str, list[KeyDef]] = {}
        self._baseline: dict[str, list[KeyDef]] = {}
        self.has_saved_changes = False
        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(tr("定义数据模型的 key。双击非分组列可编辑，右键可批量更改分组。"))
        info.setStyleSheet("color: palette(mid); margin-bottom: 10px;")
        layout.addWidget(info)

        # 模型 Tab
        self._tab_widget = QTabWidget()
        self._tabs: dict[str, _ModelTab] = {}
        for model_type in _MODEL_ORDER:
            tab = _ModelTab(model_type)
            self._tabs[model_type] = tab
            self._tab_widget.addTab(tab, MODEL_LABELS[model_type])
        layout.addWidget(self._tab_widget, stretch=1)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_ok = QPushButton(tr("保存"))
        btn_ok.setFixedWidth(80)
        btn_ok.clicked.connect(self._on_save)
        btn_row.addWidget(btn_ok)

        btn_cancel = QPushButton(tr("取消"))
        btn_cancel.setFixedWidth(80)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        apply_button_style(btn_ok)
        apply_button_style(btn_cancel, variant="neutral")

        layout.addLayout(btn_row)

    def _load_data(self):
        """从 ProfileSchema 加载并填充表格"""
        from ...core.profile import get_profile_config
        config = get_profile_config()

        for model_type in _MODEL_ORDER:
            # 编辑器只修改自己的深拷贝；取消不会污染运行中的配置单例。
            self._drafts[model_type] = deepcopy(config.get_keys_by_model(model_type))
            self._baseline[model_type] = deepcopy(self._drafts[model_type])
            self._refresh_model_tab(model_type)

    def _refresh_model_tab(
        self,
        model_type: str,
        preferred_group: str | None = None,
        selected_key: str | None = None,
    ) -> None:
        """从该类型的全部草稿重新派生分组并绘制当前组。"""
        tab = self._tabs[model_type]
        grouped = group_key_definitions(self._drafts[model_type])
        groups = list(grouped) or [DEFAULT_KEY_GROUP]
        requested = normalize_key_group(preferred_group or tab.current_group)
        selected_group = requested if requested in groups else groups[0]
        tab.set_groups(groups, selected_group)

        visible = grouped.get(selected_group, [])
        tab.table.blockSignals(True)
        tab.table.setRowCount(len(visible))
        for row, kd in enumerate(visible):
            self._populate_row(tab, row, kd)
            if kd.key == selected_key:
                tab.table.setCurrentCell(row, 0)
        tab.table.blockSignals(False)

    @staticmethod
    def _group_display_name(group: str) -> str:
        return tr("默认") if group == DEFAULT_KEY_GROUP else group

    @staticmethod
    def _populate_row(tab: _ModelTab, row: int, kd: KeyDef) -> None:
        """填充一行数据到表格"""
        key_item = QTableWidgetItem(kd.key)
        key_item.setData(_ROLE_KEYDEF, kd)
        tab.table.setItem(row, 0, key_item)
        group_item = QTableWidgetItem(
            ProfileDefinitionDialog._group_display_name(normalize_key_group(kd.group))
        )
        group_item.setToolTip(tr("点击更改分组"))
        tab.table.setItem(row, 1, group_item)
        tab.table.setItem(row, 2, QTableWidgetItem(kd.label))
        tab.table.setItem(row, 3, QTableWidgetItem(_format_cap(kd)))
        if tab.model_type == MODEL_QUOTA:
            tab.table.setItem(row, 4, QTableWidgetItem(_format_period(kd)))
            tab.table.setItem(row, 5, QTableWidgetItem(",".join(kd.sources)))
            tab.table.setItem(row, 6, QTableWidgetItem(ProfileDefinitionDialog._summarize(kd)))
        elif tab.model_type == MODEL_REGEN:
            tab.table.setItem(row, 4, QTableWidgetItem(_format_period(kd)))
            tab.table.setItem(row, 5, QTableWidgetItem(",".join(kd.uses)))
            tab.table.setItem(row, 6, QTableWidgetItem(ProfileDefinitionDialog._summarize(kd)))
        elif tab.model_type == MODEL_NOTE:
            combined = list(dict.fromkeys(kd.sources + kd.uses))
            tab.table.setItem(row, 4, QTableWidgetItem(",".join(combined)))
            tab.table.setItem(row, 5, QTableWidgetItem(ProfileDefinitionDialog._summarize(kd)))
        else:
            tab.table.setItem(row, 4, QTableWidgetItem(",".join(kd.sources)))
            tab.table.setItem(row, 5, QTableWidgetItem(",".join(kd.uses)))
            tab.table.setItem(row, 6, QTableWidgetItem(ProfileDefinitionDialog._summarize(kd)))

    @staticmethod
    def _summarize(kd: KeyDef) -> str:
        """生成 key 的详情摘要（已移除上限/周期/来源/用途，这些已独立成列）"""
        if isinstance(kd, QuotaKeyDef):
            parts = []
            if kd.reset_day and kd.period in ("week", "month"):
                if kd.period == "week" and 1 <= kd.reset_day <= 7:
                    parts.append(tr("重置日:{day}").format(day=tr(_WEEKDAY_NAMES[kd.reset_day - 1])))
                elif kd.period == "month" and 1 <= kd.reset_day <= 31:
                    parts.append(tr("重置日:{day}号").format(day=kd.reset_day))
            if kd.show_cap:
                parts.append(tr("展示上限"))
            if kd.decimal:
                parts.append(tr("支持小数"))
            if kd.increment_only:
                parts.append(tr("单向增加"))
            if kd.steps:
                parts.append(tr("快捷规则:{count}项").format(count=len(kd.steps)))
            sync_summary = _format_sync_summary(kd)
            if sync_summary:
                parts.append(sync_summary)
            parts.append(tr("重置:{time}").format(time=kd.reset_time))
            return ", ".join(parts)

        if isinstance(kd, RegenKeyDef):
            parts = []
            period_labels = {"minute": tr("分钟"), "hour": tr("小时"), "day": tr("天"), "week": tr("周")}
            if kd.regen_type == "realtime":
                unit_text = period_labels.get(kd.regen_rate_unit, kd.regen_rate_unit)
                parts.append(tr("实时:{val}/{unit}").format(val=kd.regen_rate_value, unit=unit_text))
            else:
                period_text = period_labels.get(kd.regen_period, kd.regen_period)
                parts.append(tr("准点:{val}/{period}").format(val=kd.regen_amount, period=period_text))
            if kd.regen_type == "boundary" and kd.regen_period == "week" and kd.reset_day:
                if 1 <= kd.reset_day <= 7:
                    parts.append(tr("重置日:{day}").format(day=tr(_WEEKDAY_NAMES[kd.reset_day - 1])))
            if kd.regen_type == "boundary" and kd.regen_period in ("day", "week"):
                parts.append(tr("重置:{time}").format(time=kd.reset_time))
            if kd.show_cap:
                parts.append(tr("展示上限"))
            if kd.decimal:
                parts.append(tr("支持小数"))
            if kd.steps:
                parts.append(tr("快捷规则:{count}项").format(count=len(kd.steps)))
            sync_summary = _format_sync_summary(kd)
            if sync_summary:
                parts.append(sync_summary)
            if kd.alert_orange:
                parts.append(tr("橙警:>={val}").format(val=kd.alert_orange))
            if kd.alert_red:
                parts.append(tr("红警:>={val}").format(val=kd.alert_red))
            return ", ".join(parts)

        if isinstance(kd, StockKeyDef):
            parts = []
            if kd.show_cap:
                parts.append(tr("展示上限"))
            if kd.decimal:
                parts.append(tr("支持小数"))
            if kd.steps:
                parts.append(tr("快捷规则:{count}项").format(count=len(kd.steps)))
            sync_summary = _format_sync_summary(kd)
            if sync_summary:
                parts.append(sync_summary)
            if kd.description:
                parts.append(kd.description)
            return ", ".join(parts)

        if isinstance(kd, NoteKeyDef):
            parts = []
            if kd.show_cap:
                parts.append(tr("展示上限"))
            if kd.description:
                parts.append(kd.description)
            return ", ".join(parts)

        return ""

    # ─── key 操作 ────────────────────────────────────────────

    def _add_key(self, model_type: str):
        """新增 key"""
        tab = self._tabs[model_type]
        kd = self.open_key_editor(
            self, model_type, None, self._defined_key_names(), initial_group=tab.current_group)
        if kd is None:
            return

        self._drafts[model_type].append(kd)
        self._baseline[model_type].append(deepcopy(kd))
        self.has_saved_changes = True
        self._refresh_model_tab(model_type, kd.group, kd.key)

    def _edit_key(self, model_type: str, row: int):
        """编辑 key"""
        tab = self._tabs[model_type]
        key_item = tab.table.item(row, 0)
        if not key_item:
            return

        old_kd = key_item.data(_ROLE_KEYDEF)
        if old_kd is None:
            raise RuntimeError(f"行 {row} 缺少 KeyDef 数据，无法编辑")

        kd = self.open_key_editor(
            self, model_type, old_kd, self._defined_key_names())
        if kd is None:
            return

        drafts = self._drafts[model_type]
        drafts[drafts.index(old_kd)] = kd
        baseline = self._baseline[model_type]
        for index, definition in enumerate(baseline):
            if definition.key == old_kd.key:
                baseline[index] = deepcopy(kd)
                break
        else:
            baseline.append(deepcopy(kd))
        if kd.key != old_kd.key:
            from ...core.profile.key_rename import rename_schema_references
            from ...core.profile.schema import ProfileSchema
            renames = [(model_type, old_kd.key, kd.key)]
            self._drafts = rename_schema_references(
                ProfileSchema(keys_by_model=self._drafts), renames).keys_by_model
            self._baseline = rename_schema_references(
                ProfileSchema(keys_by_model=self._baseline), renames).keys_by_model
        self.has_saved_changes = True
        for kind in _MODEL_ORDER:
            self._refresh_model_tab(kind, kd.group if kind == model_type else None,
                                    kd.key if kind == model_type else None)

    def _edit_key_groups(self, model_type: str, rows: list[int]) -> None:
        """通过可编辑下拉框批量修改所选 key 的定义分组。"""
        tab = self._tabs[model_type]
        key_defs: list[KeyDef] = []
        for row in rows:
            key_item = tab.table.item(row, 0)
            if not key_item:
                continue
            key_def = key_item.data(_ROLE_KEYDEF)
            if key_def is None:
                raise RuntimeError(f"行 {row} 缺少 KeyDef 数据，无法编辑分组")
            key_defs.append(key_def)
        if not key_defs:
            return

        known_groups = list(group_key_definitions(self._drafts[model_type]))
        group_labels = [self._group_display_name(group) for group in known_groups]
        current_group = normalize_key_group(key_defs[0].group)
        current_index = (
            known_groups.index(current_group) if current_group in known_groups else 0
        )
        value, accepted = QInputDialog.getItem(
            self,
            tr("更改分组"),
            tr("分组名称:"),
            group_labels,
            current_index,
            True,
        )
        if not accepted:
            return
        self._assign_key_groups(model_type, key_defs, value)

    def _assign_key_groups(
        self,
        model_type: str,
        key_defs: list[KeyDef],
        value: str,
    ) -> None:
        """把一批 key 移入指定组，供界面操作与回归测试共用。"""
        cleaned = value.strip()
        group = (
            DEFAULT_KEY_GROUP
            if not cleaned or cleaned == tr("默认")
            else normalize_key_group(cleaned)
        )
        selected_ids = {id(key_def) for key_def in key_defs}
        drafts = self._drafts[model_type]
        self._drafts[model_type] = [
            replace(key_def, group=group)
            if id(key_def) in selected_ids
            else key_def
            for key_def in drafts
        ]
        self._refresh_model_tab(model_type, group, key_defs[0].key)

    def _delete_key(self, model_type: str, row: int):
        """删除 key"""
        tab = self._tabs[model_type]
        key_item = tab.table.item(row, 0)
        if not key_item:
            return

        reply = QMessageBox.question(
            self, tr("确认删除"),
            tr("确定要删除 key '{key}' 吗？").format(key=key_item.text()),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        old_kd = key_item.data(_ROLE_KEYDEF)
        if old_kd is None:
            raise RuntimeError(f"行 {row} 缺少 KeyDef 数据，无法删除")
        self._drafts[model_type].remove(old_kd)
        self._refresh_model_tab(model_type, tab.current_group)

    def _swap_keys(self, model_type: str, a: int, b: int):
        """交换当前分组内两个 key 在完整定义序列中的位置。"""
        tab = self._tabs[model_type]
        item_a = tab.table.item(a, 0)
        item_b = tab.table.item(b, 0)
        if not item_a or not item_b:
            return
        key_a = item_a.data(_ROLE_KEYDEF)
        key_b = item_b.data(_ROLE_KEYDEF)
        if key_a is None or key_b is None:
            raise RuntimeError("行缺少 KeyDef 数据，无法调整顺序")
        drafts = self._drafts[model_type]
        index_a = drafts.index(key_a)
        index_b = drafts.index(key_b)
        drafts[index_a], drafts[index_b] = drafts[index_b], drafts[index_a]
        self._refresh_model_tab(model_type, tab.current_group, key_a.key)

    # ─── 编辑对话框 ──────────────────────────────────────────

    def _defined_key_names(self) -> set[str]:
        """返回当前总定义窗口中的所有 key。"""
        result: set[str] = set()
        for model_type in _MODEL_ORDER:
            result.update(kd.key for kd in self._drafts[model_type])
        return result

    @staticmethod
    def _save_key_definition(model_type: str, original_key: str | None, definition: KeyDef) -> None:
        """单 key 保存合并最新配置，不把外层编辑草稿整体写回。"""
        from ...core.profile.key_rename import save_renamed_definitions
        from ...core.profile.maintenance import profile_lock
        from ...core.profile.schema import reload_profile_config, save_profile_config
        with profile_lock:
            current = deepcopy(reload_profile_config())
            definitions = current.keys_by_model.setdefault(model_type, [])
            if original_key is None:
                if current.get_key(definition.key) is not None:
                    raise ValueError(tr("Key '{key}' 已存在").format(key=definition.key))
                definitions.append(deepcopy(definition))
            else:
                for index, item in enumerate(definitions):
                    if item.key == original_key:
                        definitions[index] = deepcopy(definition)
                        break
                else:
                    raise ValueError(tr("原 key 已不存在，请重新打开编辑对话框"))
            current._rebuild_index()
            if original_key is not None and original_key != definition.key:
                save_renamed_definitions(current, [(model_type, original_key, definition.key)])
            else:
                save_profile_config(current)
                reload_profile_config()

    @staticmethod
    def open_key_editor(
        parent: QWidget,
        model_type: str,
        existing: KeyDef | None,
        known_keys: set[str],
        *,
        initial_group: str = DEFAULT_KEY_GROUP,
    ) -> KeyDef | None:
        """打开 key 编辑对话框，返回新的 KeyDef 或 None"""
        dialog = QDialog(parent)
        title = tr("编辑") if existing else tr("新增")
        dialog.setWindowTitle(tr("{title} Key ({model})").format(title=title, model=MODEL_LABELS[model_type]))
        dialog.setMinimumWidth(806)

        layout = QFormLayout(dialog)
        margins = layout.contentsMargins()
        layout.setContentsMargins(
            margins.left(), margins.top(), margins.right(), dialog.fontMetrics().lineSpacing() // 3)

        # 通用字段
        key_input = QLineEdit(existing.key if existing else "")
        key_input.setEnabled(existing is None)
        key_row = QHBoxLayout()
        key_row.addWidget(key_input)
        if existing is not None:
            edit_key = QPushButton(tr("编辑"))
            apply_button_style(edit_key, variant="neutral")
            def begin_key_edit() -> None:
                from ...core.access import is_readonly
                if is_readonly():
                    QMessageBox.information(dialog, tr("提示"), tr("只读实例不可以重命名 key"))
                    return
                key_input.setEnabled(True)
                key_input.setFocus()
                key_input.selectAll()
                edit_key.setEnabled(False)

            edit_key.clicked.connect(begin_key_edit)
            key_row.addWidget(edit_key)
        layout.addRow("Key:", key_row)

        label_input = QLineEdit(existing.label if existing else "")
        layout.addRow(tr("标签:"), label_input)

        change_script_input = _ChangeScriptFileField(
            existing.change_script if existing else ""
        )
        layout.addRow(tr("变更脚本:"), change_script_input)

        # 模型专属字段
        widgets: dict[str, QWidget] = {}

        # 上限/软上限/展示上限（四种模型通用）
        existing_cap = existing.cap if existing else None
        existing_soft = existing.soft if existing else False
        existing_show_cap = existing.show_cap if existing else False
        existing_decimal = existing.decimal if existing else False

        cap_spin = QSpinBox()
        cap_spin.setRange(0, 999999)
        cap_spin.setSpecialValueText(tr("无上限"))
        cap_spin.setValue(existing_cap or 0)

        soft_check = QCheckBox(tr("软上限"))
        soft_check.setChecked(existing_soft)

        cap_row = QHBoxLayout()
        cap_row.addWidget(cap_spin)
        cap_row.addWidget(soft_check)
        cap_row.addStretch()
        layout.addRow(tr("上限:"), cap_row)
        widgets["cap"] = cap_spin
        widgets["soft"] = soft_check

        show_cap_check = QCheckBox(tr("展示上限"))
        show_cap_check.setChecked(existing_show_cap)

        decimal_check = QCheckBox(tr("支持小数"))
        decimal_check.setChecked(existing_decimal)
        decimal_check.setToolTip(tr("开启后允许输入小数，UI 使用 DoubleValidator"))

        cap_opts_row = QHBoxLayout()
        cap_opts_row.addWidget(show_cap_check)
        cap_opts_row.addWidget(decimal_check)
        cap_opts_row.addStretch()
        layout.addRow(cap_opts_row)
        widgets["show_cap"] = show_cap_check
        widgets["decimal"] = decimal_check

        existing_steps = (
            existing.steps
            if isinstance(existing, (QuotaKeyDef, RegenKeyDef, StockKeyDef))
            else []
        )
        source_tags = TagInputWidget(
            _standalone_terms(
                existing.sources if existing else [], existing_steps, positive=True
            )
        )
        use_tags = TagInputWidget(
            _standalone_terms(
                existing.uses if existing else [], existing_steps, positive=False
            )
        )
        layout.addRow(tr("来源:"), source_tags)
        layout.addRow(tr("用途:"), use_tags)
        widgets["source_tags"] = source_tags
        widgets["use_tags"] = use_tags

        if model_type == MODEL_QUOTA:
            kd = existing if isinstance(existing, QuotaKeyDef) else QuotaKeyDef()
            period_combo = AutoWidthComboBox()
            for period in list_profile_periods():
                period_combo.addItem(tr(period.label), period.name)
            idx = period_combo.findData(kd.period)
            if idx >= 0:
                period_combo.setCurrentIndex(idx)
            layout.addRow(tr("周期:"), period_combo)
            widgets["period"] = period_combo

            reset_input = QLineEdit(kd.reset_time)
            reset_input.setFixedWidth(80)
            layout.addRow(tr("重置时刻:"), reset_input)
            widgets["reset_time"] = reset_input

            reset_day_spin = QSpinBox()
            reset_day_spin.setRange(0, 31)
            reset_day_spin.setSpecialValueText(tr("默认"))
            reset_day_spin.setValue(kd.reset_day)
            reset_day_label = QLabel()
            widgets["reset_day"] = reset_day_spin
            widgets["reset_day_label"] = reset_day_label
            layout.addRow(reset_day_label, reset_day_spin)

            def _update_reset_day_visibility():
                p = period_combo.currentData()
                is_week = p == "week"
                is_month = p == "month"
                visible = is_week or is_month
                reset_day_spin.setVisible(visible)
                reset_day_label.setVisible(visible)
                if is_week:
                    reset_day_spin.setRange(0, 7)
                    reset_day_label.setText(tr("重置日(周几):"))
                elif is_month:
                    reset_day_spin.setRange(0, 31)
                    reset_day_label.setText(tr("重置日(几号):"))
            period_combo.currentIndexChanged.connect(_update_reset_day_visibility)
            _update_reset_day_visibility()

            # 单向增加复选框
            increment_check = QCheckBox(tr("单向增加"))
            increment_check.setChecked(kd.increment_only)
            layout.addRow(increment_check)
            widgets["increment_only"] = increment_check

        elif model_type == MODEL_REGEN:
            rt_kd = existing if isinstance(existing, RegenKeyDef) else RegenKeyDef()

            regen_type_combo = AutoWidthComboBox()
            regen_type_combo.addItem(tr("实时恢复"), "realtime")
            regen_type_combo.addItem(tr("准点恢复"), "boundary")
            idx = regen_type_combo.findData(rt_kd.regen_type)
            if idx >= 0:
                regen_type_combo.setCurrentIndex(idx)
            layout.addRow(tr("恢复类型:"), regen_type_combo)
            widgets["regen_type"] = regen_type_combo

            regen_period_combo = AutoWidthComboBox()
            regen_period_combo.addItem(tr("分钟"), "minute")
            regen_period_combo.addItem(tr("小时"), "hour")
            regen_period_combo.addItem(tr("天"), "day")
            regen_period_combo.addItem(tr("周"), "week")
            idx = regen_period_combo.findData(rt_kd.regen_period)
            if idx >= 0:
                regen_period_combo.setCurrentIndex(idx)
            layout.addRow(tr("准点周期:"), regen_period_combo)
            widgets["regen_period"] = regen_period_combo

            regen_rate_unit_combo = AutoWidthComboBox()
            regen_rate_unit_combo.addItem(tr("分钟"), "minute")
            regen_rate_unit_combo.addItem(tr("小时"), "hour")
            regen_rate_unit_combo.addItem(tr("天"), "day")
            regen_rate_unit_combo.addItem(tr("周"), "week")
            idx = regen_rate_unit_combo.findData(rt_kd.regen_rate_unit)
            if idx >= 0:
                regen_rate_unit_combo.setCurrentIndex(idx)
            layout.addRow(tr("速率单位:"), regen_rate_unit_combo)
            widgets["regen_rate_unit"] = regen_rate_unit_combo

            regen_rate_spin = QDoubleSpinBox()
            regen_rate_spin.setRange(0, 99999)
            regen_rate_spin.setDecimals(4)
            regen_rate_spin.setSingleStep(0.1)
            regen_rate_spin.setValue(rt_kd.regen_rate_value)
            layout.addRow(tr("速率数值:"), regen_rate_spin)
            widgets["regen_rate_value"] = regen_rate_spin

            regen_amount_spin = QDoubleSpinBox()
            regen_amount_spin.setRange(0, 99999)
            regen_amount_spin.setDecimals(4)
            regen_amount_spin.setSingleStep(1)
            regen_amount_spin.setValue(rt_kd.regen_amount)
            layout.addRow(tr("每次恢复:"), regen_amount_spin)
            widgets["regen_amount"] = regen_amount_spin

            reset_input = QLineEdit(rt_kd.reset_time)
            reset_input.setFixedWidth(80)
            layout.addRow(tr("重置时刻:"), reset_input)
            widgets["reset_time"] = reset_input

            reset_day_spin = QSpinBox()
            reset_day_spin.setRange(0, 7)
            reset_day_spin.setSpecialValueText(tr("默认"))
            reset_day_spin.setValue(rt_kd.reset_day)
            reset_day_label = QLabel()
            widgets["reset_day"] = reset_day_spin
            widgets["reset_day_label"] = reset_day_label
            layout.addRow(reset_day_label, reset_day_spin)

            orange_spin = QSpinBox()
            orange_spin.setRange(0, 999999)
            orange_spin.setSpecialValueText(tr("不提醒"))
            orange_spin.setValue(rt_kd.alert_orange or 0)
            layout.addRow(tr("橙色阈值:"), orange_spin)
            widgets["alert_orange"] = orange_spin

            red_spin = QSpinBox()
            red_spin.setRange(0, 999999)
            red_spin.setSpecialValueText(tr("不提醒"))
            red_spin.setValue(rt_kd.alert_red or 0)
            layout.addRow(tr("红色阈值:"), red_spin)
            widgets["alert_red"] = red_spin

            def _update_reset_time_visibility():
                regen_type = regen_type_combo.currentData()
                period = regen_period_combo.currentData()
                is_realtime = regen_type == "realtime"
                is_day_or_week = period in ("day", "week")
                is_week = period == "week"
                regen_period_combo.setVisible(not is_realtime)
                regen_amount_spin.setVisible(not is_realtime)
                regen_rate_unit_combo.setVisible(is_realtime)
                regen_rate_spin.setVisible(is_realtime)
                for field in (regen_period_combo, regen_amount_spin, regen_rate_unit_combo, regen_rate_spin):
                    label_widget = reset_input.parent().layout().labelForField(field)
                    if label_widget:
                        label_widget.setVisible(field.isVisible())
                reset_input.setVisible((not is_realtime) and is_day_or_week)
                reset_day_spin.setVisible((not is_realtime) and is_week)
                reset_day_label.setVisible((not is_realtime) and is_week)
                # 更新标签
                label_widget = reset_input.parent().layout().labelForField(reset_input)
                if label_widget:
                    label_widget.setVisible((not is_realtime) and is_day_or_week)
                if is_week:
                    reset_day_label.setText(tr("重置日(周几):"))
            regen_period_combo.currentIndexChanged.connect(_update_reset_time_visibility)
            regen_type_combo.currentIndexChanged.connect(_update_reset_time_visibility)
            _update_reset_time_visibility()

        # 同步目标动态列表（三种模型通用，下拉排除自身）
        sync_targets_widget = _SyncTargetsWidget(exclude_key_input=key_input)
        if existing and existing.sync_targets:
            for t in existing.sync_targets:
                sync_targets_widget.add_row(t)
        widgets["sync_targets"] = sync_targets_widget

        if model_type != MODEL_NOTE:
            change_rules_widget = _ChangeRulesWidget(existing_steps)
            widgets["change_rules"] = change_rules_widget

            layout.addRow(tr("变动规则:"), change_rules_widget)
            layout.addRow(tr("同步目标:"), sync_targets_widget)
        else:
            # 备注不执行数值同步；保留旧配置，但不展示无效的编辑入口。
            sync_targets_widget.setParent(dialog)
            sync_targets_widget.hide()

        # 按钮行
        error_label = QLabel()
        error_label.setStyleSheet("color: red;")
        error_label.setWordWrap(True)
        error_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        error_label.hide()

        def show_error(message: str) -> None:
            error_label.setText(message)
            error_label.setVisible(bool(message))

        btn_row = QHBoxLayout()
        from ...core.profile.schema import get_profile_config
        persisted_key = (
            existing.key if existing and get_profile_config().get_key(existing.key, model_type=model_type)
            else None
        )
        btn_rename_history = QPushButton(tr("查看 key 重命名记录"))
        btn_rename_history.setEnabled(persisted_key is not None)
        if persisted_key is None:
            btn_rename_history.setToolTip(tr("尚未保存的 key 没有重命名记录"))

        def show_rename_history() -> None:
            from .dialogs import KeyRenameHistoryDialog
            if persisted_key is not None:
                KeyRenameHistoryDialog(model_type, persisted_key, dialog).exec()

        btn_rename_history.clicked.connect(show_rename_history)
        btn_row.addWidget(btn_rename_history)
        btn_row.addWidget(error_label, stretch=1)
        btn_row.addStretch()
        btn_ok = QPushButton(tr("保存"))
        btn_row.addWidget(btn_ok)
        btn_cancel = QPushButton(tr("取消"))
        btn_cancel.clicked.connect(dialog.reject)
        btn_row.addWidget(btn_cancel)
        apply_button_style(btn_ok)
        apply_button_style(btn_cancel, variant="neutral")
        apply_button_style(btn_rename_history, variant="neutral")
        button_area = QWidget()
        button_layout = QVBoxLayout(button_area)
        button_layout.setContentsMargins(0, dialog.fontMetrics().lineSpacing(), 0, 0)
        button_layout.addLayout(btn_row)
        layout.addRow(button_area)

        result_kd: list[KeyDef | None] = [None]

        def on_accept():
            show_error("")
            key = key_input.text().strip()
            label = label_input.text().strip()

            if not key:
                show_error(tr("请输入 Key"))
                return
            if not key.replace("_", "").isalnum():
                show_error(tr("Key 只能包含字母、数字和下划线"))
                return
            if not label:
                show_error(tr("请输入标签"))
                return

            # 检查 key 唯一性（排除自身）
            all_keys = set(known_keys)
            if existing:
                all_keys.discard(existing.key)
            if key in all_keys:
                show_error(tr("Key '{key}' 已存在").format(key=key))
                return

            source_input = widgets["source_tags"]
            use_input = widgets["use_tags"]
            assert isinstance(source_input, TagInputWidget)
            assert isinstance(use_input, TagInputWidget)

            steps_list: list[StepDef] = []
            change_rules = widgets.get("change_rules")
            if isinstance(change_rules, _ChangeRulesWidget):
                rules_error = change_rules.validation_error()
                if rules_error:
                    show_error(rules_error)
                    return
                steps_list = change_rules.get_steps()

            # 规则名称隐式并入词表，但不在上方“仅词条”标签区重复展示。
            sources_list = _merge_terms(source_input.tags(), steps_list, positive=True)
            uses_list = _merge_terms(use_input.tags(), steps_list, positive=False)

            # 收集同步目标（三种模型通用）
            sync_targets_list = widgets["sync_targets"].get_sync_targets()

            # 禁止同步目标指向自身（兼容：行已存在时 key 被改名的情况）
            self_sync_key = f"{model_type}:{key}"
            if model_type != MODEL_NOTE and any(t.key == self_sync_key for t in sync_targets_list):
                show_error(tr("同步目标不能指向自身"))
                return

            # 通用上限字段（三种模型通用）
            cap_val = widgets["cap"].value()
            cap_final = cap_val if cap_val > 0 else None
            soft_final = widgets["soft"].isChecked()
            show_cap_final = widgets["show_cap"].isChecked()
            decimal_final = widgets["decimal"].isChecked()
            change_script = change_script_input.text().strip()
            group = existing.group if existing else initial_group

            # 构造 KeyDef
            if model_type == MODEL_QUOTA:
                kd = QuotaKeyDef(
                    key=key, group=group, label=label,
                    sources=sources_list,
                    uses=uses_list,
                    sync_targets=sync_targets_list,
                    change_script=change_script,
                    period=widgets["period"].currentData(),
                    cap=cap_final,
                    soft=soft_final,
                    show_cap=show_cap_final,
                    decimal=decimal_final,
                    steps=steps_list,
                    reset_time=widgets["reset_time"].text().strip() or "05:00",
                    reset_day=widgets["reset_day"].value(),
                    increment_only=widgets["increment_only"].isChecked(),
                )
            elif model_type == MODEL_REGEN:
                orange_val = widgets["alert_orange"].value()
                red_val = widgets["alert_red"].value()
                kd = RegenKeyDef(
                    key=key, group=group, label=label,
                    sources=sources_list,
                    uses=uses_list,
                    sync_targets=sync_targets_list,
                    change_script=change_script,
                    cap=cap_final,
                    soft=soft_final,
                    show_cap=show_cap_final,
                    decimal=decimal_final,
                    regen_type=widgets["regen_type"].currentData(),
                    regen_rate_value=widgets["regen_rate_value"].value(),
                    regen_rate_unit=widgets["regen_rate_unit"].currentData(),
                    regen_amount=widgets["regen_amount"].value(),
                    regen_period=widgets["regen_period"].currentData(),
                    reset_time=widgets["reset_time"].text().strip() or "05:00",
                    reset_day=widgets["reset_day"].value(),
                    alert_orange=orange_val if orange_val > 0 else None,
                    alert_red=red_val if red_val > 0 else None,
                    steps=steps_list,
                )
            elif model_type == MODEL_STOCK:
                kd = StockKeyDef(
                    key=key, group=group, label=label,
                    sources=sources_list,
                    uses=uses_list,
                    sync_targets=sync_targets_list,
                    change_script=change_script,
                    cap=cap_final,
                    soft=soft_final,
                    show_cap=show_cap_final,
                    decimal=decimal_final,
                    steps=steps_list,
                )
            elif model_type == MODEL_NOTE:
                kd = NoteKeyDef(
                    key=key, group=group, label=label,
                    sources=sources_list,
                    uses=uses_list,
                    sync_targets=sync_targets_list,
                    change_script=change_script,
                    cap=cap_final,
                    soft=soft_final,
                    show_cap=show_cap_final,
                    decimal=decimal_final,
                )
            else:
                kd = KeyDef(
                    key=key, group=group, label=label,
                    sources=sources_list, uses=uses_list,
                    change_script=change_script,
                )

            if persisted_key is not None and persisted_key != kd.key:
                reason = _rename_unavailable_reason(parent)
                if reason:
                    show_error(reason)
                    return
                message = tr(
                    "重命名将修改所有用户的当前记录、历史记录、同步来源和配置引用。"
                    "工作流和变更脚本中的 key 不会自动替换，需自行修改。\n\n是否继续？")
                if QMessageBox.question(
                    dialog, tr("确认重命名"), message,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                ) != QMessageBox.StandardButton.Yes:
                    return
            try:
                ProfileDefinitionDialog._save_key_definition(model_type, persisted_key, kd)
            except Exception as exc:
                logger.error(f"保存 key 定义失败: {exc}")
                show_error(tr("保存 profile.yaml 失败:\n{e}").format(e=exc))
                return
            result_kd[0] = kd
            dialog.accept()

        btn_ok.clicked.connect(on_accept)

        if dialog.exec():
            return result_kd[0]
        return None

    # ─── 保存 ────────────────────────────────────────────────

    def _on_save(self):
        """只合并外层尚未保存的增删、分组和排序，不覆盖已保存的定义。"""
        from ...core.profile.maintenance import profile_lock
        from ...core.profile.schema import reload_profile_config, save_profile_config
        try:
            with profile_lock:
                current = deepcopy(reload_profile_config())
                for model_type in _MODEL_ORDER:
                    baseline = {kd.key: kd for kd in self._baseline[model_type]}
                    desired = {kd.key: kd for kd in self._drafts[model_type]}
                    latest = current.get_keys_by_model(model_type)
                    merged = [
                        replace(kd, group=desired[kd.key].group)
                        if kd.key in desired and kd.key in baseline and desired[kd.key].group != baseline[kd.key].group
                        else kd
                        for kd in latest if kd.key not in baseline or kd.key in desired
                    ]
                    latest_names = {kd.key for kd in latest}
                    for key, kd in desired.items():
                        if key not in latest_names:
                            if key not in baseline:
                                merged.append(deepcopy(kd))
                            elif kd != baseline[key]:
                                raise ValueError(tr("原 key 已不存在，请重新打开编辑对话框"))
                    if list(desired) != list(baseline):
                        by_key = {kd.key: kd for kd in merged}
                        ordered = iter(by_key[key] for key in desired if key in by_key)
                        merged = [next(ordered) if kd.key in desired else kd for kd in merged]
                    current.keys_by_model[model_type] = merged
                current._rebuild_index()
                save_profile_config(current)
                reload_profile_config()
            self.has_saved_changes = True
            self.accept()
        except Exception as e:
            logger.error(f"保存失败: {e}")
            QMessageBox.warning(self, tr("保存失败"), tr("保存 profile.yaml 失败:\n{e}").format(e=e))
