"""玩家数据模型定义对话框

编辑 profile.yaml，按四种数据模型（日常/实时/资源/活动）分区管理 key 定义。
每个模型 Tab 内展示该类型的 key 列表，支持新增/删除/上移/下移。
新增/编辑通过弹出对话框完成，表单根据模型类型动态切换。
"""

from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
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
    MODEL_ACTIVITY,
    MODEL_DAILY,
    MODEL_LABELS,
    MODEL_REALTIME,
    MODEL_RESOURCE,
    ActivityKeyDef,
    DailyKeyDef,
    KeyDef,
    RealtimeKeyDef,
    ResourceKeyDef,
)

# 模型 TAB 顺序
_MODEL_ORDER = [MODEL_DAILY, MODEL_REALTIME, MODEL_RESOURCE, MODEL_ACTIVITY]

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
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(0, 160)

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

        info = QLabel("定义四种数据模型的 key。双击行可编辑。")
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
        if isinstance(kd, DailyKeyDef):
            parts = [f"周期:{kd.period}"]
            if kd.cap is not None:
                parts.append(f"上限:{kd.cap}")
            parts.append(f"重置:{kd.reset_time}")
            return ", ".join(parts)

        if isinstance(kd, RealtimeKeyDef):
            parts = [f"上限:{kd.cap}", f"回复:{kd.regen_rate}/min"]
            if kd.regen_daily:
                parts.append(f"日补:{kd.regen_daily}")
            if kd.alert_above:
                parts.append(f"提醒:>{kd.alert_above}")
            return ", ".join(parts)

        if isinstance(kd, ResourceKeyDef):
            return kd.source or ""

        if isinstance(kd, ActivityKeyDef):
            parts = [
                f"周期:{kd.period}",
                f"周限:{kd.period_cap}",
                f"总限:{kd.lifetime_cap}",
            ]
            if kd.alert_near_period_cap is not None:
                parts.append(f"周限提醒:{kd.alert_near_period_cap:.0%}")
            if kd.alert_near_lifetime_cap is not None:
                parts.append(f"总限提醒:{kd.alert_near_lifetime_cap:.0%}")
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
        key_input.setPlaceholderText("英文，如 niaoniao_of_week")
        layout.addRow("Key:", key_input)

        label_input = QLineEdit(existing.label if existing else "")
        label_input.setPlaceholderText("中文，如 袅袅(本周)")
        layout.addRow("标签:", label_input)

        source_input = QLineEdit(existing.source if existing else "")
        source_input.setPlaceholderText("可选，API 来源路径")
        layout.addRow("来源:", source_input)

        # 模型专属字段
        widgets: dict[str, QWidget] = {}

        if model_type == MODEL_DAILY:
            kd = existing if isinstance(existing, DailyKeyDef) else DailyKeyDef()
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
            layout.addRow("上限:", cap_spin)
            widgets["cap"] = cap_spin

            reset_input = QLineEdit(kd.reset_time)
            reset_input.setFixedWidth(80)
            layout.addRow("重置时刻:", reset_input)
            widgets["reset_time"] = reset_input

        elif model_type == MODEL_REALTIME:
            rt_kd = existing if isinstance(existing, RealtimeKeyDef) else RealtimeKeyDef()

            cap_spin = QSpinBox()
            cap_spin.setRange(0, 999999)
            cap_spin.setValue(rt_kd.cap or 0)
            layout.addRow("上限:", cap_spin)
            widgets["cap"] = cap_spin

            regen_spin = QDoubleSpinBox()
            regen_spin.setRange(0, 999)
            regen_spin.setDecimals(4)
            regen_spin.setSingleStep(0.01)
            regen_spin.setValue(rt_kd.regen_rate)
            regen_spin.setSuffix(" /min")
            layout.addRow("回复速率:", regen_spin)
            widgets["regen_rate"] = regen_spin

            daily_spin = QSpinBox()
            daily_spin.setRange(0, 99999)
            daily_spin.setValue(rt_kd.regen_daily)
            layout.addRow("每日补充:", daily_spin)
            widgets["regen_daily"] = daily_spin

            reset_input = QLineEdit(rt_kd.reset_time)
            reset_input.setFixedWidth(80)
            layout.addRow("重置时刻:", reset_input)
            widgets["reset_time"] = reset_input

            alert_spin = QSpinBox()
            alert_spin.setRange(0, 999999)
            alert_spin.setSpecialValueText("不提醒")
            alert_spin.setValue(rt_kd.alert_above or 0)
            layout.addRow("提醒阈值:", alert_spin)
            widgets["alert_above"] = alert_spin

        elif model_type == MODEL_RESOURCE:
            pass  # 只有 source，已在通用字段中

        elif model_type == MODEL_ACTIVITY:
            act_kd = existing if isinstance(existing, ActivityKeyDef) else ActivityKeyDef()

            period_combo = QComboBox()
            for val, text in _PERIOD_OPTIONS:
                period_combo.addItem(text, val)
            idx = period_combo.findData(act_kd.period)
            if idx >= 0:
                period_combo.setCurrentIndex(idx)
            layout.addRow("周期:", period_combo)
            widgets["period"] = period_combo

            period_cap_spin = QSpinBox()
            period_cap_spin.setRange(0, 999999)
            period_cap_spin.setValue(act_kd.period_cap)
            layout.addRow("周期限额:", period_cap_spin)
            widgets["period_cap"] = period_cap_spin

            lifetime_spin = QSpinBox()
            lifetime_spin.setRange(0, 9999999)
            lifetime_spin.setValue(act_kd.lifetime_cap)
            layout.addRow("总上限:", lifetime_spin)
            widgets["lifetime_cap"] = lifetime_spin

            reset_input = QLineEdit(act_kd.reset_time)
            reset_input.setFixedWidth(80)
            layout.addRow("重置时刻:", reset_input)
            widgets["reset_time"] = reset_input

            alert_period_spin = QDoubleSpinBox()
            alert_period_spin.setRange(0.0, 1.0)
            alert_period_spin.setDecimals(2)
            alert_period_spin.setSingleStep(0.05)
            alert_period_spin.setValue(act_kd.alert_near_period_cap if act_kd.alert_near_period_cap is not None else 0.0)
            alert_period_spin.setSpecialValueText("不提醒")
            layout.addRow("接近周限比例:", alert_period_spin)
            widgets["alert_near_period_cap"] = alert_period_spin

            alert_lifetime_spin = QDoubleSpinBox()
            alert_lifetime_spin.setRange(0.0, 1.0)
            alert_lifetime_spin.setDecimals(2)
            alert_lifetime_spin.setSingleStep(0.05)
            alert_lifetime_spin.setValue(act_kd.alert_near_lifetime_cap if act_kd.alert_near_lifetime_cap is not None else 0.0)
            alert_lifetime_spin.setSpecialValueText("不提醒")
            layout.addRow("接近总限比例:", alert_lifetime_spin)
            widgets["alert_near_lifetime_cap"] = alert_lifetime_spin

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
            source = source_input.text().strip()

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
            if model_type == MODEL_DAILY:
                cap_val = widgets["cap"].value()
                kd = DailyKeyDef(
                    key=key, label=label, source=source,
                    period=widgets["period"].currentData(),
                    cap=cap_val if cap_val > 0 else None,
                    reset_time=widgets["reset_time"].text().strip() or "05:00",
                )
            elif model_type == MODEL_REALTIME:
                alert_val = widgets["alert_above"].value()
                kd = RealtimeKeyDef(
                    key=key, label=label, source=source,
                    cap=widgets["cap"].value(),
                    regen_rate=widgets["regen_rate"].value(),
                    regen_daily=widgets["regen_daily"].value(),
                    reset_time=widgets["reset_time"].text().strip() or "05:00",
                    alert_above=alert_val if alert_val > 0 else None,
                )
            elif model_type == MODEL_RESOURCE:
                kd = ResourceKeyDef(key=key, label=label, source=source)
            elif model_type == MODEL_ACTIVITY:
                ap_val = widgets["alert_near_period_cap"].value()
                al_val = widgets["alert_near_lifetime_cap"].value()
                kd = ActivityKeyDef(
                    key=key, label=label, source=source,
                    period=widgets["period"].currentData(),
                    period_cap=widgets["period_cap"].value(),
                    lifetime_cap=widgets["lifetime_cap"].value(),
                    reset_time=widgets["reset_time"].text().strip() or "05:00",
                    alert_near_period_cap=ap_val if ap_val > 0 else None,
                    alert_near_lifetime_cap=al_val if al_val > 0 else None,
                )
            else:
                kd = KeyDef(key=key, label=label, source=source)

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
