"""用户 Profile 数据模型定义对话框

编辑 profile.yaml，按四种数据模型（配额/存量/再生/备注）分区管理 key 定义。
每个模型 Tab 内展示该类型的 key 列表，支持新增/删除/上移/下移。
新增/编辑通过弹出对话框完成，表单根据模型类型动态切换。
"""

from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.profile.models import (
    ALL_MODELS,
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
)
from ...core.profile.periods import get_profile_period, list_profile_periods
from ...i18n import tr
from ..button_styles import apply_button_style, fit_button_width

# 模型 TAB 顺序
_MODEL_ORDER = [MODEL_QUOTA, MODEL_STOCK, MODEL_REGEN, MODEL_NOTE]


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

        layout.addWidget(self._table)

        btn_add = QPushButton("+ " + tr("添加同步目标"))
        btn_add.setFixedWidth(120)
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

        combo = QComboBox()
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
        ratio_spin.setRange(-999.0, 999.0)
        ratio_spin.setDecimals(2)
        ratio_spin.setSingleStep(0.5)
        ratio_spin.setValue(target.ratio if target else 1.0)
        self._table.setCellWidget(row, 1, ratio_spin)

        # 方向限定
        direction_combo = QComboBox()
        for val, text in DIRECTION_LABELS.items():
            direction_combo.addItem(text, val)
        dir_idx = direction_combo.findData(target.direction if target else DIR_BOTH)
        if dir_idx >= 0:
            direction_combo.setCurrentIndex(dir_idx)
        self._table.setCellWidget(row, 2, direction_combo)

        # 来源（可选）
        source_input = QLineEdit(target.source if target else "")
        source_input.setPlaceholderText(tr("可选"))
        self._table.setCellWidget(row, 3, source_input)

        # 删除按钮（点击时按 widget 反查行号，避免删行后行号错位）
        btn_remove = QPushButton("×")
        # 36 而不是 30：套上带边框+内边距的统一样式后 30 会把「×」挤掉
        btn_remove.setFixedWidth(36)
        apply_button_style(btn_remove, variant="danger")
        btn_remove.clicked.connect(
            lambda _checked, b=btn_remove: self._remove_row(self._row_of_widget(b))
        )
        self._table.setCellWidget(row, 4, btn_remove)

    def _row_of_widget(self, widget: QWidget) -> int:
        """反查指定 cell widget 所在行（QTableWidget.row 只接受 QTableWidgetItem）"""
        for r in range(self._table.rowCount()):
            if self._table.cellWidget(r, 4) is widget:
                return r
        return -1

    def _remove_row(self, row: int) -> None:
        if row >= 0:
            self._table.removeRow(row)

    def get_sync_targets(self) -> list[SyncTargetDef]:
        """收集所有有效的同步目标"""
        targets: list[SyncTargetDef] = []
        for row in range(self._table.rowCount()):
            combo = self._table.cellWidget(row, 0)
            if not isinstance(combo, QComboBox):
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
                if isinstance(direction_combo, QComboBox) else DIR_BOTH
            )
            source = source_input.text().strip() if isinstance(source_input, QLineEdit) else ""
            targets.append(SyncTargetDef(key=key, ratio=ratio, direction=direction, source=source))
        return targets


class _ChangeRulesWidget(QWidget):
    """来源、用途与快捷幅度的结构化编辑器。

    UI 中的幅度始终显示为正数；用途在保存时转换为负数，来源转换为
    正数。底层仍序列化为现有 ``sources`` / ``uses`` / ``steps``，因此
    无需迁移 profile.yaml。
    """

    _KIND_USE = "use"
    _KIND_SOURCE = "source"

    def __init__(
        self,
        sources: list[str],
        uses: list[str],
        steps: list[StepDef],
        *,
        allow_steps: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._allow_steps = allow_steps

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget()
        self._table.setColumnCount(4 if allow_steps else 3)
        headers = [tr("类型"), tr("来源/用途")]
        if allow_steps:
            headers.append(tr("快捷数量"))
        headers.append("")
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(190)
        vertical_header = self._table.verticalHeader()
        if vertical_header is not None:
            vertical_header.setVisible(False)

        header = self._table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        if allow_steps:
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
            self._table.setColumnWidth(2, 120)
            self._table.setColumnWidth(3, 44)
        else:
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            self._table.setColumnWidth(2, 44)
        self._table.setColumnWidth(0, 110)
        layout.addWidget(self._table)

        buttons = QHBoxLayout()
        add_use = QPushButton("+ " + tr("添加用途"))
        add_source = QPushButton("+ " + tr("添加来源"))
        apply_button_style(add_use, add_source)
        fit_button_width(add_use, add_source, minimum=96)
        add_use.clicked.connect(lambda: self.add_row(self._KIND_USE))
        add_source.clicked.connect(lambda: self.add_row(self._KIND_SOURCE))
        buttons.addWidget(add_use)
        buttons.addWidget(add_source)
        buttons.addStretch()
        layout.addLayout(buttons)

        self._load(sources, uses, steps)

    def _load(self, sources: list[str], uses: list[str], steps: list[StepDef]) -> None:
        """合并旧配置的独立词表与 steps；用途固定排在来源之前。"""
        negative = [step for step in steps if step.value < 0]
        positive = [step for step in steps if step.value > 0]

        self._load_kind(self._KIND_USE, uses, negative)
        self._load_kind(self._KIND_SOURCE, sources, positive)

    def _load_kind(
        self,
        kind: str,
        vocabulary: list[str],
        steps: list[StepDef],
    ) -> None:
        consumed: set[int] = set()
        for name in vocabulary:
            matches = [
                (index, step)
                for index, step in enumerate(steps)
                if index not in consumed and step.source == name
            ]
            if matches:
                for index, step in matches:
                    consumed.add(index)
                    self.add_row(kind, name, abs(step.value))
            else:
                # 没有固定幅度的词条仍用于“自定义增加/减少”的候选列表。
                self.add_row(kind, name)

        # step 自带名称但没有登记进词表时也要显示，保存后自动归并。
        for index, step in enumerate(steps):
            if index not in consumed:
                self.add_row(kind, step.source, abs(step.value))

    def add_row(self, kind: str, name: str = "", amount: int = 0) -> None:
        row = self._table.rowCount()
        self._table.setRowCount(row + 1)

        kind_combo = QComboBox()
        kind_combo.addItem(tr("用途（减少）"), self._KIND_USE)
        kind_combo.addItem(tr("来源（增加）"), self._KIND_SOURCE)
        index = kind_combo.findData(kind)
        if index >= 0:
            kind_combo.setCurrentIndex(index)
        self._table.setCellWidget(row, 0, kind_combo)

        name_input = QLineEdit(name)
        name_input.setPlaceholderText(
            tr("如：和鸣抽奖") if kind == self._KIND_USE else tr("如：邮件赠送")
        )
        self._table.setCellWidget(row, 1, name_input)

        remove_column = 2
        if self._allow_steps:
            amount_spin = QSpinBox()
            amount_spin.setRange(0, 999999)
            amount_spin.setSpecialValueText(tr("仅词条"))
            amount_spin.setValue(amount)
            amount_spin.setToolTip(tr("0 表示仅作为自定义增减时的候选词条"))
            self._table.setCellWidget(row, 2, amount_spin)
            remove_column = 3

        remove_button = QPushButton("×")
        remove_button.setFixedWidth(36)
        apply_button_style(remove_button, variant="danger")
        remove_button.clicked.connect(
            lambda _checked, button=remove_button: self._remove_widget_row(button)
        )
        self._table.setCellWidget(row, remove_column, remove_button)

    def _remove_widget_row(self, widget: QWidget) -> None:
        remove_column = 3 if self._allow_steps else 2
        for row in range(self._table.rowCount()):
            if self._table.cellWidget(row, remove_column) is widget:
                self._table.removeRow(row)
                return

    def get_rules(self) -> tuple[list[str], list[str], list[StepDef]]:
        """返回去重词表与快捷幅度；用途 steps 始终排在来源之前。"""
        sources: list[str] = []
        uses: list[str] = []
        use_steps: list[StepDef] = []
        source_steps: list[StepDef] = []

        for row in range(self._table.rowCount()):
            kind_combo = self._table.cellWidget(row, 0)
            name_input = self._table.cellWidget(row, 1)
            if not isinstance(kind_combo, QComboBox) or not isinstance(name_input, QLineEdit):
                continue
            kind = kind_combo.currentData()
            name = name_input.text().strip()
            vocabulary = uses if kind == self._KIND_USE else sources
            if name and name not in vocabulary:
                vocabulary.append(name)

            if not self._allow_steps:
                continue
            amount_spin = self._table.cellWidget(row, 2)
            amount = amount_spin.value() if isinstance(amount_spin, QSpinBox) else 0
            if amount <= 0:
                continue
            step = StepDef(
                value=-amount if kind == self._KIND_USE else amount,
                source=name,
            )
            (use_steps if kind == self._KIND_USE else source_steps).append(step)

        return sources, uses, use_steps + source_steps

    def validation_error(self) -> str:
        """检查快捷幅度是否已绑定名称、是否存在完全重复的规则。"""
        seen: set[tuple[str, str, int]] = set()
        for row in range(self._table.rowCount()):
            kind_combo = self._table.cellWidget(row, 0)
            name_input = self._table.cellWidget(row, 1)
            if not isinstance(kind_combo, QComboBox) or not isinstance(name_input, QLineEdit):
                continue
            name = name_input.text().strip()
            amount = 0
            if self._allow_steps:
                amount_spin = self._table.cellWidget(row, 2)
                amount = amount_spin.value() if isinstance(amount_spin, QSpinBox) else 0
            if amount > 0 and not name:
                return tr("变动规则第 {row} 行设置了快捷数量，请填写来源或用途").format(
                    row=row + 1
                )
            if not name:
                continue
            identity = (str(kind_combo.currentData()), name, amount)
            if identity in seen:
                return tr("变动规则存在重复项：{name}（{amount}）").format(
                    name=name,
                    amount=amount if amount > 0 else tr("仅词条"),
                )
            seen.add(identity)
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
        toolbar.addStretch()

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
        # Quota: Key | 标签 | 上限 | 周期 | 来源 | 详情摘要
        # Regen: Key | 标签 | 上限 | 周期 | 用途 | 详情摘要
        # Stock: Key | 标签 | 上限 | 来源 | 用途 | 详情摘要
        # Note:  Key | 标签 | 上限 | 来源/用途 | 详情摘要
        self._table = QTableWidget()
        if self._model_type == MODEL_QUOTA:
            self._table.setColumnCount(6)
            self._table.setHorizontalHeaderLabels(["Key", tr("标签"), tr("上限"), tr("周期"), tr("来源"), tr("详情摘要")])
        elif self._model_type == MODEL_REGEN:
            self._table.setColumnCount(6)
            self._table.setHorizontalHeaderLabels(["Key", tr("标签"), tr("上限"), tr("周期"), tr("用途"), tr("详情摘要")])
        elif self._model_type == MODEL_NOTE:
            self._table.setColumnCount(5)
            self._table.setHorizontalHeaderLabels(["Key", tr("标签"), tr("上限"), tr("来源/用途"), tr("详情摘要")])
        else:
            self._table.setColumnCount(6)
            self._table.setHorizontalHeaderLabels(["Key", tr("标签"), tr("上限"), tr("来源"), tr("用途"), tr("详情摘要")])
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.doubleClicked.connect(self._edit_key)

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
        header.setMinimumSectionSize(min_col_width)
        if self._model_type == MODEL_NOTE:
            # Note: 来源/用途: 固定, 详情摘要: 拉伸
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
            self._table.setColumnWidth(3, 200)
        elif self._model_type == MODEL_STOCK:
            # 来源/用途: 固定, 详情摘要: 拉伸
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
            header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
            self._table.setColumnWidth(3, 150)
            self._table.setColumnWidth(4, 150)
        else:
            # Quota/Regen: 周期: 自适应, 来源或用途: 固定, 详情摘要: 拉伸
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
            header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
            self._table.setColumnWidth(4, 150)

        layout.addWidget(self._table)

    @property
    def table(self) -> QTableWidget:
        return self._table

    @property
    def model_type(self) -> str:
        return self._model_type

    def _get_parent(self) -> "ProfileDefinitionDialog | None":
        parent = self.parent()
        while parent and not isinstance(parent, ProfileDefinitionDialog):
            parent = parent.parent()
        return parent if isinstance(parent, ProfileDefinitionDialog) else None

    def _add_key(self):
        dialog = self._get_parent()
        if dialog:
            dialog._add_key(self._model_type)

    def _edit_key(self):
        dialog = self._get_parent()
        if dialog:
            row = self._table.currentRow()
            if row >= 0:
                dialog._edit_key(self._model_type, row)

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


class ProfileDefinitionDialog(QDialog):
    """用户 Profile 数据模型定义对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("用户数据模型定义"))
        self.setMinimumSize(800, 550)
        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(tr("定义数据模型的 key。双击行可编辑。"))
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

        btn_ok = QPushButton(tr("确定"))
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
            tab = self._tabs[model_type]
            keys = config.get_keys_by_model(model_type)
            tab.table.setRowCount(len(keys))
            for row, kd in enumerate(keys):
                self._populate_row(tab, row, kd)

    @staticmethod
    def _populate_row(tab: _ModelTab, row: int, kd: KeyDef) -> None:
        """填充一行数据到表格"""
        key_item = QTableWidgetItem(kd.key)
        key_item.setData(_ROLE_KEYDEF, kd)
        tab.table.setItem(row, 0, key_item)
        tab.table.setItem(row, 1, QTableWidgetItem(kd.label))
        tab.table.setItem(row, 2, QTableWidgetItem(_format_cap(kd)))
        if tab.model_type == MODEL_QUOTA:
            # Key | 标签 | 上限 | 周期 | 来源 | 详情摘要
            tab.table.setItem(row, 3, QTableWidgetItem(_format_period(kd)))
            tab.table.setItem(row, 4, QTableWidgetItem(",".join(kd.sources)))
            tab.table.setItem(row, 5, QTableWidgetItem(ProfileDefinitionDialog._summarize(kd)))
        elif tab.model_type == MODEL_REGEN:
            # Key | 标签 | 上限 | 周期 | 用途 | 详情摘要
            tab.table.setItem(row, 3, QTableWidgetItem(_format_period(kd)))
            tab.table.setItem(row, 4, QTableWidgetItem(",".join(kd.uses)))
            tab.table.setItem(row, 5, QTableWidgetItem(ProfileDefinitionDialog._summarize(kd)))
        elif tab.model_type == MODEL_NOTE:
            # Key | 标签 | 上限 | 来源/用途 | 详情摘要
            combined = list(dict.fromkeys(kd.sources + kd.uses))
            tab.table.setItem(row, 3, QTableWidgetItem(",".join(combined)))
            tab.table.setItem(row, 4, QTableWidgetItem(ProfileDefinitionDialog._summarize(kd)))
        else:
            # Key | 标签 | 上限 | 来源 | 用途 | 详情摘要
            tab.table.setItem(row, 3, QTableWidgetItem(",".join(kd.sources)))
            tab.table.setItem(row, 4, QTableWidgetItem(",".join(kd.uses)))
            tab.table.setItem(row, 5, QTableWidgetItem(ProfileDefinitionDialog._summarize(kd)))

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
        kd = self.open_key_editor(
            self, model_type, None, self._defined_key_names())
        if kd is None:
            return

        tab = self._tabs[model_type]
        row = tab.table.rowCount()
        tab.table.setRowCount(row + 1)
        self._populate_row(tab, row, kd)

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

        self._populate_row(tab, row, kd)

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

        tab.table.removeRow(row)

    def _swap_keys(self, model_type: str, a: int, b: int):
        """交换两行"""
        tab = self._tabs[model_type]
        for col in range(tab.table.columnCount()):
            item_a = tab.table.takeItem(a, col)
            item_b = tab.table.takeItem(b, col)
            tab.table.setItem(a, col, item_b)
            tab.table.setItem(b, col, item_a)

    # ─── 编辑对话框 ──────────────────────────────────────────

    def _defined_key_names(self) -> set[str]:
        """返回当前总定义窗口中的所有 key。"""
        result: set[str] = set()
        for model_type in _MODEL_ORDER:
            tab = self._tabs[model_type]
            for row in range(tab.table.rowCount()):
                item = tab.table.item(row, 0)
                if item:
                    result.add(item.text())
        return result

    @staticmethod
    def open_key_editor(
        parent: QWidget,
        model_type: str,
        existing: KeyDef | None,
        known_keys: set[str],
        *,
        lock_key: bool = False,
    ) -> KeyDef | None:
        """打开 key 编辑对话框，返回新的 KeyDef 或 None"""
        dialog = QDialog(parent)
        title = tr("编辑") if existing else tr("新增")
        dialog.setWindowTitle(tr("{title} Key ({model})").format(title=title, model=MODEL_LABELS[model_type]))
        dialog.setMinimumWidth(620)

        layout = QFormLayout(dialog)

        # 通用字段
        key_input = QLineEdit(existing.key if existing else "")
        key_input.setPlaceholderText(tr("英文，如 "))
        key_input.setReadOnly(lock_key)
        layout.addRow("Key:", key_input)

        label_input = QLineEdit(existing.label if existing else "")
        label_input.setPlaceholderText(tr("中文，如 周任务"))
        layout.addRow(tr("标签:"), label_input)

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

        if model_type == MODEL_QUOTA:
            kd = existing if isinstance(existing, QuotaKeyDef) else QuotaKeyDef()
            period_combo = QComboBox()
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

            regen_type_combo = QComboBox()
            regen_type_combo.addItem(tr("实时恢复"), "realtime")
            regen_type_combo.addItem(tr("准点恢复"), "boundary")
            idx = regen_type_combo.findData(rt_kd.regen_type)
            if idx >= 0:
                regen_type_combo.setCurrentIndex(idx)
            layout.addRow(tr("恢复类型:"), regen_type_combo)
            widgets["regen_type"] = regen_type_combo

            regen_period_combo = QComboBox()
            regen_period_combo.addItem(tr("分钟"), "minute")
            regen_period_combo.addItem(tr("小时"), "hour")
            regen_period_combo.addItem(tr("天"), "day")
            regen_period_combo.addItem(tr("周"), "week")
            idx = regen_period_combo.findData(rt_kd.regen_period)
            if idx >= 0:
                regen_period_combo.setCurrentIndex(idx)
            layout.addRow(tr("准点周期:"), regen_period_combo)
            widgets["regen_period"] = regen_period_combo

            regen_rate_unit_combo = QComboBox()
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

        existing_steps = (
            existing.steps
            if isinstance(existing, (QuotaKeyDef, RegenKeyDef, StockKeyDef))
            else []
        )
        change_rules_widget = _ChangeRulesWidget(
            existing.sources if existing else [],
            existing.uses if existing else [],
            existing_steps,
            allow_steps=model_type != MODEL_NOTE,
        )
        layout.addRow(tr("变动规则:"), change_rules_widget)
        widgets["change_rules"] = change_rules_widget

        # 同步目标动态列表（三种模型通用，下拉排除自身）
        sync_targets_widget = _SyncTargetsWidget(exclude_key_input=key_input)
        if existing and existing.sync_targets:
            for t in existing.sync_targets:
                sync_targets_widget.add_row(t)
        layout.addRow(tr("同步目标:"), sync_targets_widget)
        widgets["sync_targets"] = sync_targets_widget

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_ok = QPushButton(tr("保存") if lock_key else tr("确定"))
        btn_row.addWidget(btn_ok)
        btn_cancel = QPushButton(tr("取消"))
        btn_cancel.clicked.connect(dialog.reject)
        btn_row.addWidget(btn_cancel)
        apply_button_style(btn_ok)
        apply_button_style(btn_cancel, variant="neutral")
        layout.addRow(btn_row)

        # 错误提示
        error_label = QLabel()
        error_label.setStyleSheet("color: red;")
        layout.addRow(error_label)

        result_kd: list[KeyDef | None] = [None]

        def on_accept():
            key = key_input.text().strip()
            label = label_input.text().strip()

            if not key:
                error_label.setText(tr("请输入 Key"))
                return
            if not key.replace("_", "").isalnum():
                error_label.setText(tr("Key 只能包含字母、数字和下划线"))
                return
            if not label:
                error_label.setText(tr("请输入标签"))
                return

            # 检查 key 唯一性（排除自身）
            all_keys = set(known_keys)
            if existing:
                all_keys.discard(existing.key)
            if key in all_keys:
                error_label.setText(tr("Key '{key}' 已存在").format(key=key))
                return

            # 来源、用途和快捷幅度由同一个结构化编辑器生成，避免三份配置漂移。
            change_rules = widgets["change_rules"]
            assert isinstance(change_rules, _ChangeRulesWidget)
            rules_error = change_rules.validation_error()
            if rules_error:
                error_label.setText(rules_error)
                return
            sources_list, uses_list, steps_list = change_rules.get_rules()

            # 收集同步目标（三种模型通用）
            sync_targets_list = widgets["sync_targets"].get_sync_targets()

            # 禁止同步目标指向自身（兼容：行已存在时 key 被改名的情况）
            self_sync_key = f"{model_type}:{key}"
            if any(t.key == self_sync_key for t in sync_targets_list):
                error_label.setText(tr("同步目标不能指向自身"))
                return

            # 通用上限字段（三种模型通用）
            cap_val = widgets["cap"].value()
            cap_final = cap_val if cap_val > 0 else None
            soft_final = widgets["soft"].isChecked()
            show_cap_final = widgets["show_cap"].isChecked()
            decimal_final = widgets["decimal"].isChecked()

            # 构造 KeyDef
            if model_type == MODEL_QUOTA:
                kd = QuotaKeyDef(
                    key=key, label=label,
                    sources=sources_list,
                    uses=uses_list,
                    sync_targets=sync_targets_list,
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
                    key=key, label=label,
                    sources=sources_list,
                    uses=uses_list,
                    sync_targets=sync_targets_list,
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
                    key=key, label=label,
                    sources=sources_list,
                    uses=uses_list,
                    sync_targets=sync_targets_list,
                    cap=cap_final,
                    soft=soft_final,
                    show_cap=show_cap_final,
                    decimal=decimal_final,
                    steps=steps_list,
                )
            elif model_type == MODEL_NOTE:
                kd = NoteKeyDef(
                    key=key, label=label,
                    sources=sources_list,
                    uses=uses_list,
                    sync_targets=sync_targets_list,
                    cap=cap_final,
                    soft=soft_final,
                    show_cap=show_cap_final,
                    decimal=decimal_final,
                )
            else:
                kd = KeyDef(key=key, label=label, sources=sources_list, uses=uses_list)

            result_kd[0] = kd
            dialog.accept()

        btn_ok.clicked.connect(on_accept)

        if dialog.exec():
            return result_kd[0]
        return None

    # ─── 保存 ────────────────────────────────────────────────

    def _on_save(self):
        """保存所有模型类型的 key 定义到 profile.yaml"""
        from ...core.profile.schema import ProfileSchema, save_profile_config

        keys_by_model: dict[str, list[KeyDef]] = {}

        for model_type in _MODEL_ORDER:
            tab = self._tabs[model_type]
            key_defs: list[KeyDef] = []

            for row in range(tab.table.rowCount()):
                key_item = tab.table.item(row, 0)
                if not key_item:
                    continue
                kd = key_item.data(_ROLE_KEYDEF)
                if kd is None:
                    raise RuntimeError(f"行 {row} 缺少 KeyDef 数据，无法保存")
                key_defs.append(kd)

            keys_by_model[model_type] = key_defs

        schema = ProfileSchema(keys_by_model=keys_by_model)

        try:
            save_profile_config(schema)
            self.accept()
        except Exception as e:
            logger.error(f"保存失败: {e}")
            QMessageBox.warning(self, tr("保存失败"), tr("保存 profile.yaml 失败:\n{e}").format(e=e))
