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

from ..config.profile_models import (
    MODEL_LABELS,
    MODEL_QUOTA,
    MODEL_REGEN,
    MODEL_STOCK,
    KeyDef,
    QuotaKeyDef,
    RegenKeyDef,
    StockKeyDef,
)

# 模型 TAB 顺序
_MODEL_ORDER = [MODEL_QUOTA, MODEL_STOCK, MODEL_REGEN]

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
        from ..config import get_profile_config
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
                parts.append(f"幅度:{kd.steps}")
            if kd.sync_to:
                parts.append(f"同步:{kd.sync_to}")
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
                parts.append(f"幅度:{kd.steps}")
            if kd.alert_above:
                parts.append(f"提醒:>={kd.alert_above}")
            return ", ".join(parts)

        if isinstance(kd, StockKeyDef):
            parts = []
            if kd.cap is not None:
                cap_type = "软" if kd.soft else "硬"
                parts.append(f"{cap_type}上限:{kd.cap}")
            if kd.show_cap:
                parts.append("展示上限")
            if kd.steps:
                parts.append(f"幅度:{kd.steps}")
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
        dialog.setMinimumWidth(400)

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

            # 自定义增减幅度
            steps_input = QLineEdit(",".join(str(s) for s in kd.steps) if kd.steps else "")
            steps_input.setPlaceholderText("如: 1,10,100 或 -1")
            layout.addRow("增减幅度:", steps_input)
            widgets["steps"] = steps_input

            # 同步目标 Resource
            sync_combo = QComboBox()
            sync_combo.addItem("（不同步）", "")
            # 加载所有 Stock key 作为同步目标
            from ..config import get_profile_config
            res_keys = get_profile_config().get_keys_by_model(MODEL_STOCK)
            for rk in res_keys:
                sync_combo.addItem(f"{rk.label} ({rk.key})", rk.key)
            idx = sync_combo.findData(kd.sync_to)
            if idx >= 0:
                sync_combo.setCurrentIndex(idx)
            layout.addRow("同步到资源:", sync_combo)
            widgets["sync_to"] = sync_combo

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

            alert_spin = QSpinBox()
            alert_spin.setRange(0, 999999)
            alert_spin.setSpecialValueText("不提醒")
            alert_spin.setValue(rt_kd.alert_above or 0)
            layout.addRow("提醒阈值:", alert_spin)
            widgets["alert_above"] = alert_spin

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

            # 自定义增减幅度
            steps_input = QLineEdit(",".join(str(s) for s in rt_kd.steps) if rt_kd.steps else "")
            steps_input.setPlaceholderText("如: 1,10,100 或 -1")
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

            # 自定义增减幅度
            steps_input = QLineEdit(",".join(str(s) for s in res_kd.steps) if res_kd.steps else "")
            steps_input.setPlaceholderText("如: 1,10,100 或 -1")
            layout.addRow("增减幅度:", steps_input)
            widgets["steps"] = steps_input

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

            # 构造 KeyDef
            if model_type == MODEL_QUOTA:
                cap_val = widgets["cap"].value()
                # 解析 steps
                steps_raw = widgets["steps"].text().strip()
                steps_list: list[int] = []
                if steps_raw:
                    for part in steps_raw.split(","):
                        part = part.strip()
                        if part:
                            try:
                                steps_list.append(int(part))
                            except ValueError:
                                error_label.setText(f"增减幅度格式错误: '{part}'，请输入整数")
                                return
                # 解析 sync_to
                sync_to_val = widgets["sync_to"].currentData() or ""
                kd = QuotaKeyDef(
                    key=key, label=label,
                    period=widgets["period"].currentData(),
                    cap=cap_val if cap_val > 0 else None,
                    soft=widgets["soft"].isChecked(),
                    show_cap=widgets["show_cap"].isChecked(),
                    steps=steps_list,
                    sync_to=sync_to_val,
                    reset_time=widgets["reset_time"].text().strip() or "05:00",
                    reset_day=widgets["reset_day"].value(),
                    increment_only=widgets["increment_only"].isChecked(),
                )
            elif model_type == MODEL_REGEN:
                alert_val = widgets["alert_above"].value()
                # 解析 steps
                steps_raw = widgets["steps"].text().strip()
                steps_list: list[int] = []
                if steps_raw:
                    for part in steps_raw.split(","):
                        part = part.strip()
                        if part:
                            try:
                                steps_list.append(int(part))
                            except ValueError:
                                error_label.setText(f"增减幅度格式错误: '{part}'，请输入整数")
                                return
                kd = RegenKeyDef(
                    key=key, label=label,
                    cap=widgets["cap"].value(),
                    show_cap=widgets["show_cap"].isChecked(),
                    regen_period=widgets["regen_period"].currentData(),
                    regen_value=widgets["regen_value"].value(),
                    reset_time=widgets["reset_time"].text().strip() or "05:00",
                    reset_day=widgets["reset_day"].value(),
                    alert_above=alert_val if alert_val > 0 else None,
                    steps=steps_list,
                )
            elif model_type == MODEL_STOCK:
                cap_val = widgets["cap"].value()
                # 解析 steps
                steps_raw = widgets["steps"].text().strip()
                steps_list: list[int] = []
                if steps_raw:
                    for part in steps_raw.split(","):
                        part = part.strip()
                        if part:
                            try:
                                steps_list.append(int(part))
                            except ValueError:
                                error_label.setText(f"增减幅度格式错误: '{part}'，请输入整数")
                                return
                kd = StockKeyDef(
                    key=key, label=label,
                    cap=cap_val if cap_val > 0 else None,
                    soft=widgets["soft"].isChecked(),
                    show_cap=widgets["show_cap"].isChecked(),
                    steps=steps_list,
                )
            else:
                kd = KeyDef(key=key, label=label)

            result_kd[0] = kd
            dialog.accept()

        btn_ok.clicked.connect(on_accept)

        if dialog.exec():
            return result_kd[0]
        return None

    # ─── 保存 ────────────────────────────────────────────────

    def _on_save(self):
        """保存所有模型类型的 key 定义到 profile.yaml"""
        from ..config.user_profile import ProfileSchema, save_profile_config

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
