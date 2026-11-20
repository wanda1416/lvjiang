"""游戏基础配置面板。"""

from datetime import datetime

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr, tr_label
from ...config import get_game_config

#: 插件菜单可用键位：F5~F7（插件段）与 Alt+F5~F7、Alt+字母。
#: 具体合法性（区间、系统保留组合、重复）由 core 的校验器统一判定。
_PLUGIN_SHORTCUT_CHOICES = (
    [f"F{i}" for i in range(5, 8)]
    + [f"Alt+F{i}" for i in range(5, 8)]
    + [f"Alt+{letter}" for letter in "BCDEFGHIJKLMNOPQRSTUVWXYZ"]
)


def _default_shortcut(item_id: str) -> str:
    from ..menus import SHORTCUT_ITEMS

    return next(
        (entry.default for entry in SHORTCUT_ITEMS if entry.item_id == item_id),
        "",
    )


class BasicConfigPanel(QWidget):
    """不依赖等级、装备类型等维度的全局游戏参数。"""

    def __init__(self, parent=None, *, data: dict | None = None, on_changed=None):
        super().__init__(parent)
        self._data = data
        self._on_changed = on_changed
        self._loading = True
        self._init_ui()
        self._load()
        self._loading = False

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(QLabel(
            "<b>" + tr("基础配置") + "</b><br>"
            + tr("配置跨等级、跨装备类型共用的游戏规则。")
        ))

        box = QGroupBox(tr("装备养成"))
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._cooldown_days = QSpinBox()
        self._cooldown_days.setRange(1, 365)
        self._cooldown_days.setSuffix(" " + tr("天"))
        self._cooldown_days.setMinimumWidth(140)
        self._cooldown_days.setToolTip(tr(
            "扫描装备发生转律后的冷却天数"))
        self._cooldown_days.valueChanged.connect(self._apply)
        form.addRow(tr_label('装备重置冷却时间'), self._cooldown_days)
        self._reset_min_wait = QSpinBox()
        self._reset_min_wait.setRange(0, 60)
        self._reset_min_wait.setSuffix(tr(" 秒"))
        self._reset_min_wait.setMinimumWidth(140)
        self._reset_min_wait.setToolTip(tr(
            "重置调律两次确认之间的最短等待。游戏新版为 3 秒；"
            "程序在此之后还会再等一次页面刷新再点二次确认，避免弹窗没出现就被点空。"
            "填 0 表示不做这段额外等待。"))
        self._reset_min_wait.valueChanged.connect(self._apply)
        form.addRow(tr_label('重置二次确认时间'), self._reset_min_wait)
        self._cooldown_carryover = QCheckBox(tr("冷却到期后累计一次"))
        self._cooldown_carryover.setToolTip(tr(
            "逾期时间会抵扣下一轮冷却，最多保留一次可重置次数"))
        self._cooldown_carryover.toggled.connect(self._apply)
        form.addRow(tr_label('重置冷却逾期进度'), self._cooldown_carryover)
        self._stone_percent = QDoubleSpinBox()
        self._stone_percent.setRange(0.1, 100.0)
        self._stone_percent.setDecimals(1)
        self._stone_percent.setSingleStep(0.1)
        self._stone_percent.setSuffix(" %")
        self._stone_percent.setMinimumWidth(140)
        self._stone_percent.setToolTip(tr(
            "一次传律只提升一条词条，增量是该词条在当前等级普通满值的这个百分比。"
            "装备「养成」里按它估算承音喂满还需要几个传律石。"))
        self._stone_percent.valueChanged.connect(self._apply)
        form.addRow(tr_label('装备培养单次提升'), self._stone_percent)
        layout.addWidget(box)
        layout.addWidget(self._build_hotkey_box())

        self._status_label = QLabel()
        layout.addWidget(self._status_label)
        layout.addStretch()

    def _build_hotkey_box(self) -> QGroupBox:
        """插件菜单的快捷键：区间 F5~F7 与 Alt+字母，与通用菜单共用查重。"""
        from .....ui.widgets.combo_box import AutoWidthComboBox, ComboWidthMode
        from ..menus import SHORTCUT_ITEMS

        box = QGroupBox(tr("热键设置"))
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._shortcut_combos: dict[str, object] = {}
        for entry in SHORTCUT_ITEMS:
            combo = AutoWidthComboBox(width_mode=ComboWidthMode.POPUP)
            combo.addItem(tr("（不绑定）"), "")
            combo.addItems(_PLUGIN_SHORTCUT_CHOICES)
            combo.setFixedWidth(120)
            combo.currentIndexChanged.connect(self._apply)
            self._shortcut_combos[entry.item_id] = combo
            form.addRow(f"{tr(entry.label)}:", combo)
        hint = QLabel(tr(
            "菜单快捷键只在窗口内生效；插件菜单可用 F5~F7 或 Alt+字母，"
            "不能与通用/工具菜单重复。"))
        hint.setWordWrap(True)
        form.addRow(hint)
        return box

    def _load(self) -> None:
        basic = (self._data or {}).get("basic_config", {})
        manager = get_game_config()
        self._cooldown_days.setValue(int(basic.get(
            "equipment_cooldown_days",
            manager.get_equipment_cooldown_days())))
        self._cooldown_carryover.setChecked(bool(basic.get(
            "equipment_cooldown_carryover",
            manager.is_equipment_cooldown_carryover_enabled())))
        self._stone_percent.setValue(float(basic.get(
            "chuanlv_stone_percent",
            manager.get_chuanlv_stone_percent())))
        self._reset_min_wait.setValue(int(basic.get(
            "reset_min_wait_seconds",
            manager.get_reset_min_wait_seconds())))
        self._load_shortcuts(basic)

    def _load_shortcuts(self, basic: dict) -> None:
        from ..menus import SHORTCUT_ITEMS, resolved

        overrides = basic.get("hotkeys")
        overrides = overrides if isinstance(overrides, dict) else {}
        for entry in SHORTCUT_ITEMS:
            combo = self._shortcut_combos.get(entry.item_id)
            if combo is None:
                continue
            current = resolved(entry.item_id, overrides)
            combo.blockSignals(True)
            index = combo.findText(current) if current else 0
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)

    def _apply(self, _value: int | float | bool) -> None:
        if self._loading:
            return
        manager = get_game_config()
        data = self._data if self._data is not None else manager.get_raw()
        basic = dict(data.get("basic_config") or {})
        basic["equipment_cooldown_days"] = self._cooldown_days.value()
        basic["equipment_cooldown_carryover"] = (
            self._cooldown_carryover.isChecked())
        basic["chuanlv_stone_percent"] = round(
            self._stone_percent.value(), 2)
        basic["reset_min_wait_seconds"] = self._reset_min_wait.value()
        shortcuts = self._collect_shortcuts()
        if shortcuts is None:
            return
        if shortcuts:
            basic["hotkeys"] = shortcuts
        else:
            basic.pop("hotkeys", None)
        data["basic_config"] = basic
        if self._on_changed is not None:
            self._on_changed()
            self._status_label.setStyleSheet("")
            self._status_label.setText(tr("有未保存的更改"))
            return
        try:
            manager.save(data)
        except Exception as exc:  # noqa: BLE001 - 保存错误直接展示给用户
            logger.exception("基础配置保存失败")
            self._status_label.setStyleSheet("color: #c62828;")
            self._status_label.setText(tr("保存失败：{error}").format(error=exc))
            return
        # 菜单快捷键即时生效：动作就在本窗口的菜单栏上，不需要重启。
        try:
            from ..menus import apply_shortcuts

            apply_shortcuts(shortcuts)
        except Exception as exc:  # noqa: BLE001 - 应用失败不影响保存结果
            logger.warning(f"插件菜单快捷键应用失败: {exc}")
        self._status_label.setStyleSheet("color: #2e7d32;")
        self._status_label.setText(tr("已保存并生效（{time}）").format(
            time=datetime.now().strftime("%H:%M:%S")))

    def _collect_shortcuts(self) -> dict[str, str] | None:
        """收集插件菜单键位：越区间/重复时报错并返回 None。"""
        from .....core.config.menu_shortcuts import (
            ZONE_PLUGIN_MENU,
            validate_menu_shortcuts,
        )
        from ..menus import SHORTCUT_ITEMS

        values: dict[str, str] = {}
        for entry in SHORTCUT_ITEMS:
            combo = self._shortcut_combos.get(entry.item_id)
            if combo is None:
                continue
            index = combo.currentIndex()
            values[entry.item_id] = "" if index <= 0 else str(
                combo.currentText() or "")
        problems = validate_menu_shortcuts(values, owner=ZONE_PLUGIN_MENU)
        if problems:
            self._status_label.setStyleSheet("color: #c62828;")
            self._status_label.setText(
                tr("快捷键不可用：") + problems[0])
            return None
        # 只落盘与默认值不同的项，配置里不堆满当前默认键位。
        return {item_id: value for item_id, value in values.items()
                if value != _default_shortcut(item_id)}
