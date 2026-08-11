"""玩家数据模型定义对话框

编辑 profile.yaml，按三种数据模型（配额/存量/再生）分区管理 key 定义。
每个模型 Tab 内展示该类型的 key 列表，支持新增/删除/上移/下移。
新增/编辑通过弹出对话框完成，表单根据模型类型动态切换。
"""

from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
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

from ...config.profile_models import (
    ALL_MODELS,
    DIR_BOTH,
    DIRECTION_LABELS,
    MODEL_LABELS,
    MODEL_QUOTA,
    MODEL_REGEN,
    MODEL_STOCK,
    KeyDef,
    QuotaKeyDef,
    RegenKeyDef,
    StepDef,
    StockKeyDef,
    SyncTargetDef,
    format_sync_label,
)

# 模型 TAB 顺序
_MODEL_ORDER = [MODEL_QUOTA, MODEL_STOCK, MODEL_REGEN]


def _format_steps(steps: list[StepDef]) -> str:
    """steps 编辑框文本：有来源的条目显示 value:source"""
    return ",".join(f"{s.value}:{s.source}" if s.source else str(s.value) for s in steps)


def _parse_steps_text(raw: str) -> tuple[list[StepDef] | None, str]:
    """解析 steps 编辑框文本，如 '-900:打本消耗,-1100'

    返回 (steps, error_msg)；格式错误时 steps 为 None。
    """
    steps: list[StepDef] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            val_text, _, src_text = part.partition(":")
            val_text = val_text.strip()
            src_text = src_text.strip()
        else:
            val_text, src_text = part, ""
        try:
            value = int(val_text)
        except ValueError:
            return None, f"增减幅度格式错误: '{part}'，应为整数或 整数:来源"
        steps.append(StepDef(value=value, source=src_text))
    return steps, ""


def _parse_sources_text(raw: str) -> list[str]:
    """解析来源/用途词表编辑框文本（逗号分隔，去空去重保序）"""
    seen: set[str] = set()
    result: list[str] = []
    for part in raw.split(","):
        s = part.strip()
        if s and s not in seen:
            seen.add(s)
            result.append(s)
    return result


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
    return f"同步:{','.join(sync_parts)}"

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
        self._table.setHorizontalHeaderLabels(["目标", "倍率", "方向", "来源", ""])
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
        self._table.setColumnWidth(4, 30)

        layout.addWidget(self._table)

        btn_add = QPushButton("+ 添加同步目标")
        btn_add.setFixedWidth(120)
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
        from ...config import get_profile_config
        config = get_profile_config()

        combo = QComboBox()
        combo.addItem("（请选择）", "")
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
        source_input.setPlaceholderText("可选")
        self._table.setCellWidget(row, 3, source_input)

        # 删除按钮（点击时按 widget 反查行号，避免删行后行号错位）
        btn_remove = QPushButton("×")
        btn_remove.setFixedWidth(30)
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


# QTableWidgetItem.UserRole key：在表格首列存储完整 KeyDef 对象
_ROLE_KEYDEF = Qt.ItemDataRole.UserRole

# 周期选项
_PERIOD_OPTIONS = [
    ("day", "每天"),
    ("week", "每周"),
    ("month", "每月"),
    ("season", "赛季"),
    ("half_season", "半赛季"),
]

# 周几选项（isoweekday: 1=周一 ... 7=周日）
_WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


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

        btn_add = QPushButton("+ 新增")
        btn_add.setFixedWidth(70)
        btn_add.clicked.connect(self._add_key)
        toolbar.addWidget(btn_add)

        btn_del = QPushButton("- 删除")
        btn_del.setFixedWidth(70)
        btn_del.clicked.connect(self._delete_key)
        toolbar.addWidget(btn_del)

        btn_up = QPushButton("↑ 上移")
        btn_up.setFixedWidth(70)
        btn_up.clicked.connect(self._move_up)
        toolbar.addWidget(btn_up)

        btn_down = QPushButton("↓ 下移")
        btn_down.setFixedWidth(70)
        btn_down.clicked.connect(self._move_down)
        toolbar.addWidget(btn_down)

        layout.addLayout(toolbar)

        # key 表格
        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Key", "标签", "详情摘要"])
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.doubleClicked.connect(self._edit_key)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(0, 160)
        self._table.setColumnWidth(1, 100)

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
    """玩家数据模型定义对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("玩家数据模型定义")
        self.setMinimumSize(800, 550)
        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel("定义三种数据模型的 key。双击行可编辑。")
        info.setStyleSheet("color: #666666; margin-bottom: 10px;")
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

        btn_ok = QPushButton("确定")
        btn_ok.setFixedWidth(80)
        btn_ok.clicked.connect(self._on_save)
        btn_row.addWidget(btn_ok)

        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedWidth(80)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        layout.addLayout(btn_row)

    def _load_data(self):
        """从 ProfileSchema 加载并填充表格"""
        from ...config import get_profile_config
        config = get_profile_config()

        for model_type in _MODEL_ORDER:
            tab = self._tabs[model_type]
            keys = config.get_keys_by_model(model_type)
            tab.table.setRowCount(len(keys))
            for row, kd in enumerate(keys):
                key_item = QTableWidgetItem(kd.key)
                key_item.setData(_ROLE_KEYDEF, kd)
                tab.table.setItem(row, 0, key_item)
                tab.table.setItem(row, 1, QTableWidgetItem(kd.label))
                tab.table.setItem(row, 2, QTableWidgetItem(self._summarize(kd)))

    @staticmethod
    def _summarize(kd: KeyDef) -> str:
        """生成 key 的详情摘要"""
        if isinstance(kd, QuotaKeyDef):
            parts = [f"周期:{kd.period}"]
            if kd.reset_day and kd.period in ("week", "month"):
                if kd.period == "week" and 1 <= kd.reset_day <= 7:
                    parts.append(f"重置日:{_WEEKDAY_NAMES[kd.reset_day - 1]}")
                elif kd.period == "month" and 1 <= kd.reset_day <= 31:
                    parts.append(f"重置日:{kd.reset_day}号")
            if kd.cap is not None:
                cap_type = "软" if kd.soft else "硬"
                parts.append(f"{cap_type}上限:{kd.cap}")
            if kd.show_cap:
                parts.append("展示上限")
            if kd.increment_only:
                parts.append("单向增加")
            if kd.steps:
                parts.append(f"幅度:{_format_steps(kd.steps)}")
            sync_summary = _format_sync_summary(kd)
            if sync_summary:
                parts.append(sync_summary)
            if kd.sources:
                parts.append(f"来源:{','.join(kd.sources)}")
            if kd.uses:
                parts.append(f"用途:{','.join(kd.uses)}")
            parts.append(f"重置:{kd.reset_time}")
            return ", ".join(parts)

        if isinstance(kd, RegenKeyDef):
            parts = [f"上限:{kd.cap}"]
            period_labels = {"minute": "分钟", "hour": "小时", "day": "天", "week": "周"}
            period_text = period_labels.get(kd.regen_period, kd.regen_period)
            parts.append(f"回复:{kd.regen_value}/{period_text}")
            if kd.regen_period == "week" and kd.reset_day:
                if 1 <= kd.reset_day <= 7:
                    parts.append(f"重置日:{_WEEKDAY_NAMES[kd.reset_day - 1]}")
            if kd.regen_period in ("day", "week"):
                parts.append(f"重置:{kd.reset_time}")
            if kd.show_cap:
                parts.append("展示上限")
            if kd.steps:
                parts.append(f"幅度:{_format_steps(kd.steps)}")
            sync_summary = _format_sync_summary(kd)
            if sync_summary:
                parts.append(sync_summary)
            if kd.sources:
                parts.append(f"来源:{','.join(kd.sources)}")
            if kd.uses:
                parts.append(f"用途:{','.join(kd.uses)}")
            if kd.alert_orange:
                parts.append(f"橙警:>={kd.alert_orange}")
            if kd.alert_red:
                parts.append(f"红警:>={kd.alert_red}")
            return ", ".join(parts)

        if isinstance(kd, StockKeyDef):
            parts = []
            if kd.cap is not None:
                cap_type = "软" if kd.soft else "硬"
                parts.append(f"{cap_type}上限:{kd.cap}")
            if kd.show_cap:
                parts.append("展示上限")
            if kd.steps:
                parts.append(f"幅度:{_format_steps(kd.steps)}")
            sync_summary = _format_sync_summary(kd)
            if sync_summary:
                parts.append(sync_summary)
            if kd.sources:
                parts.append(f"来源:{','.join(kd.sources)}")
            if kd.uses:
                parts.append(f"用途:{','.join(kd.uses)}")
            if kd.description:
                parts.append(kd.description)
            return ", ".join(parts)

        return ""

    # ─── key 操作 ────────────────────────────────────────────

    def _add_key(self, model_type: str):
        """新增 key"""
        kd = self._open_edit_dialog(model_type, None)
        if kd is None:
            return

        tab = self._tabs[model_type]
        row = tab.table.rowCount()
        tab.table.setRowCount(row + 1)
        key_item = QTableWidgetItem(kd.key)
        key_item.setData(_ROLE_KEYDEF, kd)
        tab.table.setItem(row, 0, key_item)
        tab.table.setItem(row, 1, QTableWidgetItem(kd.label))
        tab.table.setItem(row, 2, QTableWidgetItem(self._summarize(kd)))

    def _edit_key(self, model_type: str, row: int):
        """编辑 key"""
        tab = self._tabs[model_type]
        key_item = tab.table.item(row, 0)
        if not key_item:
            return

        old_kd = key_item.data(_ROLE_KEYDEF)
        if old_kd is None:
            raise RuntimeError(f"行 {row} 缺少 KeyDef 数据，无法编辑")

        kd = self._open_edit_dialog(model_type, old_kd)
        if kd is None:
            return

        key_item = QTableWidgetItem(kd.key)
        key_item.setData(_ROLE_KEYDEF, kd)
        tab.table.setItem(row, 0, key_item)
        tab.table.setItem(row, 1, QTableWidgetItem(kd.label))
        tab.table.setItem(row, 2, QTableWidgetItem(self._summarize(kd)))

    def _delete_key(self, model_type: str, row: int):
        """删除 key"""
        tab = self._tabs[model_type]
        key_item = tab.table.item(row, 0)
        if not key_item:
            return

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除 key '{key_item.text()}' 吗？",
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

    def _open_edit_dialog(self, model_type: str, existing: KeyDef | None) -> KeyDef | None:
        """打开 key 编辑对话框，返回新的 KeyDef 或 None"""
        dialog = QDialog(self)
        title = "编辑" if existing else "新增"
        dialog.setWindowTitle(f"{title} Key ({MODEL_LABELS[model_type]})")
        dialog.setMinimumWidth(620)

        layout = QFormLayout(dialog)

        # 通用字段
        key_input = QLineEdit(existing.key if existing else "")
        key_input.setPlaceholderText("英文，如 ")
        layout.addRow("Key:", key_input)

        label_input = QLineEdit(existing.label if existing else "")
        label_input.setPlaceholderText("中文，如 袅袅(本周)")
        layout.addRow("标签:", label_input)

        # 模型专属字段
        widgets: dict[str, QWidget] = {}

        # 来源/用途词表（三种模型通用）：来源对应增加，用途对应减少
        sources_input = QLineEdit(",".join(existing.sources) if existing else "")
        sources_input.setPlaceholderText("逗号分隔，增加时供下拉选择，如: 打本,商店,任务")
        layout.addRow("来源:", sources_input)
        widgets["sources"] = sources_input

        uses_input = QLineEdit(",".join(existing.uses) if existing else "")
        uses_input.setPlaceholderText("逗号分隔，减少时供下拉选择，如: 兑换,强化,出售")
        layout.addRow("用途:", uses_input)
        widgets["uses"] = uses_input

        if model_type == MODEL_QUOTA:
            kd = existing if isinstance(existing, QuotaKeyDef) else QuotaKeyDef()
            period_combo = QComboBox()
            for val, text in _PERIOD_OPTIONS:
                period_combo.addItem(text, val)
            idx = period_combo.findData(kd.period)
            if idx >= 0:
                period_combo.setCurrentIndex(idx)
            layout.addRow("周期:", period_combo)
            widgets["period"] = period_combo

            cap_spin = QSpinBox()
            cap_spin.setRange(0, 999999)
            cap_spin.setSpecialValueText("无上限")
            cap_spin.setValue(kd.cap or 0)

            soft_check = QCheckBox("软上限")
            soft_check.setChecked(kd.soft)

            cap_row = QHBoxLayout()
            cap_row.addWidget(cap_spin)
            cap_row.addWidget(soft_check)
            cap_row.addStretch()
            layout.addRow("上限:", cap_row)
            widgets["cap"] = cap_spin
            widgets["soft"] = soft_check

            show_cap_check = QCheckBox("展示上限")
            show_cap_check.setChecked(kd.show_cap)
            layout.addRow(show_cap_check)
            widgets["show_cap"] = show_cap_check

            reset_input = QLineEdit(kd.reset_time)
            reset_input.setFixedWidth(80)
            layout.addRow("重置时刻:", reset_input)
            widgets["reset_time"] = reset_input

            reset_day_spin = QSpinBox()
            reset_day_spin.setRange(0, 31)
            reset_day_spin.setSpecialValueText("默认")
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
                    reset_day_label.setText("重置日(周几):")
                elif is_month:
                    reset_day_spin.setRange(0, 31)
                    reset_day_label.setText("重置日(几号):")
            period_combo.currentIndexChanged.connect(_update_reset_day_visibility)
            _update_reset_day_visibility()

            # 单向增加复选框
            increment_check = QCheckBox("单向增加")
            increment_check.setChecked(kd.increment_only)
            layout.addRow(increment_check)
            widgets["increment_only"] = increment_check

            # 自定义增减幅度（支持 value:来源）
            steps_input = QLineEdit(_format_steps(kd.steps))
            steps_input.setPlaceholderText("如: -900:打本消耗,-1100 或 1,10:商店")
            layout.addRow("增减幅度:", steps_input)
            widgets["steps"] = steps_input

        elif model_type == MODEL_REGEN:
            rt_kd = existing if isinstance(existing, RegenKeyDef) else RegenKeyDef()

            cap_spin = QSpinBox()
            cap_spin.setRange(0, 999999)
            cap_spin.setValue(rt_kd.cap or 0)
            layout.addRow("上限:", cap_spin)
            widgets["cap"] = cap_spin

            show_cap_check = QCheckBox("展示上限")
            show_cap_check.setChecked(rt_kd.show_cap)
            layout.addRow(show_cap_check)
            widgets["show_cap"] = show_cap_check

            regen_period_combo = QComboBox()
            regen_period_combo.addItem("分钟", "minute")
            regen_period_combo.addItem("小时", "hour")
            regen_period_combo.addItem("天", "day")
            regen_period_combo.addItem("周", "week")
            idx = regen_period_combo.findData(rt_kd.regen_period)
            if idx >= 0:
                regen_period_combo.setCurrentIndex(idx)
            layout.addRow("回复周期:", regen_period_combo)
            widgets["regen_period"] = regen_period_combo

            regen_value_spin = QDoubleSpinBox()
            regen_value_spin.setRange(0, 99999)
            regen_value_spin.setDecimals(4)
            regen_value_spin.setSingleStep(0.1)
            regen_value_spin.setValue(rt_kd.regen_value)
            layout.addRow("回复数值:", regen_value_spin)
            widgets["regen_value"] = regen_value_spin

            reset_input = QLineEdit(rt_kd.reset_time)
            reset_input.setFixedWidth(80)
            layout.addRow("重置时刻:", reset_input)
            widgets["reset_time"] = reset_input

            reset_day_spin = QSpinBox()
            reset_day_spin.setRange(0, 7)
            reset_day_spin.setSpecialValueText("默认")
            reset_day_spin.setValue(rt_kd.reset_day)
            reset_day_label = QLabel()
            widgets["reset_day"] = reset_day_spin
            widgets["reset_day_label"] = reset_day_label
            layout.addRow(reset_day_label, reset_day_spin)

            orange_spin = QSpinBox()
            orange_spin.setRange(0, 999999)
            orange_spin.setSpecialValueText("不提醒")
            orange_spin.setValue(rt_kd.alert_orange or 0)
            layout.addRow("橙色阈值:", orange_spin)
            widgets["alert_orange"] = orange_spin

            red_spin = QSpinBox()
            red_spin.setRange(0, 999999)
            red_spin.setSpecialValueText("不提醒")
            red_spin.setValue(rt_kd.alert_red or 0)
            layout.addRow("红色阈值:", red_spin)
            widgets["alert_red"] = red_spin

            def _update_reset_time_visibility():
                period = regen_period_combo.currentData()
                is_day_or_week = period in ("day", "week")
                is_week = period == "week"
                reset_input.setVisible(is_day_or_week)
                reset_day_spin.setVisible(is_week)
                reset_day_label.setVisible(is_week)
                # 更新标签
                label_widget = reset_input.parent().layout().labelForField(reset_input)
                if label_widget:
                    label_widget.setVisible(is_day_or_week)
                if is_week:
                    reset_day_label.setText("重置日(周几):")
            regen_period_combo.currentIndexChanged.connect(_update_reset_time_visibility)
            _update_reset_time_visibility()

            # 自定义增减幅度（支持 value:来源）
            steps_input = QLineEdit(_format_steps(rt_kd.steps))
            steps_input.setPlaceholderText("如: 1:任务奖励,10 或 -1")
            layout.addRow("增减幅度:", steps_input)
            widgets["steps"] = steps_input

        elif model_type == MODEL_STOCK:
            res_kd = existing if isinstance(existing, StockKeyDef) else StockKeyDef()

            cap_spin = QSpinBox()
            cap_spin.setRange(0, 999999)
            cap_spin.setSpecialValueText("无上限")
            cap_spin.setValue(res_kd.cap or 0)

            soft_check = QCheckBox("软上限")
            soft_check.setChecked(res_kd.soft)

            cap_row = QHBoxLayout()
            cap_row.addWidget(cap_spin)
            cap_row.addWidget(soft_check)
            cap_row.addStretch()
            layout.addRow("上限:", cap_row)
            widgets["cap"] = cap_spin
            widgets["soft"] = soft_check

            show_cap_check = QCheckBox("展示上限")
            show_cap_check.setChecked(res_kd.show_cap)
            layout.addRow(show_cap_check)
            widgets["show_cap"] = show_cap_check

            # 自定义增减幅度（支持 value:来源）
            steps_input = QLineEdit(_format_steps(res_kd.steps))
            steps_input.setPlaceholderText("如: 1:兑换,10 或 -1")
            layout.addRow("增减幅度:", steps_input)
            widgets["steps"] = steps_input

        # 同步目标动态列表（三种模型通用，下拉排除自身）
        sync_targets_widget = _SyncTargetsWidget(exclude_key_input=key_input)
        if existing and existing.sync_targets:
            for t in existing.sync_targets:
                sync_targets_widget.add_row(t)
        layout.addRow("同步目标:", sync_targets_widget)
        widgets["sync_targets"] = sync_targets_widget

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_ok = QPushButton("确定")
        btn_row.addWidget(btn_ok)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(dialog.reject)
        btn_row.addWidget(btn_cancel)
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
                error_label.setText("请输入 Key")
                return
            if not key.replace("_", "").isalnum():
                error_label.setText("Key 只能包含字母、数字和下划线")
                return
            if not label:
                error_label.setText("请输入标签")
                return

            # 检查 key 唯一性（排除自身）
            all_keys = set()
            for mt in _MODEL_ORDER:
                tab = self._tabs[mt]
                for row in range(tab.table.rowCount()):
                    item = tab.table.item(row, 0)
                    if item and item.text() != (existing.key if existing else ""):
                        all_keys.add(item.text())
            if key in all_keys:
                error_label.setText(f"Key '{key}' 已存在")
                return

            # 来源/用途词表
            sources_list = _parse_sources_text(widgets["sources"].text())
            uses_list = _parse_sources_text(widgets["uses"].text())

            # 收集同步目标（三种模型通用）
            sync_targets_list = widgets["sync_targets"].get_sync_targets()

            # 禁止同步目标指向自身（兼容：行已存在时 key 被改名的情况）
            self_sync_key = f"{model_type}:{key}"
            if any(t.key == self_sync_key for t in sync_targets_list):
                error_label.setText("同步目标不能指向自身")
                return

            # 构造 KeyDef
            if model_type == MODEL_QUOTA:
                cap_val = widgets["cap"].value()
                # 解析 steps（支持 value:来源）
                steps_list, steps_err = _parse_steps_text(widgets["steps"].text().strip())
                if steps_list is None:
                    error_label.setText(steps_err)
                    return
                kd = QuotaKeyDef(
                    key=key, label=label,
                    sources=sources_list,
                    uses=uses_list,
                    sync_targets=sync_targets_list,
                    period=widgets["period"].currentData(),
                    cap=cap_val if cap_val > 0 else None,
                    soft=widgets["soft"].isChecked(),
                    show_cap=widgets["show_cap"].isChecked(),
                    steps=steps_list,
                    reset_time=widgets["reset_time"].text().strip() or "05:00",
                    reset_day=widgets["reset_day"].value(),
                    increment_only=widgets["increment_only"].isChecked(),
                )
            elif model_type == MODEL_REGEN:
                orange_val = widgets["alert_orange"].value()
                red_val = widgets["alert_red"].value()
                # 解析 steps（支持 value:来源）
                steps_list, steps_err = _parse_steps_text(widgets["steps"].text().strip())
                if steps_list is None:
                    error_label.setText(steps_err)
                    return
                kd = RegenKeyDef(
                    key=key, label=label,
                    sources=sources_list,
                    uses=uses_list,
                    sync_targets=sync_targets_list,
                    cap=widgets["cap"].value(),
                    show_cap=widgets["show_cap"].isChecked(),
                    regen_period=widgets["regen_period"].currentData(),
                    regen_value=widgets["regen_value"].value(),
                    reset_time=widgets["reset_time"].text().strip() or "05:00",
                    reset_day=widgets["reset_day"].value(),
                    alert_orange=orange_val if orange_val > 0 else None,
                    alert_red=red_val if red_val > 0 else None,
                    steps=steps_list,
                )
            elif model_type == MODEL_STOCK:
                cap_val = widgets["cap"].value()
                # 解析 steps（支持 value:来源）
                steps_list, steps_err = _parse_steps_text(widgets["steps"].text().strip())
                if steps_list is None:
                    error_label.setText(steps_err)
                    return
                kd = StockKeyDef(
                    key=key, label=label,
                    sources=sources_list,
                    uses=uses_list,
                    sync_targets=sync_targets_list,
                    cap=cap_val if cap_val > 0 else None,
                    soft=widgets["soft"].isChecked(),
                    show_cap=widgets["show_cap"].isChecked(),
                    steps=steps_list,
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
        from ...config.user_profile import ProfileSchema, save_profile_config

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
            QMessageBox.warning(self, "保存失败", f"保存 profile.yaml 失败:\n{e}")
