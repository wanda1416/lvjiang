"""武学配置面板：武学名 → 武器 + 属性。

武器和属性是武学的固有属性。流派和玩法都只引用武学，不再各自录入武器——
拆分之前这两个字段各录各的，写成「武器=枪 + 武学=无名剑法」也存得下来，
然后毕业率按枪算、词条按剑法找，全程没有任何地方会喊。
"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.button_styles import apply_button_style

from .....i18n import tr
from ...config import get_game_config
from ..layout_helpers import config_field_card, configure_navigation_list

_ATTRS_REL = "yysls/game_config.yaml"

_ATTRS = ["鸣金", "裂石", "破竹", "牵丝"]


class MartialArtPanel(QWidget):
    """左侧武学列表，右侧武器 + 属性。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._data: dict = {}
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
        self._list = QListWidget()
        configure_navigation_list(self._list, minimum_width=200)
        self._list.currentTextChanged.connect(self._on_selected)
        left_layout.addWidget(self._list)

        btns = QHBoxLayout()
        self._btn_add = QPushButton(tr("+ 武学"))
        self._btn_add.clicked.connect(self._on_add)
        btns.addWidget(self._btn_add)
        self._btn_del = QPushButton(tr("- 武学"))
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
        self._combo_weapon = QComboBox()
        self._combo_attr = QComboBox()
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
        from lvjiang.core.config.resolver import get_resolver
        try:
            self._data = get_resolver().load_merged(_ATTRS_REL)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"加载配置失败: {exc}")
            self._data = {}
        self._reload()

    def _entries(self) -> list[dict]:
        return [e for e in (self._data.get("martial_arts") or [])
                if isinstance(e, dict) and e.get("name")]

    def _save_data(self) -> None:
        from lvjiang.core.config.resolver import get_resolver
        try:
            get_resolver().save_merged(_ATTRS_REL, self._data)
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
        self._list.addItems([e["name"] for e in self._entries()])
        self._loading = False
        if keep:
            items = self._list.findItems(keep, Qt.MatchFlag.MatchExactly)
            if items:
                self._list.setCurrentItem(items[0])
        elif self._list.count():
            self._list.setCurrentRow(0)

    def _on_selected(self, name: str) -> None:
        cfg = next((e for e in self._entries() if e["name"] == name), {})
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
        entries = self._entries()
        for e in entries:
            if e["name"] == name:
                e["weapon"], e["attr"] = weapon, attr
                break
        else:
            entries.append({"name": name, "weapon": weapon, "attr": attr})
        entries.sort(key=lambda e: (str(e.get("attr") or ""),
                                    str(e.get("weapon") or ""), e["name"]))
        self._data["martial_arts"] = entries
        self._save_data()

    def _on_add(self) -> None:
        name, ok = QInputDialog.getText(self, tr("新增武学"), tr("武学名称:"))
        name = (name or "").strip()
        if not ok or not name:
            return
        if any(e["name"] == name for e in self._entries()):
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
        used = [s for s, cfg in (self._data.get("schools") or {}).items()
                if any((cfg.get(side) or {}).get("martial_art") == name
                       for side in ("main", "sub"))]
        if used:
            # 删了会让流派引用悬空，而悬空引用正是这次拆分要消灭的东西
            QMessageBox.warning(
                self, tr("无法删除"),
                tr("以下流派仍在使用该武学：{schools}").format(
                    schools="、".join(used)))
            return
        self._data["martial_arts"] = [
            e for e in self._entries() if e["name"] != name]
        self._save_data()
        self._reload()
