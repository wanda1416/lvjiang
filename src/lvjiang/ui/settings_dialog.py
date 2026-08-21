"""配置管理对话框（多 Tab）

Tab1 基础配置、Tab2 输入模拟（引擎级点击参数）、Tab3 等待参数（命名等待）、
Tab4 系统参数（可用工作环境）。
Tab1 写 session.json（settings 节点）；Tab2/Tab3/Tab4 写 app.yaml（input_simulation / delay_params / envs，
system ← local 合并），保存后以配置文件为准覆盖代码默认值。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
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
    QSpinBox,
    QTabWidget,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ..core.config import load_available_envs, load_user_config, save_app_config, save_settings
from ..i18n import tr

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

# 数值输入框统一定宽，避免被布局拉满整行
_SPIN_WIDTH = 90


class SettingsDialog(QDialog):
    """配置管理：Tab1 基础配置 + Tab2 输入模拟 + Tab3 等待参数"""

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
        layout.addWidget(tabs)

        # ── 底部按钮：保存（左）与关闭（右）隔开，语义不同 ──
        # 保存默认置灰，参数发生变更后启用；保存后不关闭对话框，可继续修改
        btn_row = QHBoxLayout()
        self._save_btn = QPushButton(tr("保存"))
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)
        btn_row.addStretch()
        close_btn = QPushButton(tr("关闭"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    # ─── 脏状态跟踪 ──────────────────────────────────

    def _mark_dirty(self, *_args):
        """任意参数变更后启用保存按钮"""
        self._save_btn.setEnabled(True)

    def _connect_dirty_signals(self):
        """UI 构建完成后统一连接变更信号（避免初始赋值触发）"""
        self._lang_combo.currentIndexChanged.connect(self._mark_dirty)
        self._capture_combo.currentIndexChanged.connect(self._mark_dirty)
        self._input_combo.currentIndexChanged.connect(self._mark_dirty)
        self._title_edit.textChanged.connect(self._mark_dirty)
        self._offset_spin.valueChanged.connect(self._mark_dirty)
        self._jitter_spin.valueChanged.connect(self._mark_dirty)
        for lo_spin, hi_spin in self._range_spins.values():
            lo_spin.valueChanged.connect(self._mark_dirty)
            hi_spin.valueChanged.connect(self._mark_dirty)
        for entry in self._custom_rows:
            self._connect_row_dirty(entry)

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

        self._capture_combo = QComboBox()
        self._capture_combo.addItem(tr("视频流截图 (scrcpy)"), True)
        self._capture_combo.addItem(tr("静态截图 (screencap)"), False)
        self._capture_combo.setCurrentIndex(0 if self._config.adb_capture_streaming else 1)
        form.addRow(tr("ADB 截图方式:"), self._capture_combo)

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
        add_row.addWidget(add_btn)
        add_row.addStretch()
        vbox.addLayout(add_row)

        vbox.addStretch()
        return tab

    # ─── Tab4 系统参数（可用工作环境）────────────────────

    def _build_env_tab(self) -> QWidget:
        """系统参数 Tab：可用工作环境列表"""
        tab = QWidget()
        vbox = QVBoxLayout(tab)

        caption = QLabel(tr("工作环境决定 DSL 中 env() 的返回值，用于区分 PC 游戏与手游的导航策略。"))
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
        settings = {
            "adb_capture_streaming": self._capture_combo.currentData(),
            "desktop_background_input": self._input_combo.currentData(),
            "desktop_window_title": self._title_edit.text().strip(),
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
        # 保存后不关闭：置灰保存按钮，当前各行均视为已保存，可继续修改
        for entry in self._custom_rows:
            entry["saved"] = bool(entry["key"].text().strip())
        for entry in self._env_rows:
            entry["saved"] = bool(entry["key"].text().strip())
        self._save_btn.setEnabled(False)
