"""智能调律公共配置页。"""
from __future__ import annotations

from typing import Callable

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.core.tuning_rules import (
    BEHAVIOR_ACTION_LABELS,
    BEHAVIOR_ACTION_TOOLTIPS,
    BEHAVIOR_ACTIONS,
    TuneConfigManager,
)

from .....i18n import tr


class SmartTuningPage(QWidget):
    """编辑 tune_config.yaml.smart_tuning，不随基础规则组切换。"""

    def __init__(self, manager: TuneConfigManager,
                 status_cb: Callable[[str, bool], None], parent=None):
        super().__init__(parent)
        self._manager = manager
        self._status_cb = status_cb
        self._loading = True
        self._init_ui()
        self._load()
        self._loading = False

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        title = QLabel("<b>" + tr("智能调律") + "</b>")
        layout.addWidget(title)
        description = QLabel(tr(
            "仅在现有结束处理决定继续调律后执行。系统会基于"
            "备战方案和承音上限推演当前装备的最大可能毕业率；"
            "计算失败或结果不可靠时始终放行。"))
        description.setWordWrap(True)
        description.setStyleSheet("color: #666;")
        layout.addWidget(description)

        self._enabled = QCheckBox(tr("启用智能调律"))
        self._enabled.toggled.connect(self._changed)
        layout.addWidget(self._enabled)

        scope_box = QGroupBox(tr("备战方案范围"))
        scope_layout = QVBoxLayout(scope_box)
        self._scope_group = QButtonGroup(self)
        self._scope_incoming = QRadioButton(tr("传入玩法"))
        self._scope_incoming.setToolTip(tr(
            "只评估本次自动调律实际勾选的规则与玩法，避免为"
            "本次不打算培养的方案消耗材料。"))
        self._scope_all = QRadioButton(tr("全部玩法"))
        self._scope_all.setToolTip(tr(
            "评估所有已启用调律规则所覆盖的玩法。"))
        self._scope_group.addButton(self._scope_incoming)
        self._scope_group.addButton(self._scope_all)
        self._scope_incoming.toggled.connect(self._changed)
        self._scope_all.toggled.connect(self._changed)
        scope_layout.addWidget(self._scope_incoming)
        scope_layout.addWidget(self._scope_all)
        layout.addWidget(scope_box)

        evaluation_box = QGroupBox(tr("最大可能毕业率判定"))
        evaluation_layout = QVBoxLayout(evaluation_box)
        self._evaluation_enabled = QCheckBox(tr("启用判定"))
        self._evaluation_enabled.toggled.connect(self._changed)
        evaluation_layout.addWidget(self._evaluation_enabled)
        operator_row = QHBoxLayout()
        operator_row.addWidget(QLabel(tr("候选装备最大可能毕业率")))
        self._operator = QComboBox()
        self._operator.addItem(tr("大于"), "gt")
        self._operator.addItem(tr("大于等于"), "gte")
        self._operator.setMinimumWidth(110)
        self._operator.currentIndexChanged.connect(self._changed)
        operator_row.addWidget(self._operator)
        operator_row.addWidget(QLabel(tr("当前方案穿戴装备")))
        operator_row.addSpacing(16)
        operator_row.addWidget(QLabel(tr("毕业率精度")))
        self._precision = QComboBox()
        self._precision.addItem("1%", 0.01)
        self._precision.addItem("0.1%", 0.001)
        self._precision.addItem("0.01%", 0.0001)
        self._precision.setMinimumWidth(90)
        self._precision.setToolTip(tr(
            "比较前，候选装备与当前穿戴装备的毕业率都会按所选精度"
            "向下取整。例如 94.59% 在 0.1% 精度下按 94.5% 判断。"))
        self._precision.currentIndexChanged.connect(self._changed)
        operator_row.addWidget(self._precision)
        operator_row.addStretch()
        evaluation_layout.addLayout(operator_row)
        layout.addWidget(evaluation_box)

        action_box = QGroupBox(tr("无法提升时的处理"))
        action_layout = QVBoxLayout(action_box)
        self._action_enabled = QCheckBox(tr("启用处理动作"))
        self._action_enabled.setToolTip(tr(
            "关闭时只记录智能判定结果，不改变原有调律流程。"))
        self._action_enabled.toggled.connect(self._changed)
        action_layout.addWidget(self._action_enabled)
        action_row = QHBoxLayout()
        action_row.addWidget(QLabel(tr("处理动作")))
        self._action = QComboBox()
        self._action.setMinimumWidth(180)
        for key in BEHAVIOR_ACTIONS:
            self._action.addItem(BEHAVIOR_ACTION_LABELS.get(key, key), key)
            index = self._action.count() - 1
            self._action.setItemData(
                index, BEHAVIOR_ACTION_TOOLTIPS.get(key, ""),
                Qt.ItemDataRole.ToolTipRole)
        self._action.currentIndexChanged.connect(self._changed)
        action_row.addWidget(self._action)
        action_row.addStretch()
        action_layout.addLayout(action_row)
        layout.addWidget(action_box)
        layout.addStretch()

        self._dependent_widgets = (
            scope_box, evaluation_box, action_box,
        )

    def _load(self) -> None:
        config = self._manager.get().smart_tuning
        self._enabled.setChecked(config.enabled)
        self._scope_incoming.setChecked(config.plan_scope == "incoming")
        self._scope_all.setChecked(config.plan_scope == "all")
        self._evaluation_enabled.setChecked(config.evaluation.enabled)
        self._operator.setCurrentIndex(max(
            0, self._operator.findData(config.evaluation.operator)))
        self._precision.setCurrentIndex(max(
            0, self._precision.findData(config.evaluation.precision)))
        self._action_enabled.setChecked(config.failure_action.enabled)
        self._action.setCurrentIndex(max(
            0, self._action.findData(config.failure_action.action)))
        self._sync_enabled()

    def _sync_enabled(self) -> None:
        enabled = self._enabled.isChecked()
        for widget in self._dependent_widgets:
            widget.setEnabled(enabled)
        self._operator.setEnabled(
            enabled and self._evaluation_enabled.isChecked())
        self._precision.setEnabled(
            enabled and self._evaluation_enabled.isChecked())
        self._action.setEnabled(enabled and self._action_enabled.isChecked())

    def _build(self) -> dict:
        data = self._manager.get_raw()
        data["smart_tuning"] = {
            "enabled": self._enabled.isChecked(),
            "plan_scope": (
                "all" if self._scope_all.isChecked() else "incoming"),
            "evaluation": {
                "enabled": self._evaluation_enabled.isChecked(),
                "operator": self._operator.currentData() or "gt",
                "precision": self._precision.currentData() or 0.001,
            },
            "failure_action": {
                "enabled": self._action_enabled.isChecked(),
                "action": self._action.currentData() or "skip",
            },
        }
        return data

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        self._sync_enabled()
        data = self._build()
        error = self._manager.validate(data)
        if error:
            self._status_cb(
                tr("校验失败（未保存）：{err}").format(err=error), True)
            return
        try:
            self._manager.save(data)
        except Exception as exc:  # noqa: BLE001
            logger.exception("智能调律配置暂存失败")
            self._status_cb(tr("保存失败：{e}").format(e=exc), True)
            return
        self._status_cb(tr("智能调律配置已暂存"), False)
