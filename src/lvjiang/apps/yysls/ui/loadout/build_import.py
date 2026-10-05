"""导入出装搭配：固定目标方案，预览后确认原子替换。"""
from __future__ import annotations

import copy

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from lvjiang.ui.combo_box import AutoWidthComboBox, ComboWidthMode

from .....i18n import tr
from ...config import get_game_config
from ...config.builds import BuildRepository, check_requirements
from ...core.loadout.affix_distribution import distribution_counts
from ...core.loadout.build_import import equipment_for_plan, import_build
from ...core.loadout.repository import LoadoutRepository
from .build_calculator import BUILD_DISPLAY_SLOTS, _cell, _table


class BuildImportDialog(QDialog):
    def __init__(self, username: str, repo: LoadoutRepository, *, parent=None, builds=None):
        super().__init__(parent)
        self.setWindowTitle(tr("导入出装搭配"))
        self.repo = repo
        self.gc = get_game_config()
        self.state = repo.load()
        self.plan = copy.deepcopy(self.state.active_plan)
        self.expected_plan = self.plan.to_dict()
        self.builds_repo = builds or BuildRepository()
        self.builds = copy.deepcopy(self.builds_repo.all(self.plan.playstyle)) if self.plan.playstyle else []
        self._equipment: dict[str, dict] = {}
        layout = QVBoxLayout(self)
        target = QLabel(tr("目标用户：{user}　备战方案：{plan}　玩法：{style}").format(
            user=username, plan=self.plan.name, style=self.plan.playstyle or tr("未选择")))
        target.setWordWrap(True)
        target.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(target)
        self.combo = AutoWidthComboBox(width_mode=ComboWidthMode.FULL)
        for build in self.builds:
            self.combo.addItem(f"{build.name} · {build.level}级 · {'承音' if build.chengyin else '普通'}", build.id)
        layout.addWidget(self.combo)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.details)
        self.table = _table(["位置", "宫", "商", "角", "徵", "羽", "套装"])
        header = self.table.horizontalHeader()
        assert header is not None
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setWordWrap(False)
        layout.addWidget(self.table)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        note = QLabel(tr("生成八件模拟装备并替换目标方案的出装，原装备保留；同时应用搭配的弓玦，战斗类型设为 PVE。"))
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert button is not None
        self.import_button = button
        self.import_button.setText(tr("导入"))
        buttons.accepted.connect(self._import)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.combo.currentIndexChanged.connect(self._preview)
        self._preview()
        self.resize(1100, 530)

    def _preview(self, *_args):
        self.table.setRowCount(0)
        self._equipment = {}
        self.import_button.setEnabled(False)
        index = self.combo.currentIndex()
        if index < 0:
            self.status.setText(tr("当前玩法没有可导入的出装搭配"))
            return
        build = self.builds[index]
        level = build.gongjue_level or self.gc.gongjue_level_for(build.level)
        self.details.setText(tr("弓玦：{name}（{level}级）　战斗类型：{type}").format(
            name=build.gongjue or tr("无"), level=level, type=build.combat_type.upper()))
        try:
            if build.level > self.state.effective_world_level(self.gc.current_equip_level()):
                raise ValueError(tr("搭配等级高于当前用户世界等级，请选择适用等级的搭配"))
            self._equipment = equipment_for_plan(build, self.plan, self.gc)
        except (ValueError, KeyError, TypeError) as exc:
            self.status.setText(str(exc))
            return
        self.table.setRowCount(len(BUILD_DISPLAY_SLOTS))
        for row, spec in enumerate(BUILD_DISPLAY_SLOTS):
            equip = self._equipment[spec.key]
            _cell(self.table, row, 0, spec.label)
            for i in range(1, 6):
                _cell(self.table, row, i, (equip.get(f"affix_{i}") or {}).get("name", "—"))
            set_key = equip.get("equipment_set") or ""
            _cell(self.table, row, 6, (self.gc.get_equipment_sets().get(set_key) or {}).get("name") or tr("无"))
        self.table.resizeRowsToContents()
        attribute = str((self.gc.get_playstyle(build.playstyle) or {}).get("attr") or "")
        counts = distribution_counts(self._equipment, attribute, self.gc)
        requirements = check_requirements(counts, self.builds_repo.common_requirements(build.combat_type) + build.requirements)
        missing = [row["affix"] for row in requirements if row["priority"] == "required" and not row["satisfied"]]
        self.status.setText(tr("词条数：{count}/40").format(count=sum(counts.values()))
                            + (tr("；强制要求未满足：") + "、".join(missing) if missing else ""))
        self.import_button.setEnabled(True)

    def _import(self):
        if not self.import_button.isEnabled() or not self._equipment:
            return
        build = self.builds[self.combo.currentIndex()]
        if any(self.plan.equipment.values()):
            message = tr(
                "将出装搭配「{build}」导入备战方案「{plan}」，替换八个槽位的装备。\n"
                "原装备保留在当前用户装备库中，不会删除，也不会影响其他备战方案。\n"
                "同时应用该搭配的弓玦，战斗类型设为 PVE：\n{details}\n是否继续？"
            ).format(build=build.name, plan=self.plan.name, details=self.details.text())
            if QMessageBox.question(self, tr("确认替换当前出装"), message,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                    QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
                return
        try:
            import_build(self.repo, self.plan.id, build, expected_plan=self.expected_plan, game_config=self.gc)
        except (ValueError, KeyError, TypeError, OSError, TimeoutError) as exc:
            QMessageBox.warning(self, tr("导入失败"), str(exc))
            return
        self.accept()
