"""配置管理对话框（多 Tab）

Tab1 基础配置、Tab2 输入模拟（引擎级点击参数）、Tab3 等待参数（命名等待）。
所有配置写入 session.json（settings / input_delay 节点），
保存后以配置文件为准覆盖代码默认值。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget, QToolButton,
    QToolTip, QVBoxLayout, QWidget,
)

from ..config import load_user_config, save_input_delay, save_settings
from .widgets import NoWheelDoubleSpinBox, NoWheelSpinBox

# 引擎级点击参数（InputBackend 自动生效，不暴露 key）：(字段名, 显示标签, 用途说明)
# 二元组范围用 min~max 两个输入框，用途说明通过行尾「?」按钮点击查看
_RANGE_FIELDS = [
    ("before_click_wait", "点击前延迟(秒)", "每次点击前的随机等待，模拟人类反应时间"),
    ("after_click_wait", "点击后延迟(秒)", "每次点击后的随机等待"),
    ("mouse_move_duration", "鼠标移动时长(秒)", "鼠标/触控移动到目标位置的耗时"),
]

# 等待参数不可占用的保留 key（DelayConfig 引擎级固定字段）
_RESERVED_KEYS = {name for name, *_ in _RANGE_FIELDS} | {
    "click_random_offset", "region_jitter_ratio", "custom",
}

# 数值输入框统一定宽，避免被布局拉满整行
_SPIN_WIDTH = 90


class SettingsDialog(QDialog):
    """配置管理：Tab1 基础配置 + Tab2 输入模拟 + Tab3 等待参数"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("配置管理")
        self.setMinimumSize(720, 480)
        self.resize(760, 520)
        self._config = load_user_config()
        self._range_spins: dict[str, tuple[NoWheelDoubleSpinBox, NoWheelDoubleSpinBox]] = {}
        self._custom_rows: list[dict] = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._build_basic_tab(), "基础配置")
        tabs.addTab(self._build_input_tab(), "输入模拟")
        tabs.addTab(self._build_wait_tab(), "等待参数")
        layout.addWidget(tabs)

        # ── 底部按钮 ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    # ─── Tab1 基础配置 ─────────────────────────────────────

    def _build_basic_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        self._capture_combo = QComboBox()
        self._capture_combo.addItem("视频流截图 (scrcpy)", True)
        self._capture_combo.addItem("静态截图 (screencap)", False)
        self._capture_combo.setCurrentIndex(0 if self._config.adb_capture_streaming else 1)
        form.addRow("ADB 截图方式:", self._capture_combo)

        self._input_combo = QComboBox()
        self._input_combo.addItem("后台输入 (PostMessage)", True)
        self._input_combo.addItem("光标输入 (SendInput)", False)
        self._input_combo.setCurrentIndex(0 if self._config.desktop_background_input else 1)
        form.addRow("窗口输入模式:", self._input_combo)

        self._title_edit = QLineEdit(self._config.desktop_window_title)
        self._title_edit.setPlaceholderText("空串不自动定位窗口")
        form.addRow("默认窗口标题:", self._title_edit)

        return tab

    # ─── Tab2 输入模拟（引擎级点击参数）───────────────────

    def _build_input_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        delay = self._config.input_delay

        for name, label, tip in _RANGE_FIELDS:
            lo, hi = getattr(delay, name)
            form.addRow(f"{label}:", self._build_range_row(name, lo, hi, tip))

        self._offset_spin = NoWheelSpinBox()
        self._offset_spin.setRange(0, 50)
        self._offset_spin.setValue(delay.click_random_offset)
        self._offset_spin.setFixedWidth(_SPIN_WIDTH)
        form.addRow("坐标随机偏移(px):", self._spin_with_tip(
            self._offset_spin, "点击坐标附加 ±N 像素的随机偏移"))

        self._jitter_spin = NoWheelDoubleSpinBox()
        self._jitter_spin.setRange(0.0, 0.49)
        self._jitter_spin.setSingleStep(0.01)
        self._jitter_spin.setDecimals(2)
        self._jitter_spin.setValue(delay.region_jitter_ratio)
        self._jitter_spin.setFixedWidth(_SPIN_WIDTH)
        form.addRow("区域中心偏移比例:", self._spin_with_tip(
            self._jitter_spin,
            "区域内点击点相对中心的随机偏移比例（必须小于 0.5，防止偏出区域）"))

        return tab

    # ─── Tab3 等待参数（命名等待，供 wait 按 key 引用）────

    def _build_wait_tab(self) -> QWidget:
        tab = QWidget()
        vbox = QVBoxLayout(tab)

        caption = QLabel("工作流通过 wait <key>（DSL）或 wait_delay(key)（代码）按 key 引用，"
                         "在等待范围内随机取值。")
        caption.setWordWrap(True)
        vbox.addWidget(caption)

        # 表头与数据行共用同一 QGridLayout，保证列对齐
        self._custom_grid = QGridLayout()
        self._custom_grid.setColumnStretch(0, 3)
        self._custom_grid.setColumnStretch(1, 3)
        self._custom_grid.addWidget(QLabel("参数 key"), 0, 0)
        self._custom_grid.addWidget(QLabel("名称"), 0, 1)
        self._custom_grid.addWidget(QLabel("等待范围(秒)"), 0, 2, 1, 3)
        vbox.addLayout(self._custom_grid)

        for key, item in self._config.input_delay.custom.items():
            self._add_custom_row(key, item.label, *item.range)

        add_row = QHBoxLayout()
        add_btn = QPushButton("添加参数")
        add_btn.clicked.connect(lambda: self._add_custom_row())
        add_row.addWidget(add_btn)
        add_row.addStretch()
        vbox.addLayout(add_row)

        vbox.addStretch()
        return tab

    # ─── 通用控件 ──────────────────────────────────────────

    @staticmethod
    def _tip_button(tip: str) -> QToolButton:
        """创建「?」提示按钮，点击后在按钮旁弹出用途说明"""
        btn = QToolButton()
        btn.setText("?")
        btn.setFixedSize(20, 20)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: QToolTip.showText(QCursor.pos(), tip, btn))
        return btn

    def _spin_with_tip(self, spin, tip: str) -> QHBoxLayout:
        """单输入框 + 提示按钮的行布局"""
        row = QHBoxLayout()
        row.addWidget(spin)
        row.addWidget(self._tip_button(tip))
        row.addStretch()
        return row

    def _build_range_row(self, name: str, lo: float, hi: float, tip: str) -> QHBoxLayout:
        """构建 min~max 二元组输入行（定宽左对齐，行尾附提示按钮）"""
        lo_spin, hi_spin = self._make_range_spins(lo, hi)
        self._range_spins[name] = (lo_spin, hi_spin)
        row = QHBoxLayout()
        row.addWidget(lo_spin)
        row.addWidget(QLabel("~"))
        row.addWidget(hi_spin)
        row.addWidget(self._tip_button(tip))
        row.addStretch()
        return row

    @staticmethod
    def _make_range_spins(lo: float, hi: float) -> tuple[NoWheelDoubleSpinBox, NoWheelDoubleSpinBox]:
        """创建 min~max 一对输入框：max 下限跟随 min，输入期即阻止 max < min"""
        spins = []
        for value in (lo, hi):
            spin = NoWheelDoubleSpinBox()
            spin.setRange(0.0, 60.0)
            spin.setSingleStep(0.1)
            spin.setDecimals(2)
            spin.setValue(value)
            spin.setFixedWidth(_SPIN_WIDTH)
            spins.append(spin)
        lo_spin, hi_spin = spins
        hi_spin.setMinimum(lo_spin.value())
        lo_spin.valueChanged.connect(hi_spin.setMinimum)
        return lo_spin, hi_spin

    def _add_custom_row(self, key: str = "", label: str = "",
                        lo: float = 1.0, hi: float = 1.0):
        """向共享网格追加一行等待参数：key + 名称 + min~max + 删除按钮"""
        key_edit = QLineEdit(key)
        key_edit.setPlaceholderText("如 my_wait")
        key_edit.setMinimumWidth(100)

        label_edit = QLineEdit(label)
        label_edit.setPlaceholderText("显示名称")
        label_edit.setMinimumWidth(100)

        lo_spin, hi_spin = self._make_range_spins(lo, hi)

        entry = {"key": key_edit, "label": label_edit, "lo": lo_spin, "hi": hi_spin}
        del_btn = QPushButton("删除")
        del_btn.clicked.connect(lambda: self._remove_custom_row(entry))

        widgets = [key_edit, label_edit, lo_spin, QLabel("~"), hi_spin, del_btn]
        entry["widgets"] = widgets
        grid_row = self._custom_grid.rowCount()
        for col, widget in enumerate(widgets):
            self._custom_grid.addWidget(widget, grid_row, col)

        self._custom_rows.append(entry)

    def _remove_custom_row(self, entry: dict):
        self._custom_rows.remove(entry)
        for widget in entry["widgets"]:
            self._custom_grid.removeWidget(widget)
            widget.deleteLater()

    # ─── 保存 ──────────────────────────────────────────────

    def _collect_custom(self) -> dict | None:
        """收集等待参数，校验失败弹窗提示并返回 None"""
        custom: dict[str, dict] = {}
        for entry in self._custom_rows:
            key = entry["key"].text().strip()
            if not key:
                continue  # 空行忽略
            if not key.isidentifier():
                QMessageBox.warning(self, "配置管理",
                                    f"参数 key「{key}」无效：只能由字母/数字/下划线组成且不以数字开头")
                return None
            if key in _RESERVED_KEYS:
                QMessageBox.warning(self, "配置管理", f"参数 key「{key}」与引擎参数冲突")
                return None
            if key in custom:
                QMessageBox.warning(self, "配置管理", f"参数 key「{key}」重复")
                return None
            lo, hi = entry["lo"].value(), entry["hi"].value()
            if hi < lo:
                hi = lo
            custom[key] = {
                "label": entry["label"].text().strip(),
                "range": [round(lo, 2), round(hi, 2)],
            }
        return custom

    def _on_save(self):
        custom = self._collect_custom()
        if custom is None:
            return
        save_settings({
            "adb_capture_streaming": self._capture_combo.currentData(),
            "desktop_background_input": self._input_combo.currentData(),
            "desktop_window_title": self._title_edit.text().strip(),
        })
        delay: dict = {}
        for name, *_ in _RANGE_FIELDS:
            lo_spin, hi_spin = self._range_spins[name]
            lo, hi = lo_spin.value(), hi_spin.value()
            if hi < lo:
                hi = lo
            delay[name] = [round(lo, 2), round(hi, 2)]
        delay["click_random_offset"] = self._offset_spin.value()
        delay["region_jitter_ratio"] = round(self._jitter_spin.value(), 2)
        delay["custom"] = custom
        save_input_delay(delay)
        self.accept()
