"""游戏基础配置面板。"""

from datetime import datetime

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr
from ...config import get_game_config


class BasicConfigPanel(QWidget):
    """不依赖等级、装备类型等维度的全局游戏参数。"""

    def __init__(self, data: dict | None = None, on_changed=None, parent=None):
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
        form.addRow(tr("冷却时间:"), self._cooldown_days)
        self._cooldown_carryover = QCheckBox(tr("冷却到期后累计一次"))
        self._cooldown_carryover.setToolTip(tr(
            "逾期时间会抵扣下一轮冷却，最多保留一次可重置次数"))
        self._cooldown_carryover.toggled.connect(self._apply)
        form.addRow(tr("逾期进度:"), self._cooldown_carryover)
        layout.addWidget(box)

        self._status_label = QLabel()
        layout.addWidget(self._status_label)
        layout.addStretch()

    def _load(self) -> None:
        basic = (self._data or {}).get("basic_config", {})
        manager = get_game_config()
        self._cooldown_days.setValue(int(basic.get(
            "equipment_cooldown_days",
            manager.get_equipment_cooldown_days())))
        self._cooldown_carryover.setChecked(bool(basic.get(
            "equipment_cooldown_carryover",
            manager.is_equipment_cooldown_carryover_enabled())))

    def _apply(self, _value: int | bool) -> None:
        if self._loading:
            return
        manager = get_game_config()
        data = self._data if self._data is not None else manager.get_raw()
        basic = dict(data.get("basic_config") or {})
        basic["equipment_cooldown_days"] = self._cooldown_days.value()
        basic["equipment_cooldown_carryover"] = (
            self._cooldown_carryover.isChecked())
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
        self._status_label.setStyleSheet("color: #2e7d32;")
        self._status_label.setText(tr("已保存并生效（{time}）").format(
            time=datetime.now().strftime("%H:%M:%S")))
