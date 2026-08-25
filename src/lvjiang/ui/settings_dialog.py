"""配置管理对话框（多 Tab）

Tab1 基础配置、Tab2 输入模拟（引擎级点击参数）、Tab3 等待参数（命名等待）、
Tab4 系统参数（可用工作环境）、Tab5 热键设置（F7~F12 按键位）。
Tab1/Tab5 写 session.json（settings 节点）；Tab2/Tab3/Tab4 写 app.yaml（input_simulation / delay_params / envs，
system ← local 合并），保存后以配置文件为准覆盖代码默认值。
Tab5 修改的热键保存后立即重建全局监听并生效。
"""

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QCursor, QDesktopServices
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ..core.config import (
    load_available_envs,
    load_user_config,
    save_app_config,
    save_settings,
)
from ..i18n import tr
from .button_styles import apply_button_style

# 引擎级点击参数（InputBackend 自动生效，不暴露 key）：(字段名, 显示标签, 用途说明)
# 二元组范围用 min~max 两个输入框，用途说明通过行尾「?」按钮点击查看
_RANGE_FIELDS = [
    ("before_click_wait", tr("点击前延迟(秒)"), tr("每次点击前的随机等待，模拟人类反应时间")),
    ("after_click_wait", tr("点击后延迟(秒)"), tr("每次点击后的随机等待")),
    ("mouse_move_duration", tr("鼠标移动时长(秒)"), tr("鼠标/触控移动到目标位置的耗时")),
]

# 等待参数不可占用的保留 key（InputSimConfig 引擎级固定字段）
_RESERVED_KEYS = {name for name, *_ in _RANGE_FIELDS} | {
    "click_random_offset", "region_jitter_ratio",
}

# 热键设置：HotkeyConfig 字段名 → 显示标签
_HOTKEY_FIELDS = [
    ("start", tr("开始执行 / 开始调律")),
    ("pause", tr("暂停 / 恢复")),
    ("stop", tr("停止 / 结束")),
    ("record", tr("脚本录制")),
]
_HOTKEY_CHOICES = [f"F{i}" for i in range(7, 13)]

# 数值输入框统一定宽，避免被布局拉满整行
_SPIN_WIDTH = 90
# 热键候选最长为 F10/F11/F12；显式定宽，避免“脚本录制”行与提示按钮
# 共用子布局时 QComboBox 被压缩到只剩下部分文字。
_HOTKEY_COMBO_WIDTH = 90


class SettingsDialog(QDialog):
    """配置管理：Tab1 基础配置 + Tab2 输入模拟 + Tab3 等待参数"""

    hotkeys_saved = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("配置管理"))
        self.setMinimumSize(720, 480)
        self.resize(760, 520)
        self._config = load_user_config()
        self._range_spins: dict[str, tuple[QDoubleSpinBox, QDoubleSpinBox]] = {}
        self._custom_rows: list[dict] = []
        self._setup_ui()
        self._connect_dirty_signals()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._build_basic_tab(), tr("基础配置"))
        tabs.addTab(self._build_input_tab(), tr("输入模拟"))
        tabs.addTab(self._build_wait_tab(), tr("等待参数"))
        tabs.addTab(self._build_env_tab(), tr("系统参数"))
        tabs.addTab(self._build_hotkey_tab(), tr("热键设置"))
        tabs.addTab(self._build_privacy_tab(), tr("网络与隐私"))
        layout.addWidget(tabs)

        # ── 底部按钮：保存（左）与关闭（右）隔开，语义不同 ──
        # 保存默认置灰，参数发生变更后启用；保存后不关闭对话框，可继续修改
        btn_row = QHBoxLayout()
        self._save_btn = QPushButton(tr("保存"))
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)
        btn_row.addStretch()
        self._close_btn = QPushButton(tr("关闭"))
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)
        apply_button_style(self._save_btn)
        apply_button_style(self._close_btn, variant="neutral")
        layout.addLayout(btn_row)

    # ─── 脏状态跟踪 ──────────────────────────────────

    def _mark_dirty(self, *_args):
        """任意参数变更后启用保存按钮"""
        self._save_btn.setEnabled(True)

    def _connect_dirty_signals(self):
        """UI 构建完成后统一连接变更信号（避免初始赋值触发）"""
        self._lang_combo.currentIndexChanged.connect(self._mark_dirty)
        self._capture_stream_radio.toggled.connect(self._mark_dirty)
        self._capture_static_radio.toggled.connect(self._mark_dirty)
        self._android_input_adb_radio.toggled.connect(self._mark_dirty)
        self._android_input_agent_radio.toggled.connect(self._mark_dirty)
        self._input_combo.currentIndexChanged.connect(self._mark_dirty)
        self._title_edit.textChanged.connect(self._mark_dirty)
        self._offset_spin.valueChanged.connect(self._mark_dirty)
        self._jitter_spin.valueChanged.connect(self._mark_dirty)
        for lo_spin, hi_spin in self._range_spins.values():
            lo_spin.valueChanged.connect(self._mark_dirty)
            hi_spin.valueChanged.connect(self._mark_dirty)
        for entry in self._custom_rows:
            self._connect_row_dirty(entry)
        for combo in self._hotkey_combos.values():
            combo.currentIndexChanged.connect(self._mark_dirty)

    def _connect_row_dirty(self, entry: dict):
        """连接单行等待参数的变更信号"""
        entry["key"].textChanged.connect(self._mark_dirty)
        entry["label"].textChanged.connect(self._mark_dirty)
        entry["lo"].valueChanged.connect(self._mark_dirty)
        entry["hi"].valueChanged.connect(self._mark_dirty)

    # ─── Tab1 基础配置 ─────────────────────────────────────

    def _build_basic_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        # ── 语言选择 ──
        from ..i18n import available_languages, current_language
        self._lang_combo = QComboBox()
        languages = available_languages()
        for lang in languages:
            self._lang_combo.addItem(f"{lang['name']} ({lang['code']})", lang["code"])
        current = current_language()
        idx = self._lang_combo.findData(current)
        if idx >= 0:
            self._lang_combo.setCurrentIndex(idx)
        form.addRow(tr("界面语言") + ":", self._lang_combo)

        capture_row = QWidget()
        capture_layout = QHBoxLayout(capture_row)
        capture_layout.setContentsMargins(0, 0, 0, 0)
        self._capture_group = QButtonGroup(self)
        self._capture_stream_radio = QRadioButton(tr("scrcpy 视频流"))
        self._capture_static_radio = QRadioButton(tr("ADB screencap 静态截图"))
        self._capture_group.addButton(self._capture_stream_radio)
        self._capture_group.addButton(self._capture_static_radio)
        self._capture_stream_radio.setChecked(
            self._config.android_capture_method == "scrcpy")
        self._capture_static_radio.setChecked(
            self._config.android_capture_method == "screencap")
        capture_layout.addWidget(self._capture_stream_radio)
        capture_layout.addWidget(self._capture_static_radio)
        capture_layout.addStretch()
        form.addRow(tr("安卓截图方式:"), capture_row)

        input_row = QWidget()
        input_layout = QHBoxLayout(input_row)
        input_layout.setContentsMargins(0, 0, 0, 0)
        self._android_input_group = QButtonGroup(self)
        self._android_input_adb_radio = QRadioButton(tr("ADB shell input"))
        self._android_input_agent_radio = QRadioButton(
            tr("设备端手势（Beta，需安装律匠 App）"))
        self._android_input_group.addButton(self._android_input_adb_radio)
        self._android_input_group.addButton(self._android_input_agent_radio)
        self._android_input_adb_radio.setChecked(
            self._config.android_input_method == "adb")
        self._android_input_agent_radio.setChecked(
            self._config.android_input_method == "device_gesture")
        agent_tip = tr(
            "实验功能：手机需安装律匠 App 并开启无障碍服务；"
            "仅改变点击和滑动的执行通道，不改变截图方式。连接失败时回退 ADB shell input。")
        self._android_input_agent_radio.setToolTip(agent_tip)
        input_row.setToolTip(agent_tip)
        input_layout.addWidget(self._android_input_adb_radio)
        input_layout.addWidget(self._android_input_agent_radio)
        input_layout.addStretch()
        form.addRow(tr("安卓输入方式:"), input_row)

        self._input_combo = QComboBox()
        self._input_combo.addItem(tr("后台输入 (PostMessage)"), True)
        self._input_combo.addItem(tr("光标输入 (SendInput)"), False)
        self._input_combo.setCurrentIndex(0 if self._config.desktop_background_input else 1)
        form.addRow(tr("窗口输入模式:"), self._input_combo)

        self._title_edit = QLineEdit(self._config.desktop_window_title)
        self._title_edit.setPlaceholderText(tr("空串不自动定位窗口"))
        form.addRow(tr("默认窗口标题:"), self._title_edit)

        return tab

    # ─── Tab2 输入模拟（引擎级点击参数）───────────────────

    def _build_input_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        sim = self._config.input_sim

        for name, label, tip in _RANGE_FIELDS:
            lo, hi = getattr(sim, name)
            form.addRow(f"{label}:", self._build_range_row(name, lo, hi, tip))

        self._offset_spin = QSpinBox()
        self._offset_spin.setRange(0, 50)
        self._offset_spin.setValue(sim.click_random_offset)
        self._offset_spin.setFixedWidth(_SPIN_WIDTH)
        form.addRow(tr("坐标随机偏移(px):"), self._spin_with_tip(
            self._offset_spin, tr("点击坐标附加 ±N 像素的随机偏移")))

        self._jitter_spin = QDoubleSpinBox()
        self._jitter_spin.setRange(0.0, 0.49)
        self._jitter_spin.setSingleStep(0.01)
        self._jitter_spin.setDecimals(2)
        self._jitter_spin.setValue(sim.region_jitter_ratio)
        self._jitter_spin.setFixedWidth(_SPIN_WIDTH)
        form.addRow(tr("区域中心偏移比例:"), self._spin_with_tip(
            self._jitter_spin,
            tr("区域内点击点相对中心的随机偏移比例（必须小于 0.5，防止偏出区域）")))

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
        self._custom_grid.addWidget(QLabel(tr("参数 key")), 0, 0)
        self._custom_grid.addWidget(QLabel(tr("名称")), 0, 1)
        self._custom_grid.addWidget(QLabel(tr("等待范围(秒)")), 0, 2, 1, 3)
        vbox.addLayout(self._custom_grid)

        for key, item in self._config.delay_params.items():
            self._add_custom_row(key, item.label, *item.range, saved=True)  # type: ignore[misc]

        add_row = QHBoxLayout()
        add_btn = QPushButton(tr("添加参数"))
        add_btn.clicked.connect(self._on_add_custom_row)
        apply_button_style(add_btn)
        add_row.addWidget(add_btn)
        add_row.addStretch()
        vbox.addLayout(add_row)

        vbox.addStretch()
        return tab

    # ─── Tab4 系统参数（可用目标环境）────────────────────

    def _build_env_tab(self) -> QWidget:
        """系统参数 Tab：可用目标环境列表"""
        tab = QWidget()
        vbox = QVBoxLayout(tab)

        caption = QLabel(tr("目标环境决定 DSL 中 env() 的返回值，用于区分 PC 游戏与手游的导航策略。指游戏运行的目标环境，非本机系统环境。"))
        caption.setWordWrap(True)
        vbox.addWidget(caption)

        # 表头
        self._env_grid = QGridLayout()
        self._env_grid.setColumnStretch(0, 2)
        self._env_grid.setColumnStretch(1, 3)
        self._env_grid.addWidget(QLabel(tr("环境 key")), 0, 0)
        self._env_grid.addWidget(QLabel(tr("显示名称")), 0, 1)
        vbox.addLayout(self._env_grid)

        # 加载当前环境列表
        self._env_rows: list[dict] = []
        for key, name in load_available_envs():
            self._add_env_row(key, name, saved=True)

        add_row = QHBoxLayout()
        add_btn = QPushButton(tr("添加环境"))
        add_btn.clicked.connect(self._on_add_env_row)
        apply_button_style(add_btn)
        add_row.addWidget(add_btn)
        add_row.addStretch()
        vbox.addLayout(add_row)

        vbox.addStretch()
        return tab

    def _on_add_env_row(self):
        """用户点击添加环境"""
        entry = self._add_env_row()
        self._connect_env_row_dirty(entry)
        self._mark_dirty()

    def _add_env_row(self, key: str = "", name: str = "", saved: bool = False) -> dict:
        """向共享网格追加一行环境配置"""
        key_edit = QLineEdit(key)
        key_edit.setPlaceholderText(tr("如 ios"))
        key_edit.setMinimumWidth(100)

        name_edit = QLineEdit(name)
        name_edit.setPlaceholderText(tr("显示名称"))
        name_edit.setMinimumWidth(100)

        entry = {"key": key_edit, "name": name_edit, "saved": saved}
        del_btn = QPushButton(tr("删除"))
        del_btn.clicked.connect(lambda: self._remove_env_row(entry))
        apply_button_style(del_btn, variant="danger")

        widgets = [key_edit, name_edit, del_btn]
        entry["widgets"] = widgets
        grid_row = self._env_grid.rowCount()
        for col, widget in enumerate(widgets):
            self._env_grid.addWidget(widget, grid_row, col)

        self._env_rows.append(entry)
        return entry

    def _connect_env_row_dirty(self, entry: dict):
        """连接环境行的变更信号"""
        entry["key"].textChanged.connect(self._mark_dirty)
        entry["name"].textChanged.connect(self._mark_dirty)

    def _remove_env_row(self, entry: dict):
        """删除一行环境配置"""
        self._env_rows.remove(entry)
        for widget in entry["widgets"]:
            self._env_grid.removeWidget(widget)
            widget.deleteLater()
        self._mark_dirty()

    # ─── Tab5 热键设置（F7~F12 按键位）─────────────

    def _build_hotkey_tab(self) -> QWidget:
        """热键设置 Tab：自定义全局热键按键位（限 F7~F12）。"""
        tab = QWidget()
        vbox = QVBoxLayout(tab)

        warn = QLabel(tr("以下为系统级全局热键，保存后立即生效。"))
        warn.setStyleSheet("color: #2E7D32; font-weight: bold;")
        warn.setWordWrap(True)
        vbox.addWidget(warn)

        form = QFormLayout()
        hk = self._config.hotkeys
        self._hotkey_combos: dict[str, QComboBox] = {}
        for name, label in _HOTKEY_FIELDS:
            combo = QComboBox()
            combo.addItems(_HOTKEY_CHOICES)
            combo.setFixedWidth(_HOTKEY_COMBO_WIDTH)
            idx = combo.findText(getattr(hk, name))
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            self._hotkey_combos[name] = combo
            if name == "record":
                row = QHBoxLayout()
                row.addWidget(combo)
                row.addWidget(self._tip_button(tr(
                    "该热键只在「脚本录制」对话框打开期间临时全局注册，"
                    "对话框关闭后立即注销；其余三个热键在软件运行期间始终生效。")))
                row.addStretch()
                form.addRow(f"{label}:", row)
            else:
                form.addRow(f"{label}:", combo)
        vbox.addLayout(form)

        vbox.addStretch()
        return tab

    def _collect_hotkeys(self) -> dict | None:
        """收集热键设置，校验四个动作不能绑定同一个按键"""
        values = {name: combo.currentText() for name, combo in self._hotkey_combos.items()}
        if len(set(values.values())) != len(values):
            QMessageBox.warning(self, tr("配置管理"), tr("热键不能重复绑定同一个按键，请检查后重试"))
            return None
        return values

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
    def _make_range_spins(lo: float, hi: float) -> tuple[QDoubleSpinBox, QDoubleSpinBox]:
        """创建 min~max 一对输入框：max 下限跟随 min，输入期即阻止 max < min"""
        spins = []
        for value in (lo, hi):
            spin = QDoubleSpinBox()
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

    def _on_add_custom_row(self):
        """用户点击添加：新行未保存，删除无需确认，并视为变更"""
        entry = self._add_custom_row()
        self._connect_row_dirty(entry)
        self._mark_dirty()

    def _add_custom_row(self, key: str = "", label: str = "",
                        lo: float = 1.0, hi: float = 1.0,
                        saved: bool = False) -> dict:
        """向共享网格追加一行等待参数：key + 名称 + min~max + 删除按钮

        saved: 是否来自已保存的配置（删除时需二次确认）
        """
        key_edit = QLineEdit(key)
        key_edit.setPlaceholderText(tr("如 my_wait"))
        key_edit.setMinimumWidth(100)

        label_edit = QLineEdit(label)
        label_edit.setPlaceholderText(tr("显示名称"))
        label_edit.setMinimumWidth(100)

        lo_spin, hi_spin = self._make_range_spins(lo, hi)

        entry = {"key": key_edit, "label": label_edit, "lo": lo_spin, "hi": hi_spin,
                 "saved": saved}
        del_btn = QPushButton(tr("删除"))
        del_btn.clicked.connect(lambda: self._remove_custom_row(entry))
        apply_button_style(del_btn, variant="danger")

        widgets = [key_edit, label_edit, lo_spin, QLabel("~"), hi_spin, del_btn]
        entry["widgets"] = widgets
        grid_row = self._custom_grid.rowCount()
        for col, widget in enumerate(widgets):
            self._custom_grid.addWidget(widget, grid_row, col)

        self._custom_rows.append(entry)
        return entry

    def _remove_custom_row(self, entry: dict):
        """删除一行等待参数；已保存的行需二次确认"""
        if entry["saved"]:
            key = entry["key"].text().strip() or tr("(未命名)")
            reply = QMessageBox.question(
                self, tr("确认删除"),
                f"确定删除等待参数「{key}」吗？\n"
                f"引用该参数的工作流将在加载时报错。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._custom_rows.remove(entry)
        for widget in entry["widgets"]:
            self._custom_grid.removeWidget(widget)
            widget.deleteLater()
        self._mark_dirty()

    # ─── Tab6 网络与隐私 ────────────────────────────────────
    #
    # 与其余 Tab 不同：这里的每个开关点击后**立即生效**，不经"保存"按钮。
    # 公告/更新是给用户的服务，统计是用户给项目的贡献，分开存放；关闭
    # 统计必须同步清空本地缓冲，这类有副作用的操作不适合被"保存"按钮
    # 延后到不确定的时间点（用户可能改完就直接关对话框）。

    def _build_privacy_tab(self) -> QWidget:
        tab = QWidget()
        vbox = QVBoxLayout(tab)

        network = load_user_config().network

        self._offline_check = QCheckBox(tr("完全离线模式（关闭全部联网行为）"))
        self._offline_check.setChecked(network.offline)
        self._offline_check.toggled.connect(self._on_offline_toggled)
        vbox.addWidget(self._offline_check)

        sub_box = QVBoxLayout()
        sub_box.setContentsMargins(24, 0, 0, 0)

        self._announcement_check = QCheckBox(tr("启动时检查公告"))
        self._announcement_check.setChecked(network.announcement)
        self._announcement_check.toggled.connect(
            lambda v: self._on_net_feature_toggled("announcement", v))
        sub_box.addWidget(self._announcement_check)

        self._update_check = QCheckBox(tr("启动时检查新版本"))
        self._update_check.setChecked(network.update)
        self._update_check.toggled.connect(
            lambda v: self._on_net_feature_toggled("update", v))
        sub_box.addWidget(self._update_check)

        self._telemetry_check = QCheckBox(
            tr("参与匿名数据收集，帮助改进调律策略"))
        self._telemetry_check.setChecked(network.telemetry)
        self._telemetry_check.toggled.connect(self._on_telemetry_toggled)
        sub_box.addWidget(self._telemetry_check)

        caption = QLabel(
            tr("仅用于改进内置调律规则，不公开发布原始数据；不含账号、"
               "角色名、装备名称或完整词条组合。"))
        caption.setWordWrap(True)
        caption.setStyleSheet("color: palette(mid);")
        sub_box.addWidget(caption)

        id_row = QHBoxLayout()
        self._telemetry_id_label = QLabel()
        id_row.addWidget(self._telemetry_id_label)
        id_row.addStretch()
        btn_reset = QPushButton(tr("重置标识"))
        btn_reset.clicked.connect(self._on_reset_telemetry_id)
        id_row.addWidget(btn_reset)
        btn_view = QPushButton(tr("查看待上报数据"))
        btn_view.clicked.connect(self._on_view_pending_telemetry)
        id_row.addWidget(btn_view)
        btn_more = QPushButton(tr("了解详情"))
        btn_more.clicked.connect(self._on_learn_more_telemetry)
        id_row.addWidget(btn_more)
        apply_button_style(btn_reset, btn_view, btn_more, variant="neutral")
        sub_box.addLayout(id_row)

        vbox.addLayout(sub_box)
        vbox.addStretch()

        self._sub_network_widgets = [
            self._announcement_check, self._update_check,
            self._telemetry_check, btn_reset, btn_view]
        self._refresh_privacy_tab_state()
        return tab

    def _on_offline_toggled(self, checked: bool):
        from ..core.telemetry.settings import set_network_feature
        set_network_feature("offline", checked)
        self._refresh_privacy_tab_state()

    def _on_net_feature_toggled(self, feature: str, checked: bool):
        from ..core.telemetry.settings import set_network_feature
        set_network_feature(feature, checked)

    def _on_telemetry_toggled(self, checked: bool):
        from ..core.telemetry.settings import set_telemetry_enabled
        set_telemetry_enabled(checked)
        self._refresh_privacy_tab_state()

    def _on_reset_telemetry_id(self):
        reply = QMessageBox.question(
            self, tr("重置匿名标识"),
            tr("重置后服务端会把你当成一个新用户，之前的记录无法再与"
               "新标识关联。确定重置吗？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        from ..core.telemetry.identity import reset_identity
        reset_identity()
        self._refresh_privacy_tab_state()

    def _on_view_pending_telemetry(self):
        from ..core.telemetry.paths import spool_dir
        d = spool_dir()
        d.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    @staticmethod
    def _on_learn_more_telemetry():
        QDesktopServices.openUrl(QUrl(
            "https://github.com/wanda1416/lvjiang/blob/master/PRIVACY.md"))

    def _refresh_privacy_tab_state(self):
        """离线模式勾上时，其余三项置灰但保留各自的值不清空。"""
        from ..core.telemetry.identity import get_identity

        offline = self._offline_check.isChecked()
        for widget in self._sub_network_widgets:
            widget.setEnabled(not offline)

        network = load_user_config().network
        if network.telemetry:
            identity = get_identity()
            self._telemetry_id_label.setText(
                tr("当前标识：") + identity.install_id[:8] + "…")
        else:
            self._telemetry_id_label.setText(tr("当前标识：未生成（未开启）"))

    # ─── 保存 ──────────────────────────────────────────────

    def _collect_custom(self) -> dict | None:
        """收集等待参数，校验失败弹窗提示并返回 None"""
        custom: dict[str, dict] = {}
        for entry in self._custom_rows:
            key = entry["key"].text().strip()
            if not key:
                continue  # 空行忽略
            if not key.isidentifier():
                QMessageBox.warning(self, tr("配置管理"),
                                    f"参数 key「{key}」无效：只能由字母/数字/下划线组成且不以数字开头")
                return None
            if key in _RESERVED_KEYS:
                QMessageBox.warning(self, tr("配置管理"), f"参数 key「{key}」与引擎参数冲突")
                return None
            if key in custom:
                QMessageBox.warning(self, tr("配置管理"), f"参数 key「{key}」重复")
                return None
            lo, hi = entry["lo"].value(), entry["hi"].value()
            if hi < lo:
                hi = lo
            custom[key] = {
                "label": entry["label"].text().strip(),
                "range": [round(lo, 2), round(hi, 2)],
            }
        return custom

    def _collect_envs(self) -> list[dict]:
        """收集环境配置，返回 [{key, name}, ...]"""
        envs = []
        seen_keys = set()
        for entry in self._env_rows:
            key = entry["key"].text().strip()
            if not key:
                continue
            if key in seen_keys:
                continue
            seen_keys.add(key)
            envs.append({
                "key": key,
                "name": entry["name"].text().strip() or key,
            })
        return envs

    def _on_save(self):
        delay_params = self._collect_custom()
        if delay_params is None:
            return
        hotkeys = self._collect_hotkeys()
        if hotkeys is None:
            return
        settings = {
            "android_capture_method": (
                "scrcpy" if self._capture_stream_radio.isChecked() else "screencap"),
            "android_input_method": (
                "device_gesture" if self._android_input_agent_radio.isChecked() else "adb"),
            "desktop_background_input": self._input_combo.currentData(),
            "desktop_window_title": self._title_edit.text().strip(),
            "hotkeys": hotkeys,
        }
        # 保存语言设置（需重启生效）
        lang = self._lang_combo.currentData()
        if lang:
            settings["language"] = lang
        save_settings(settings)
        input_sim: dict = {}
        for name, *_ in _RANGE_FIELDS:
            lo_spin, hi_spin = self._range_spins[name]
            lo, hi = lo_spin.value(), hi_spin.value()
            if hi < lo:
                hi = lo
            input_sim[name] = [round(lo, 2), round(hi, 2)]
        input_sim["click_random_offset"] = self._offset_spin.value()
        input_sim["region_jitter_ratio"] = round(self._jitter_spin.value(), 2)
        envs = self._collect_envs()
        save_app_config(input_sim, delay_params, envs)
        self.hotkeys_saved.emit(hotkeys)
        # 保存后不关闭：置灰保存按钮，当前各行均视为已保存，可继续修改
        for entry in self._custom_rows:
            entry["saved"] = bool(entry["key"].text().strip())
        for entry in self._env_rows:
            entry["saved"] = bool(entry["key"].text().strip())
        self._save_btn.setEnabled(False)
