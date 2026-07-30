"""词条选择 + 排序对话框（包内复用）

上区展示已选词条，支持拖拽排序；下区两种形态：
- 默认（两级）：左列为词条归属分类（固定 5 类，来自
  GameConfigManager.get_affix_categories；动态词条归入「动态类」桶，
  插在属攻类之后），右列为该分类下且在候选集内的词条复选框网格；
- flat=True（平铺，供判定区等候选已收窄到可用词条库的场景）：
  无分类列，全部候选按归类顺序摊平为单一复选框网格。
勾选即追加到上区末尾，取消勾选即从上区移除。
候选中无归属者归入末尾「未归类」桶。

selected() 按上区当前顺序返回，供转律词条库 / 词条库 / 首词条 /
条件词条等场景统一复用。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.evaluator.tuning_rules import (
    DYNAMIC_AFFIXES,
    DYNAMIC_CATEGORY,
)
from lvjiang.apps.yysls.game_config import get_game_config

# 候选无归属时归入的兜底桶名
_UNCATEGORIZED = "未归类"

# 动态类展示位置锚点：插在该分类之后
_DYNAMIC_ANCHOR = "属攻类"

# 右列复选网格列数
_COLS = 3


class AffixSelectSortDialog(QDialog):
    """词条选择 + 排序对话框（上区拖拽排序，下区按归属勾选，
    flat=True 时下区按归类顺序平铺）"""

    def __init__(self, candidates: list[str], selected: list[str],
                 title: str = "选择词条", parent=None, *,
                 flat: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(560, 560)
        self._candidates = list(candidates)
        # 词条名 → 复选框（构建右列时按分类填充，故用单一映射便于同步）
        self._checks: dict[str, QCheckBox] = {}
        # 分类名 → 该分类下的候选词条（保持候选声明序）
        self._by_category = self._group_candidates()

        layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter, 1)

        # ── 上区：已选词条（拖拽排序）──
        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_header = QHBoxLayout()
        top_header.addWidget(QLabel("已选词条（可拖拽调整顺序）"))
        top_header.addStretch()
        btn_remove = QPushButton("移除选中")
        btn_remove.clicked.connect(self._remove_selected)
        top_header.addWidget(btn_remove)
        top_layout.addLayout(top_header)

        self._selected_list = QListWidget()
        self._selected_list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove)
        self._selected_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        for name in selected:
            self._selected_list.addItem(name)
        top_layout.addWidget(self._selected_list, 1)
        splitter.addWidget(top_widget)

        # ── 下区：两级（左列分类 + 右列复选）或平铺（单网格）──
        bottom_widget = QWidget()
        bottom_layout = QHBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        self._cat_list: QListWidget | None = None
        if not flat:
            self._cat_list = QListWidget()
            self._cat_list.setFixedWidth(120)
            self._cat_list.currentTextChanged.connect(self._show_category)
            for cat in self._by_category:
                self._cat_list.addItem(cat)
            bottom_layout.addWidget(self._cat_list)

        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setSpacing(6)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._grid_host)
        bottom_layout.addWidget(scroll, 1)
        splitter.addWidget(bottom_widget)

        splitter.setSizes([200, 320])

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if flat:
            # 平铺：全部候选按归类顺序摊平为单一网格
            self._fill_grid([n for names in self._by_category.values()
                             for n in names])
        elif self._cat_list is not None and self._cat_list.count() > 0:
            self._cat_list.setCurrentRow(0)

    # ── 候选分类 ──

    def _group_candidates(self) -> dict[str, list[str]]:
        """按归属把候选归类（保持候选声明序），无归属者入「未归类」

        动态词条（规则层词汇，不在游戏配置归属体系）特判归入
        「动态类」桶，插在属攻类之后展示。
        """
        cfg = get_game_config()
        buckets: dict[str, list[str]] = {}
        for name in self._candidates:
            if name in DYNAMIC_AFFIXES:
                cat = DYNAMIC_CATEGORY
            else:
                cat = cfg.get_affix_category(name) or _UNCATEGORIZED
            buckets.setdefault(cat, []).append(name)
        # 按 5 类固定顺序排列（动态类紧随属攻类），未归类置末尾
        ordered: dict[str, list[str]] = {}
        for cat in cfg.get_affix_categories():
            if cat in buckets:
                ordered[cat] = buckets[cat]
            if cat == _DYNAMIC_ANCHOR and DYNAMIC_CATEGORY in buckets:
                ordered[DYNAMIC_CATEGORY] = buckets[DYNAMIC_CATEGORY]
        if DYNAMIC_CATEGORY in buckets and DYNAMIC_CATEGORY not in ordered:
            ordered[DYNAMIC_CATEGORY] = buckets[DYNAMIC_CATEGORY]
        if _UNCATEGORIZED in buckets:
            ordered[_UNCATEGORIZED] = buckets[_UNCATEGORIZED]
        return ordered

    # ── 右列渲染 ──

    def _show_category(self, category: str):
        """切换分类：重建右列复选网格（两级形态）"""
        self._fill_grid(self._by_category.get(category, []))

    def _fill_grid(self, names: list[str]):
        """重建复选网格，勾选态与上区当前选择同步"""
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._checks.clear()
        chosen = set(self._current_selected())
        for i, name in enumerate(names):
            cb = QCheckBox(name)
            cb.setChecked(name in chosen)
            cb.toggled.connect(lambda checked, n=name: self._on_toggled(n, checked))
            self._grid.addWidget(cb, i // _COLS, i % _COLS)
            self._checks[name] = cb

    # ── 交互 ──

    def _on_toggled(self, name: str, checked: bool):
        if checked:
            if name not in self._current_selected():
                self._selected_list.addItem(name)
        else:
            for row in range(self._selected_list.count()):
                if self._selected_list.item(row).text() == name:
                    self._selected_list.takeItem(row)
                    break

    def _remove_selected(self):
        row = self._selected_list.currentRow()
        if row < 0:
            return
        name = self._selected_list.item(row).text()
        self._selected_list.takeItem(row)
        cb = self._checks.get(name)
        if cb is not None:
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)

    def _current_selected(self) -> list[str]:
        return [self._selected_list.item(i).text()
                for i in range(self._selected_list.count())]

    # ── 结果 ──

    def selected(self) -> list[str]:
        """按上区当前顺序返回已选词条"""
        return self._current_selected()
