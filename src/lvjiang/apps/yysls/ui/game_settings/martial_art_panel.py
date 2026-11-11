"""武学配置面板：武学名 → 武器 + 属性。

武器和属性是武学的固有属性。流派和玩法都只引用武学，不再各自录入武器——
拆分之前这两个字段各录各的，写成「武器=枪 + 武学=无名剑法」也存得下来，
然后毕业率按枪算、词条按剑法找，全程没有任何地方会喊。
"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.button_styles import apply_button_style
from lvjiang.ui.combo_box import AutoWidthComboBox
from lvjiang.ui.layout_helpers import config_field_card, configure_navigation_list

from .....i18n import tr, tr_label
from ...config import get_game_config
from .order_list import DragOrderListWidget, reorder_mapping

_ATTRS_REL = "yysls/game_config"

_ATTRS = ["鸣金", "裂石", "破竹", "牵丝"]


class MartialArtPanel(QWidget):
    """左侧武学列表，右侧武器 + 属性。"""

    def __init__(self, parent=None, *, data: dict | None = None, on_changed=None):
        super().__init__(parent)
        self._loading = False
        self._data: dict = data if data is not None else {}
        self._external_data = data is not None
        self._on_changed = on_changed
        self._build_ui()
        self._load_data()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # 左侧：武学类型列表（与词组配置/装备配置同款导航栏）
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel(tr("武学类型")))
        self._list = DragOrderListWidget()
        configure_navigation_list(self._list, minimum_width=200)
        self._list.currentTextChanged.connect(self._on_selected)
        self._list.order_changed.connect(self._on_order_changed)
        left_layout.addWidget(self._list)

        btns = QHBoxLayout()
        self._btn_add = QPushButton("+" + tr('武学'))
        self._btn_add.clicked.connect(self._on_add)
        btns.addWidget(self._btn_add)
        self._btn_del = QPushButton("-" + tr('武学'))
        self._btn_del.clicked.connect(self._on_delete)
        btns.addWidget(self._btn_del)
        apply_button_style(self._btn_add)
        apply_button_style(self._btn_del, variant="danger")
        left_layout.addLayout(btns)
        splitter.addWidget(left_widget)

        # 右侧：每项配置使用独立区域，避免普通表单挤成一小团。
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        self._combo_weapon = AutoWidthComboBox()
        self._combo_attr = AutoWidthComboBox()
        self._combo_attr.addItems(_ATTRS)
        for combo in (self._combo_weapon, self._combo_attr):
            combo.currentTextChanged.connect(self._on_field_changed)
        right_layout.addWidget(config_field_card(
            tr("武器"), self._combo_weapon))
        right_layout.addWidget(config_field_card(
            tr("属性"), self._combo_attr))
        hint = QLabel(tr("流派与玩法只引用武学，武器由此派生，不在别处录入"))
        hint.setWordWrap(True)
        hint.setContentsMargins(14, 2, 14, 0)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        right_layout.addWidget(hint)
        right_layout.addStretch()
        splitter.addWidget(right_widget)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 650])

    # ── 数据 ──

    def _load_data(self) -> None:
        if not self._external_data:
            try:
                from ...config.game_config_files import load_game_config
                self._data = load_game_config()
            except Exception as exc:  # noqa: BLE001
                logger.error(f"加载配置失败: {exc}")
                self._data = {}
        self._reload()

    def _names(self) -> list[str]:
        """武学名列表；键序即配置声明顺序，直接用于候选与列表。"""
        return [str(name) for name in (self._data.get("martial_arts") or {})]

    def _entry(self, name: str) -> dict:
        raw = (self._data.get("martial_arts") or {}).get(name)
        return raw if isinstance(raw, dict) else {}

    def _save_data(self) -> None:
        if self._on_changed is not None:
            self._on_changed()
            return
        try:
            from ...config.game_config_files import save_game_config
            save_game_config(self._data)
            get_game_config().reload()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"保存配置失败: {exc}")

    def _reload(self) -> None:
        gc = get_game_config()
        self._loading = True
        self._combo_weapon.clear()
        self._combo_weapon.addItems(gc.get_weapon_types())
        current = self._list.currentItem()
        keep = current.text() if current else ""
        self._list.clear()
        self._list.addItems(self._names())
        self._loading = False
        if keep:
            items = self._list.findItems(keep, Qt.MatchFlag.MatchExactly)
            if items:
                self._list.setCurrentItem(items[0])
        elif self._list.count():
            self._list.setCurrentRow(0)

    def _on_order_changed(self) -> None:
        """拖动后按当前行序重写武学键序，保持选中项（用户模式无入口）。"""
        if not self._list.is_reorderable():
            return
        names = [item.text() for index in range(self._list.count())
                 if (item := self._list.item(index)) is not None]
        entries = self._data.get("martial_arts") or {}
        ordered = reorder_mapping(entries, names)
        if list(ordered) != list(entries):
            self._data["martial_arts"] = ordered
            self._save_data()
        # 重建列表并保持选中项：_reload 会按当前行文本恢复选中，拖动后仍是
        # 被拖的那一条。
        self._reload()

    def _on_selected(self, name: str) -> None:
        cfg = self._entry(name)
        self._loading = True
        self._combo_weapon.setCurrentText(cfg.get("weapon", ""))
        self._combo_attr.setCurrentText(cfg.get("attr", ""))
        self._loading = False

    def _on_field_changed(self, _text: str) -> None:
        if self._loading:
            return
        item = self._list.currentItem()
        if item is None:
            return
        self._write(item.text(), self._combo_weapon.currentText(),
                    self._combo_attr.currentText())

    def _write(self, name: str, weapon: str, attr: str) -> None:
        """写一条武学：已有条目原位改值，新条目追加到末尾（键序即顺序）。"""
        entries = self._data.setdefault("martial_arts", {})
        entry = entries.get(name)
        if isinstance(entry, dict):
            entry["weapon"], entry["attr"] = weapon, attr
        else:
            entries[name] = {"weapon": weapon, "attr": attr}
        self._save_data()

    def _on_add(self) -> None:
        name, ok = QInputDialog.getText(self, tr("新增武学"), tr_label('武学名称'))
        name = (name or "").strip()
        if not ok or not name:
            return
        if name in (self._data.get("martial_arts") or {}):
            QMessageBox.warning(self, tr("新增武学"), tr("该武学已存在"))
            return
        self._write(name, self._combo_weapon.currentText() or "",
                    self._combo_attr.currentText() or _ATTRS[0])
        self._reload()

    def _on_delete(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        name = item.text()
        # 武学被流派和玩法同时引用：两处都要查，只查流派会留下玩法 arts 里的
        # 悬空引用（匹配不到任何玩法，用户也没有修复入口）。
        used = [str(school) for school, cfg in
                (self._data.get("schools") or {}).items()
                if any((cfg.get(side) or {}).get("martial_art") == name
                       for side in ("main", "sub"))]
        used += [str(style) for style, cfg in
                 (self._data.get("playstyles") or {}).items()
                 if name in (cfg.get("arts") or [])]
        if used:
            # 删了会让引用悬空，而悬空引用正是这次拆分要消灭的东西
            QMessageBox.warning(
                self, tr("无法删除"),
                tr("以下流派或玩法仍在使用该武学：{schools}").format(
                    schools="、".join(used)))
            return
        entries = self._data.get("martial_arts")
        if isinstance(entries, dict):
            entries.pop(name, None)
        self._save_data()
        self._reload()
