"""配置管理对话框（多 Tab）

Tab1 基础配置、Tab2 输入模拟（引擎级点击参数）、Tab3 等待参数（命名等待）、
Tab4 方案设置（连接方案 + 可用工作环境）、Tab5 字体设置、Tab6 热键设置（F7~F12 按键位）。
Tab1/Tab5/Tab6 写 session.json（settings 节点）；Tab2/Tab3/Tab4 的环境列表写 app.yaml
（input_simulation / delay_params / envs，system ← local 合并），保存后以配置文件为准
覆盖代码默认值。Tab4 的方案写 session.json 的 plans 节点——方案是机器级运行态，与用户无关。
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
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
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
from .button_styles import apply_button_style, apply_compact_tool_button_style

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
    ("start", tr("开始执行")),
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
    font_sizes_saved = pyqtSignal(dict)
    plans_saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("配置管理"))
        self.setMinimumSize(720, 480)
        self.resize(760, 520)
        self._config = load_user_config()
        # 构建方案页时可能会把旧版空值/失效值归一化为表单实际显示值，
        # 此时底部保存按钮尚未创建；先独立记录脏状态，等按钮创建后同步。
        self._dirty = False
        self._range_spins: dict[str, tuple[QDoubleSpinBox, QDoubleSpinBox]] = {}
        self._custom_rows: list[dict] = []
        self._setup_ui()
        self._connect_dirty_signals()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_basic_tab(), tr("基础配置"))
        self._tabs.addTab(self._build_input_tab(), tr("输入模拟"))
        self._tabs.addTab(self._build_wait_tab(), tr("等待参数"))
        self._tabs.addTab(self._build_plan_tab(), tr("方案设置"))
        self._tabs.addTab(self._build_font_tab(), tr("字体设置"))
        self._tabs.addTab(self._build_hotkey_tab(), tr("热键设置"))
        self._privacy_tab_index = self._tabs.addTab(
            self._build_privacy_tab(), tr("网络与隐私"))
        layout.addWidget(self._tabs)

        # ── 底部按钮：保存（左）与关闭（右）隔开，语义不同 ──
        # 保存默认置灰，参数发生变更后启用；保存后不关闭对话框，可继续修改
        btn_row = QHBoxLayout()
        self._save_btn = QPushButton(tr("保存"))
        self._save_btn.setEnabled(self._dirty)
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)
        btn_row.addStretch()
        self._close_btn = QPushButton(tr("关闭"))
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)
        apply_button_style(self._save_btn)
        apply_button_style(self._close_btn, variant="neutral")
        layout.addLayout(btn_row)
        self._tabs.currentChanged.connect(self._refresh_save_button_visibility)
        self._refresh_save_button_visibility(self._tabs.currentIndex())

    # ─── 脏状态跟踪 ──────────────────────────────────

    def _mark_dirty(self, *_args):
        """任意参数变更后启用保存按钮"""
        self._dirty = True
        if hasattr(self, "_save_btn"):
            self._save_btn.setEnabled(True)

    def _refresh_save_button_visibility(self, index: int) -> None:
        """即时保存的网络与隐私页不展示无意义的全局保存按钮。"""
        self._save_btn.setVisible(index != self._privacy_tab_index)

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
        self._overview_font_spin.valueChanged.connect(self._mark_dirty)
        self._user_info_font_spin.valueChanged.connect(self._mark_dirty)

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

    def _build_plan_tab(self) -> QWidget:
        """方案设置 Tab：上半「连接方案」，下半原有的「系统参数」。"""
        tab = QWidget()
        vbox = QVBoxLayout(tab)

        plans_box = QGroupBox(tr("连接方案"))
        plans_box.setLayout(self._build_plan_manager_layout())
        vbox.addWidget(plans_box, 1)

        env_box = QGroupBox(tr("系统参数"))
        env_layout = QVBoxLayout(env_box)
        env_layout.addWidget(self._build_env_tab())
        vbox.addWidget(env_box)
        return tab

    # ─── 连接方案 ──────────────────────────────────

    def _build_plan_manager_layout(self) -> QHBoxLayout:
        """左侧方案列表 + 右侧方案详情。"""
        from ..core.config.plans import load_plans

        self._plans = load_plans()
        row = QHBoxLayout()

        left = QVBoxLayout()
        caption = QLabel(
            tr("方案把图库、环境、布局绑成一个整体，并声明支持的连接模式。"))
        caption.setWordWrap(True)
        left.addWidget(caption)
        self._plan_list = QListWidget()
        self._plan_list.currentRowChanged.connect(self._on_plan_row_changed)
        left.addWidget(self._plan_list, 1)
        buttons = QHBoxLayout()
        new_btn = QPushButton(tr("新建"))
        new_btn.clicked.connect(self._on_new_plan)
        from_current_btn = QPushButton(tr("从当前组合新建"))
        from_current_btn.clicked.connect(self._on_new_plan_from_current)
        self._plan_delete_btn = QPushButton(tr("删除"))
        self._plan_delete_btn.clicked.connect(self._on_delete_plan)
        apply_button_style(new_btn, from_current_btn)
        apply_button_style(self._plan_delete_btn, variant="danger")
        buttons.addWidget(new_btn)
        buttons.addWidget(from_current_btn)
        buttons.addWidget(self._plan_delete_btn)
        buttons.addStretch()
        left.addLayout(buttons)
        row.addLayout(left, 3)

        form = QFormLayout()
        self._plan_name_edit = QLineEdit()
        self._plan_name_edit.textChanged.connect(self._on_plan_name_edited)
        form.addRow(tr("名称") + ":", self._plan_name_edit)
        self._plan_space_combo = QComboBox()
        self._plan_space_combo.addItems(self._available_spaces())
        form.addRow(tr("图库") + ":", self._plan_space_combo)
        self._plan_env_combo = QComboBox()
        for key, display in load_available_envs():
            self._plan_env_combo.addItem(display, key)
        form.addRow(tr("环境") + ":", self._plan_env_combo)
        self._plan_layout_combo = QComboBox()
        self._plan_layout_combo.addItems(self._available_layouts())
        form.addRow(tr("布局") + ":", self._plan_layout_combo)
        modes = QHBoxLayout()
        self._plan_mode_window = QCheckBox(tr("窗口模式"))
        self._plan_mode_adb = QCheckBox(tr("ADB 模式"))
        modes.addWidget(self._plan_mode_window)
        modes.addWidget(self._plan_mode_adb)
        modes.addStretch()
        form.addRow(tr("模式") + ":", modes)
        # 分发：勾上则方案写进 app.yaml 随包发布，否则留在本机 session.json。
        # 只有开发模式看得到——普通用户既不该改发行配置，也不需要理解这层。
        self._plan_distribute = QCheckBox(
            tr("写入 app.yaml，随安装包分发"))
        self._plan_distribute_label = QLabel(tr("分发") + ":")
        form.addRow(self._plan_distribute_label, self._plan_distribute)
        if not self._plan_dev_mode():
            self._plan_distribute_label.hide()
            self._plan_distribute.hide()
        for widget in (self._plan_space_combo, self._plan_env_combo,
                       self._plan_layout_combo):
            widget.currentIndexChanged.connect(self._on_plan_field_edited)
        for box in (self._plan_mode_window, self._plan_mode_adb,
                    self._plan_distribute):
            box.toggled.connect(self._on_plan_field_edited)
        row.addLayout(form, 4)

        self._refresh_plan_list()
        return row

    def _available_spaces(self) -> list[str]:
        host_db = getattr(self.parent(), "_reference_db", None)
        if host_db is not None:
            return list(host_db.get_spaces())
        from ..core.reference_db import ReferenceDatabase
        return list(ReferenceDatabase().get_spaces())

    def _available_layouts(self) -> list[str]:
        host_manager = getattr(self.parent(), "_layout_manager", None)
        if host_manager is None:
            from ..core.layout_manager import LayoutConfigManager
            host_manager = LayoutConfigManager()
        return list(host_manager.list_layouts())

    @staticmethod
    def _plan_dev_mode() -> bool:
        from ..core.config.resolver import get_resolver
        return get_resolver().is_dev_mode()

    def _plan_is_editable(self, plan) -> bool:
        """分发方案随安装包下发，普通用户只能看不能改。"""
        if plan is None:
            return False
        return self._plan_dev_mode() or not plan.distributed

    def _current_plan(self):
        row = self._plan_list.currentRow()
        if 0 <= row < len(self._plans):
            return self._plans[row]
        return None

    def _refresh_plan_list(self, select: int = -1) -> None:
        self._plan_list.blockSignals(True)
        self._plan_list.clear()
        for plan in self._plans:
            self._plan_list.addItem(plan.name)
        row = select if 0 <= select < len(self._plans) else (
            0 if self._plans else -1)
        self._plan_list.setCurrentRow(row)
        self._plan_list.blockSignals(False)
        self._load_plan_into_form(self._current_plan())

    def _load_plan_into_form(self, plan) -> None:
        from ..core.config.plans import PLAN_MODE_ADB, PLAN_MODE_WINDOW

        widgets = (self._plan_name_edit, self._plan_space_combo,
                   self._plan_env_combo, self._plan_layout_combo,
                   self._plan_mode_window, self._plan_mode_adb,
                   self._plan_distribute)
        for widget in widgets:
            widget.blockSignals(True)
        editable = self._plan_is_editable(plan)
        for widget in widgets:
            widget.setEnabled(editable)
        self._plan_delete_btn.setEnabled(editable)
        self._plan_name_edit.setText(plan.name if plan else "")
        space_idx = self._plan_space_combo.findText(plan.space) if plan else -1
        self._plan_space_combo.setCurrentIndex(max(space_idx, 0))
        env_idx = self._plan_env_combo.findData(plan.env) if plan else -1
        self._plan_env_combo.setCurrentIndex(max(env_idx, 0))
        layout_idx = (self._plan_layout_combo.findText(plan.layout)
                      if plan else -1)
        self._plan_layout_combo.setCurrentIndex(max(layout_idx, 0))
        self._plan_mode_window.setChecked(
            bool(plan) and PLAN_MODE_WINDOW in plan.modes)
        self._plan_mode_adb.setChecked(
            bool(plan) and PLAN_MODE_ADB in plan.modes)
        self._plan_distribute.setChecked(bool(plan) and plan.distributed)
        for widget in widgets:
            widget.blockSignals(False)
        # 上面是在 blockSignals 里填的，编辑回调不会触发；而 max(idx, 0) 会把
        # 空值或已失效的值静默显示成第一项。不回写的话表单显示的和方案里存的
        # 就对不上——新建的方案三项全是空串，选中后主界面三个框纹丝不动。
        # 只读方案例外：它引用的图库/布局在这台机器上可能根本不存在，回退到
        # 第一项再回写，等于让用户「看一眼」就篡改了随包分发的配置。
        if editable and self._write_form_into_plan(plan):
            self._mark_dirty()

    def _on_plan_row_changed(self, _row: int) -> None:
        self._load_plan_into_form(self._current_plan())

    def _on_plan_name_edited(self, text: str) -> None:
        plan = self._current_plan()
        if not self._plan_is_editable(plan):
            return
        plan.name = text
        item = self._plan_list.currentItem()
        if item is not None:
            item.setText(text)
        self._mark_dirty()

    def _write_form_into_plan(self, plan) -> bool:
        """把表单当前显示的值写进方案，返回是否真的有变化。

        所见即所存：表单上显示什么，方案里就必须存什么。
        """
        from ..core.config.plans import PLAN_MODE_ADB, PLAN_MODE_WINDOW

        modes = []
        if self._plan_mode_window.isChecked():
            modes.append(PLAN_MODE_WINDOW)
        if self._plan_mode_adb.isChecked():
            modes.append(PLAN_MODE_ADB)
        updated = (
            self._plan_space_combo.currentText(),
            self._plan_env_combo.currentData() or "",
            self._plan_layout_combo.currentText(),
            modes,
            self._plan_distribute.isChecked(),
        )
        current = (plan.space, plan.env, plan.layout, plan.modes,
                   plan.distributed)
        if current == updated:
            return False
        (plan.space, plan.env, plan.layout, plan.modes,
         plan.distributed) = updated
        return True

    def _on_plan_field_edited(self, *_args) -> None:
        plan = self._current_plan()
        if not self._plan_is_editable(plan):
            return
        self._write_form_into_plan(plan)
        self._mark_dirty()

    def _append_plan(self, plan) -> None:
        self._plans.append(plan)
        self._refresh_plan_list(select=len(self._plans) - 1)
        self._plan_name_edit.setFocus()
        self._plan_name_edit.selectAll()
        self._mark_dirty()

    def _on_new_plan(self) -> None:
        from ..core.config.plans import PLAN_MODES, Plan
        self._append_plan(Plan.create(tr("新方案"), modes=list(PLAN_MODES)))

    def _on_new_plan_from_current(self) -> None:
        """拿主界面手上那套组合直接建方案——迁移成本最低的一条路。"""
        from ..core.config.plans import PLAN_MODE_ADB, PLAN_MODE_WINDOW, Plan

        host = self.parent()
        space_combo = getattr(host, "reference_space_combo", None)
        env_combo = getattr(host, "_env_combo", None)
        layout_combo = getattr(host, "layout_combo", None)
        backend = getattr(host, "_backend", None)
        modes = [backend] if backend in (PLAN_MODE_WINDOW, PLAN_MODE_ADB) \
            else [PLAN_MODE_WINDOW, PLAN_MODE_ADB]
        self._append_plan(Plan.create(
            tr("新方案"),
            space=space_combo.currentText() if space_combo else "",
            env=(env_combo.currentData() or "") if env_combo else "",
            layout=layout_combo.currentText() if layout_combo else "",
            modes=modes,
        ))

    def _on_delete_plan(self) -> None:
        plan = self._current_plan()
        if not self._plan_is_editable(plan):
            return
        confirmed = QMessageBox.question(
            self, tr("删除方案"),
            tr("确定删除方案「{name}」吗？").format(name=plan.name),
        ) == QMessageBox.StandardButton.Yes
        if not confirmed:
            return
        row = self._plan_list.currentRow()
        self._plans.remove(plan)
        self._refresh_plan_list(select=min(row, len(self._plans) - 1))
        self._mark_dirty()

    def _collect_plans(self) -> list:
        return list(self._plans)

    def _validate_plans(self) -> str:
        """返回错误说明；空串表示通过。"""
        for plan in self._plans:
            if not plan.name.strip():
                return tr("方案名称不能为空")
            if not plan.modes:
                return tr("方案「{name}」至少要勾选一种连接模式").format(
                    name=plan.name)
        return ""

    def _build_env_tab(self) -> QWidget:
        """系统参数：可用目标环境列表"""
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

    # ─── Tab5 字体设置 ─────────────────────────────

    @staticmethod
    def _default_font_point_size(widget: QWidget) -> int:
        size = widget.font().pointSize()
        return size if 8 <= size <= 24 else 10

    def _build_font_tab(self) -> QWidget:
        """用户总览/用户信息内容区字号，不影响顶部工具栏。"""
        tab = QWidget()
        vbox = QVBoxLayout(tab)

        caption = QLabel(tr(
            "只调整「用户总览」和「用户信息」中刷新工具栏下方的内容字号，"
            "包括列表表头；顶部工具栏和底部「数据模型」按钮保持不变。"
        ))
        caption.setWordWrap(True)
        vbox.addWidget(caption)

        form = QFormLayout()
        default_size = self._default_font_point_size(tab)
        fonts = self._config.font_sizes

        self._overview_font_spin = QSpinBox()
        self._overview_font_spin.setRange(8, 24)
        self._overview_font_spin.setSuffix(" pt")
        self._overview_font_spin.setValue(fonts.user_overview or default_size)
        self._overview_font_spin.setFixedWidth(_SPIN_WIDTH)
        form.addRow(tr("用户总览字号") + ":", self._overview_font_spin)

        self._user_info_font_spin = QSpinBox()
        self._user_info_font_spin.setRange(8, 24)
        self._user_info_font_spin.setSuffix(" pt")
        self._user_info_font_spin.setValue(fonts.user_info or default_size)
        self._user_info_font_spin.setFixedWidth(_SPIN_WIDTH)
        form.addRow(tr("用户信息字号") + ":", self._user_info_font_spin)

        vbox.addLayout(form)
        vbox.addStretch()
        return tab

    # ─── Tab6 热键设置（F7~F12 按键位）─────────────

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
        apply_compact_tool_button_style(btn)
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

        self._offline_check = QCheckBox(tr("完全离线模式（暂停全部联网功能）"))
        self._offline_check.setChecked(network.offline)
        self._offline_check.toggled.connect(self._on_offline_toggled)
        vbox.addWidget(self._offline_check)

        self._offline_hint = QLabel()
        self._offline_hint.setWordWrap(True)
        self._offline_hint.setContentsMargins(24, 0, 0, 4)
        vbox.addWidget(self._offline_hint)

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

        self._remote_config_check = QCheckBox(tr("接收在线配置更新"))
        self._remote_config_check.setChecked(network.remote_config)
        self._remote_config_check.toggled.connect(
            lambda v: self._on_net_feature_toggled("remote_config", v))
        sub_box.addWidget(self._remote_config_check)

        remote_caption = QLabel(
            tr("只下发识别所需的配置（场景与布局坐标、插件规则），用于在不发"
               "新版的情况下修复识别问题；你自己改过的配置始终优先，不会被"
               "覆盖。拉取到的配置下次启动生效。"))
        remote_caption.setWordWrap(True)
        remote_caption.setStyleSheet("color: palette(mid);")
        sub_box.addWidget(remote_caption)

        self._remote_config_status = QLabel()
        self._remote_config_status.setStyleSheet("color: palette(mid);")
        sub_box.addWidget(self._remote_config_status)

        self._telemetry_check = QCheckBox(tr("参与匿名数据收集，帮助改进内置规则"))
        self._telemetry_check.setChecked(network.telemetry)
        self._telemetry_check.toggled.connect(self._on_telemetry_toggled)
        sub_box.addWidget(self._telemetry_check)

        caption = QLabel(self._telemetry_caption())
        caption.setWordWrap(True)
        caption.setStyleSheet("color: palette(mid);")
        sub_box.addWidget(caption)

        id_row = QHBoxLayout()
        self._telemetry_id_label = QLabel()
        self._telemetry_id_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        id_row.addWidget(self._telemetry_id_label)
        id_row.addStretch()
        self._telemetry_reset_button = QPushButton(tr("重置标识"))
        self._telemetry_reset_button.clicked.connect(
            self._on_reset_telemetry_id)
        id_row.addWidget(self._telemetry_reset_button)
        btn_view = QPushButton(tr("查看待上报数据"))
        btn_view.clicked.connect(self._on_view_pending_telemetry)
        id_row.addWidget(btn_view)
        btn_more = QPushButton(tr("了解详情"))
        btn_more.clicked.connect(self._on_learn_more_telemetry)
        id_row.addWidget(btn_more)
        apply_button_style(
            self._telemetry_reset_button, btn_view, btn_more,
            variant="neutral")
        sub_box.addLayout(id_row)

        vbox.addLayout(sub_box)
        vbox.addStretch()

        # 离线模式只覆盖三个联网偏好的“当前生效状态”，不改写其保存值。
        # 标识重置和待上报数据查看都是纯本地操作，离线时仍应可用。
        self._network_feature_checks = [
            self._announcement_check, self._update_check,
            self._remote_config_check, self._telemetry_check]
        self._refresh_remote_config_status()
        self._refresh_privacy_tab_state()
        return tab

    @staticmethod
    def _telemetry_caption() -> str:
        """收集说明由插件经 ``AppHooks.telemetry_disclosures`` 提供。

        通用设置页不该替插件描述它收集什么——原先写死的"改进内置调律规则、
        不含装备名称或完整词条组合"是燕云的说法，换个插件就是错的。同意框
        （ui/notices/telemetry_consent_dialog.py）读的是同一份声明，两处
        描述天然一致，不会各说各的。
        """
        from ..apps import get_registry

        parts = [
            f"{item.purpose}；{tr('不收集')}：{'、'.join(item.excluded)}"
            if item.excluded else item.purpose
            for item in get_registry().get("telemetry_disclosures", ())
        ]
        history_notices = [
            tr("开启后会补传最近 {days} 天内尚未上报的相关历史数据；关闭期间的记录仍保存在本地。").format(
                days=vars(item).get("historical_upload_days"))
            for item in get_registry().get("telemetry_disclosures", ())
            if vars(item).get("historical_upload_days")
        ]
        if not parts:
            return tr("仅上报匿名的运行环境信息，不含任何游戏内数据。")
        return (tr("不公开发布原始数据。") + " " + " ".join(parts)
                + ((" " + " ".join(history_notices)) if history_notices else ""))

    def _refresh_remote_config_status(self):
        """展示在线配置版本——用户得能知道自己在跑哪一版。

        已下载但尚未生效的要说清楚：配置落在暂存层、下次启动才提升
        （见 core/config/remote.py），此时直接报"当前版本 vN"是错的——
        本次会话跑的还是上一版。
        """
        from ..core.config.remote import (
            get_config_version,
            get_last_synced_at,
            stage_dir,
        )
        version = get_config_version()
        if not version:
            self._remote_config_status.setText(tr("尚未获取过在线配置"))
            return
        if stage_dir().is_dir():
            self._remote_config_status.setText(
                tr("已下载在线配置 v{version}，重启后生效").format(version=version))
            return
        synced = (get_last_synced_at() or "")[:10]
        self._remote_config_status.setText(
            tr("当前在线配置版本 v{version}（{date} 同步）").format(
                version=version, date=synced or tr("未知日期")))

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
        if checked:
            starter = getattr(self.parent(), "_start_telemetry_report_on_startup", None)
            if callable(starter):
                starter()

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
        identity = reset_identity()
        # 直接使用本次重置的返回值，避免页面继续展示重置前的标识。
        self._show_telemetry_identity(identity.install_id)

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
        """展示实际生效状态，同时保留离线前的联网功能偏好。"""
        from ..core.telemetry.identity import get_identity

        offline = self._offline_check.isChecked()
        network = load_user_config().network
        self._telemetry_reset_button.setEnabled(network.telemetry)
        # 顺序必须与 _network_feature_checks 一一对应（zip strict=True 会
        # 在漏加时直接报错，而不是静默错位）
        saved_values = (
            network.announcement,
            network.update,
            network.remote_config,
            network.telemetry,
        )
        for checkbox, saved in zip(
            self._network_feature_checks, saved_values, strict=True,
        ):
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(saved) and not offline)
            checkbox.setEnabled(not offline)
            checkbox.blockSignals(False)

        if offline:
            self._offline_hint.setText(tr(
                "已暂停公告检查、版本检查、在线配置更新和匿名数据上报；"
                "关闭离线模式后，将恢复之前的选择。"))
            self._offline_hint.setStyleSheet("color: #D97706;")
        else:
            self._offline_hint.setText(tr(
                "可在下方分别控制公告、版本更新、在线配置和匿名数据上报。"))
            self._offline_hint.setStyleSheet("color: palette(mid);")

        if network.telemetry:
            identity = get_identity()
            self._show_telemetry_identity(identity.install_id)
        else:
            self._telemetry_id_label.setText(tr("当前标识：未生成（未开启）"))

    def _show_telemetry_identity(self, install_id: str) -> None:
        """完整展示匿名标识，便于用户核对和复制。"""
        self._telemetry_id_label.setText(tr("当前标识：") + install_id)

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
        plan_error = self._validate_plans()
        if plan_error:
            QMessageBox.warning(self, tr("方案设置"), plan_error)
            return
        settings = {
            "android_capture_method": (
                "scrcpy" if self._capture_stream_radio.isChecked() else "screencap"),
            "android_input_method": (
                "device_gesture" if self._android_input_agent_radio.isChecked() else "adb"),
            "desktop_background_input": self._input_combo.currentData(),
            "desktop_window_title": self._title_edit.text().strip(),
            "hotkeys": hotkeys,
            "font_sizes": {
                "user_overview": self._overview_font_spin.value(),
                "user_info": self._user_info_font_spin.value(),
            },
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
        from ..core.config.plans import save_plans
        save_plans(self._collect_plans())
        self.plans_saved.emit()
        self.hotkeys_saved.emit(hotkeys)
        self.font_sizes_saved.emit(settings["font_sizes"])
        # 保存后不关闭：置灰保存按钮，当前各行均视为已保存，可继续修改
        for entry in self._custom_rows:
            entry["saved"] = bool(entry["key"].text().strip())
        for entry in self._env_rows:
            entry["saved"] = bool(entry["key"].text().strip())
        self._dirty = False
        self._save_btn.setEnabled(False)
