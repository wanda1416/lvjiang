"""配置管理对话框

基础配置写入 session.json 的 settings 节点，
延迟参数写入 workflows.yaml 的顶层 input_delay 节点（与 flows 同级）。
保存后以配置文件为准覆盖代码默认值。
"""

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from ..config import load_user_config, save_input_delay, save_settings
from .widgets import NoWheelDoubleSpinBox, NoWheelSpinBox

# 延迟参数定义：(字段名, 显示标签)。二元组范围型用 min~max 两个输入框
_RANGE_FIELDS = [
    ("before_click_wait", "点击前延迟(秒)"),
    ("after_click_wait", "点击后延迟(秒)"),
    ("mouse_move_duration", "鼠标移动时长(秒)"),
    ("step_interval", "步骤间等待(秒)"),
    ("click_interval", "连续点击间隔(秒)"),
    ("page_refresh_wait", "页面刷新等待(秒)"),
    ("scroll_settle_wait", "滚动惯性等待(秒)"),
    ("after_tune_wait", "调律结果等待(秒)"),
]


class SettingsDialog(QDialog):
    """配置管理：基础配置 + 延迟参数"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("配置管理")
        self.setMinimumWidth(420)
        self._config = load_user_config()
        self._range_spins: dict[str, tuple[NoWheelDoubleSpinBox, NoWheelDoubleSpinBox]] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ── 基础配置 ──
        basic_group = QGroupBox("基础配置")
        basic_form = QFormLayout(basic_group)

        self._capture_combo = QComboBox()
        self._capture_combo.addItem("视频流截图 (scrcpy)", True)
        self._capture_combo.addItem("静态截图 (screencap)", False)
        self._capture_combo.setCurrentIndex(0 if self._config.adb_capture_streaming else 1)
        basic_form.addRow("ADB 截图方式:", self._capture_combo)

        self._input_combo = QComboBox()
        self._input_combo.addItem("后台输入 (PostMessage)", True)
        self._input_combo.addItem("光标输入 (SendInput)", False)
        self._input_combo.setCurrentIndex(0 if self._config.desktop_background_input else 1)
        basic_form.addRow("窗口输入模式:", self._input_combo)

        self._title_edit = QLineEdit(self._config.desktop_window_title)
        self._title_edit.setPlaceholderText("空串不自动定位窗口")
        basic_form.addRow("默认窗口标题:", self._title_edit)

        layout.addWidget(basic_group)

        # ── 延迟参数 ──
        delay_group = QGroupBox("延迟参数（模拟人类操作）")
        delay_form = QFormLayout(delay_group)
        delay = self._config.input_delay

        for name, label in _RANGE_FIELDS:
            value = getattr(delay, name)
            lo, hi = (value, value) if isinstance(value, (int, float)) else value
            delay_form.addRow(f"{label}:", self._build_range_row(name, lo, hi))

        self._offset_spin = NoWheelSpinBox()
        self._offset_spin.setRange(0, 50)
        self._offset_spin.setValue(delay.click_random_offset)
        delay_form.addRow("坐标随机偏移(px):", self._offset_spin)

        self._jitter_spin = NoWheelDoubleSpinBox()
        self._jitter_spin.setRange(0.0, 0.49)
        self._jitter_spin.setSingleStep(0.01)
        self._jitter_spin.setDecimals(2)
        self._jitter_spin.setValue(delay.region_jitter_ratio)
        delay_form.addRow("区域中心偏移比例:", self._jitter_spin)

        layout.addWidget(delay_group)

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

    def _build_range_row(self, name: str, lo: float, hi: float) -> QHBoxLayout:
        """构建 min~max 二元组输入行"""
        lo_spin = NoWheelDoubleSpinBox()
        lo_spin.setRange(0.0, 60.0)
        lo_spin.setSingleStep(0.1)
        lo_spin.setDecimals(2)
        lo_spin.setValue(lo)
        hi_spin = NoWheelDoubleSpinBox()
        hi_spin.setRange(0.0, 60.0)
        hi_spin.setSingleStep(0.1)
        hi_spin.setDecimals(2)
        hi_spin.setValue(hi)
        self._range_spins[name] = (lo_spin, hi_spin)
        row = QHBoxLayout()
        row.addWidget(lo_spin)
        row.addWidget(QLabel("~"))
        row.addWidget(hi_spin)
        return row

    def _on_save(self):
        save_settings({
            "adb_capture_streaming": self._capture_combo.currentData(),
            "desktop_background_input": self._input_combo.currentData(),
            "desktop_window_title": self._title_edit.text().strip(),
        })
        delay: dict = {}
        for name, _ in _RANGE_FIELDS:
            lo_spin, hi_spin = self._range_spins[name]
            lo, hi = lo_spin.value(), hi_spin.value()
            if hi < lo:
                hi = lo
            delay[name] = [round(lo, 2), round(hi, 2)]
        delay["click_random_offset"] = self._offset_spin.value()
        delay["region_jitter_ratio"] = round(self._jitter_spin.value(), 2)
        save_input_delay(delay)
        self.accept()
