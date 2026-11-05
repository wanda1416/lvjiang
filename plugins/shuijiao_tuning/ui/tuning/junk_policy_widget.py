"""桌面用户级垃圾计数配置。2026-10-31 新增。"""
from __future__ import annotations

from lvjiang.apps.shuijiao_tuning.core.junk_recycle import JunkRecyclePolicy
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.ui.tune_settings.affix_picker import AffixSelectSortDialog


class JunkPolicyWidget(QGroupBox):
    config_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("垃圾词条计数回收", parent)
        self._names = list(JunkRecyclePolicy().affixes)
        self._small_names = list(JunkRecyclePolicy().small_affixes)
        self._large_names = list(JunkRecyclePolicy().large_affixes)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.enabled_cb = QCheckBox("启用：累计垃圾词条达到")
        self.minimum_spin = QSpinBox()
        self.minimum_spin.setRange(1, 5)
        self.minimum_spin.setValue(2)
        self.minimum_spin.setSuffix(" 条就回收")
        row.addWidget(self.enabled_cb)
        row.addWidget(self.minimum_spin)
        row.addStretch()
        self.edit_button = QPushButton("修改垃圾名单")
        self.edit_button.clicked.connect(lambda: self._edit("affixes"))
        row.addWidget(self.edit_button)
        layout.addLayout(row)
        self.names_label = QLabel()
        self.names_label.setWordWrap(True)
        layout.addWidget(self.names_label)
        self.by_build_cb = QCheckBox("按当前大小外方案追加垃圾词条")
        self.by_build_cb.setChecked(True)
        layout.addWidget(self.by_build_cb)
        row = QHBoxLayout()
        for title, key in (("修改小外名单", "small_affixes"), ("修改大外名单", "large_affixes")):
            button = QPushButton(title)
            button.clicked.connect(lambda _checked=False, key=key: self._edit(key))
            row.addWidget(button)
        layout.addLayout(row)
        self.build_names_label = QLabel()
        self.build_names_label.setWordWrap(True)
        layout.addWidget(self.build_names_label)
        self.protect_cb = QCheckBox("保护所有已配置流派的顶级潜力（含一次合法转律）")
        self.protect_cb.setChecked(True)
        self.confirm_cb = QCheckBox("每次回收前询问：回收本件 / 保留本件 / 结束任务")
        layout.addWidget(self.protect_cb)
        layout.addWidget(self.confirm_cb)
        note = QLabel("首条准入与必需组合以「流派与词条」五框为准，组合判定独立于垃圾计数门槛。"
                      "115 阶金装垃圾首条先不加狗粮补满再回收，紫装直接回收。"
                      "其他流派毕业成品仍受保护；定音不计，锁定与冷却保护优先。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.enabled_cb.toggled.connect(self._changed)
        self.minimum_spin.valueChanged.connect(self._changed)
        self.protect_cb.toggled.connect(self._changed)
        self.confirm_cb.toggled.connect(self._changed)
        self.by_build_cb.toggled.connect(self._changed)
        self._update_label()

    def _changed(self, *_args):
        self._update_label()
        self.config_changed.emit()

    def _update_label(self):
        self.names_label.setText("通用垃圾：" + "、".join(self._names))
        self.build_names_label.setText("小外追加：" + ("、".join(self._small_names) or "无")
                                       + "\n大外追加：" + ("、".join(self._large_names) or "无")
                                       + "\n按所选流派分别计废；纯奶必需的玩家增效不计通用垃圾。")
        self.minimum_spin.setEnabled(self.enabled_cb.isChecked())

    def _edit(self, key: str):
        attr = {"affixes": "_names", "small_affixes": "_small_names", "large_affixes": "_large_names"}[key]
        dialog = AffixSelectSortDialog(
            [n for n in get_game_config().get_normal_affix_names() if n != "对玩家单位增效"], getattr(self, attr),
            "选择垃圾词条", self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            names = dialog.selected()
            if names or key != "affixes":
                setattr(self, attr, names)
                self._changed()

    def get_config(self) -> dict:
        return {"enabled": self.enabled_cb.isChecked(),
                "minimum": self.minimum_spin.value(), "affixes": list(self._names),
                "protect_other_builds": self.protect_cb.isChecked(),
                "confirm_recycle": self.confirm_cb.isChecked(), "by_build": self.by_build_cb.isChecked(),
                "small_affixes": list(self._small_names), "large_affixes": list(self._large_names)}

    def set_config(self, raw: dict | None):
        policy = JunkRecyclePolicy.from_config(raw)
        self.enabled_cb.blockSignals(True)
        self.minimum_spin.blockSignals(True)
        self.protect_cb.blockSignals(True)
        self.confirm_cb.blockSignals(True)
        self.by_build_cb.blockSignals(True)
        self.enabled_cb.setChecked(policy.enabled)
        self.minimum_spin.setValue(policy.minimum)
        self._names = list(policy.affixes)
        self.protect_cb.setChecked(policy.protect_other_builds)
        self.confirm_cb.setChecked(policy.confirm_recycle)
        self.by_build_cb.setChecked(policy.by_build)
        self._small_names = list(policy.small_affixes)
        self._large_names = list(policy.large_affixes)
        self.enabled_cb.blockSignals(False)
        self.minimum_spin.blockSignals(False)
        self.protect_cb.blockSignals(False)
        self.confirm_cb.blockSignals(False)
        self.by_build_cb.blockSignals(False)
        self._update_label()
