"""调律管理中的本地频率页面。2026-10-31 新增。"""
from __future__ import annotations

import csv

from lvjiang.apps.shuijiao_tuning.core.tuning_history.frequency import (
    FrequencyRow,
    calculate_frequency,
)
from lvjiang.apps.shuijiao_tuning.core.tuning_history.frequency_groups import (
    attribute_members,
    format_stat,
    grouped_stat,
    initial_categories,
    initial_category,
)
from lvjiang.apps.shuijiao_tuning.core.tuning_history.repository import (
    TuningHistoryRepository,
)
from PyQt6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.config.equipment_slots import SLOT_SPECS
from lvjiang.apps.yysls.ui.loadout.background import JobController
from lvjiang.ui.combo_box import AutoWidthComboBox

SOURCE_LABELS = {"initial": "首词条观测", "tune": "自然调律"}


class TuningFrequencyWidget(QWidget):
    def __init__(self, repository: TuningHistoryRepository | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("frequencyPanel")
        self.setStyleSheet("""
            #frequencyPanel QGroupBox {border: 1px solid #dce4ef; border-radius: 9px;
                margin-top: 12px; padding: 12px 8px 8px; background: #ffffff; color: #243b53;}
            #frequencyPanel QGroupBox::title {subcontrol-origin: margin; left: 12px; padding: 0 5px;}
            #frequencyPanel QLabel {color: #52657a;}
        """)
        self.repository = repository
        self._rows: list[FrequencyRow] = []
        self._loaded = False
        self._job = JobController(self)
        self._job.finished.connect(self._show_result)
        self._job.failed.connect(lambda msg: self.status.setText("统计失败：" + msg))
        layout = QVBoxLayout(self)
        line = QHBoxLayout()
        self.user_combo = AutoWidthComboBox(width_mode="popup")
        self.user_combo.addItem("全部客户", "")
        self.part_combo = AutoWidthComboBox(width_mode="full")
        self.part_combo.addItem("全部部位", "")
        for part in dict.fromkeys(s.part for s in SLOT_SPECS):
            self.part_combo.addItem(part, part)
        self.level_spin = QSpinBox()
        self.level_spin.setRange(0, 999)
        self.level_spin.setSpecialValueText("全部等级")
        self.level_spin.setValue(115)
        self.level_spin.setEnabled(False)
        self.level_spin.setToolTip("频率仅统计115级金色装备，排除紫装的提前筛选偏差")
        self.slot_combo = AutoWidthComboBox(width_mode="full")
        self.slot_combo.addItem("全部槽位", None)
        for n in range(1, 6):
            self.slot_combo.addItem("固定首条" if n == 1 else f"调律第 {n-1} 条", n)
        for label, widget in [("客户", self.user_combo),
                              ("等阶", self.level_spin), ("槽位", self.slot_combo)]:
            line.addWidget(QLabel(label))
            line.addWidget(widget)
        layout.addLayout(line)
        self.part_combo.hide()
        line = QHBoxLayout()
        self.weapon_combo = AutoWidthComboBox(width_mode="full")
        self.weapon_combo.addItem("全部武器类型", "")
        self.first_combo = AutoWidthComboBox(width_mode="stretch")
        self.first_combo.addItem("全部首词条", "")
        for name in dict.fromkeys(n for s in SLOT_SPECS for n in initial_categories(s.part)):
            self.first_combo.addItem(name, name)
        line.addWidget(self.weapon_combo)
        line.addWidget(self.first_combo)
        line.addStretch()
        refresh = QPushButton("刷新统计")
        refresh.clicked.connect(self.refresh)
        line.addWidget(refresh)
        export = QPushButton("导出 CSV")
        export.clicked.connect(self._export)
        line.addWidget(export)
        layout.addLayout(line)
        self._initial_labels: dict[str, dict[str, QLabel]] = {}
        self._queries: dict[str, list[tuple[AutoWidthComboBox, QLabel, QLabel]]] = {}
        self._panel = QGroupBox("115级金装 · 八部位词条频率")
        grid = QGridLayout(self._panel)
        gc = get_game_config()
        for spec in SLOT_SPECS:
            card = QGroupBox({"main_weapon": "武器一", "sub_weapon": "武器二", "head": "头甲"}.get(spec.key, spec.label))
            body = QVBoxLayout(card)
            if spec.is_weapon:
                body.addWidget(QLabel("共用武器样本；可按武器类型筛选"))
            body.addWidget(QLabel("固定首条"))
            initial = QGridLayout()
            self._initial_labels[spec.key] = {}
            for i, name in enumerate(initial_categories(spec.part)):
                value_label = QLabel()
                value_label.setWordWrap(True)
                initial.addWidget(value_label, i // 2, i % 2)
                self._initial_labels[spec.key][name] = value_label
            body.addLayout(initial)
            body.addWidget(QLabel("自然调律（五个独立查询框）"))
            options = [n for n in gc.get_normal_affix_names() if spec.part in gc.get_affix_parts(n)]
            if not spec.is_weapon:
                members = attribute_members("最小属性攻击") + attribute_members("最大属性攻击")
                options = [n for n in options if n not in members]
                options += ["最小属性攻击", "最大属性攻击"]
            self._queries[spec.key] = []
            defaults = ["最小无相攻击", "最大无相攻击", "最小外功攻击", "最大外功攻击", "敏"] if spec.is_weapon else [
                "最小外功攻击", "最大外功攻击", "敏", "最小属性攻击", "最大属性攻击"]
            for default in defaults:
                row = QHBoxLayout()
                combo = AutoWidthComboBox(width_mode="popup", minimum_width=120)
                combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                for name in options:
                    combo.addItem(name, name)
                combo.setCurrentIndex(max(0, combo.findData(default)))
                stat = QLabel()
                row.addWidget(combo, 1)
                row.addWidget(stat)
                body.addLayout(row)
                detail = QLabel()
                detail.setWordWrap(True)
                body.addWidget(detail)
                self._queries[spec.key].append((combo, stat, detail))
                combo.currentIndexChanged.connect(self._render_cards)
            grid.addWidget(card, spec.row, spec.col)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._panel)
        layout.addWidget(scroll, 1)
        note = QLabel("自然调律频率 = 该词条新增次数 ÷ 同部位有效新增词条总数。"
                      "首条单独统计；不含定音、转律和未完成轮次。武器一、二共用武器词条池，可按武器类型筛选。"
                      "只统计115级金装（装备背景色），排除紫装及105/110级。首条重复扫描会重复观测；频率不代表官方掉率。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel("点击刷新统计，或打开本页自动读取本地历史。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        for combo in (self.user_combo, self.part_combo, self.slot_combo,
                      self.weapon_combo, self.first_combo):
            combo.currentIndexChanged.connect(self.refresh)
        self.level_spin.valueChanged.connect(self.refresh)
        self._render_cards()

    def _render_cards(self, *_):
        for spec in SLOT_SPECS:
            for name, label in self._initial_labels[spec.key].items():
                label.setText(name + "\n" + format_stat(*grouped_stat(self._rows, spec.part, "initial", name)))
            for combo, label, detail in self._queries[spec.key]:
                name = combo.currentData() or ""
                label.setText(format_stat(*grouped_stat(self._rows, spec.part, "tune", name)))
                members = attribute_members(name) if not spec.is_weapon else []
                values = []
                full = []
                for n in members:
                    hits, total = grouped_stat(self._rows, spec.part, "tune", n)
                    values.append(n[2:-2] + (f" {hits / total:.2%}" if total else " —"))
                    full.append(n + "：" + format_stat(hits, total))
                detail.setText("\n".join("    ".join(values[i:i + 2]) for i in range(0, len(values), 2)))
                detail.setToolTip("\n".join(full))
                detail.setVisible(bool(members))

    def refresh(self, *_args):
        filters = dict(username=self.user_combo.currentData(), part=self.part_combo.currentData(),
                       level=115, slot=self.slot_combo.currentData())
        weapon_filter = self.weapon_combo.currentData()
        first_filter = self.first_combo.currentData()
        allowed = get_game_config().get_normal_affix_names()
        self.status.setText("正在统计本地历史…")

        def work(_ctx):
            records = (self.repository or TuningHistoryRepository()).frequency_records()
            records = [r for r in records if r.get("level") == 115 and r.get("quality") == "gold"]
            users = sorted({r["username"] for r in records})
            weapons = sorted({r["equip_type"] for r in records
                              if r["slot_key"] in {"main_weapon", "sub_weapon"}})
            if weapon_filter:
                records = [r for r in records if r["slot_key"] not in {"main_weapon", "sub_weapon"}
                           or r["equip_type"] == weapon_filter]
            if first_filter:
                import json

                from lvjiang.apps.yysls.config.equipment_slots import slot_part

                filtered = []
                for record in records:
                    try:
                        affixes = json.loads(record.get("initial_affixes_json") or "[]")
                        name = affixes[0].get("name") if affixes else ""
                        if name and initial_category(slot_part(record.get("slot_key", "")), name) == first_filter:
                            filtered.append(record)
                    except (ValueError, TypeError, AttributeError, IndexError):
                        continue
                records = filtered
            rows, invalid = calculate_frequency(records, allowed=allowed, **filters)
            return rows, invalid, users, weapons

        self._job.start(work)

    @staticmethod
    def _fill_options(combo, default: str, values: list[str]):
        selected = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(default, "")
        for value in values:
            combo.addItem(value, value)
        combo.setCurrentIndex(max(0, combo.findData(selected)))
        combo.blockSignals(False)

    def _show_result(self, result):
        rows, invalid, users, weapons = result
        self._loaded = True
        self._rows = rows
        self._fill_options(self.user_combo, "全部客户", users)
        self._fill_options(self.weapon_combo, "全部武器类型", weapons)
        self._render_cards()
        self.status.setText(f"已更新八部位面板；排除 {invalid} 条无法归入自然观测的记录。"
                            if rows else "当前筛选没有有效样本。先执行调律或更改筛选。")

    def _export(self):
        if not self._rows:
            self.status.setText("没有有效样本，无法导出。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出词条频率", "词条频率.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["部位", "来源", "词条", "出现次数", "有效观测数", "频率"])
                for row in self._rows:
                    writer.writerow([row.part, SOURCE_LABELS[row.source], row.affix,
                                     row.hits, row.total, row.frequency])
            self.status.setText("已导出当前筛选的统计结果。")
        except OSError as exc:
            self.status.setText("导出失败：" + str(exc))
