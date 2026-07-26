"""单流派规则面板

变体 QTabBar（单变体隐藏）+ 左侧固定导航 + 右侧 QStackedWidget。
负责 收集 → 校验 → 写盘 → reload 流程：控件变更即校验，校验通过
才写盘并 reload；失败时不写盘、状态栏红字提示。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from loguru import logger
from PyQt6.QtWidgets import (
    QHBoxLayout, QListWidget, QScrollArea, QStackedWidget, QTabBar,
    QVBoxLayout, QWidget,
)

from src.apps.yysls.evaluator.rules import TuningRuleManager

from .part_pattern_page import PartPatternPage
from .school_settings_page import SchoolSettingsPage
from .variant_pool_page import VariantPoolPage

# 左侧导航条目：(标题, 部位 key（None = 非部位页）)
_NAV_ITEMS: list[tuple[str, str | None]] = [
    ("流派设置", None),
    ("转律与词条库", None),
    ("主武器", "主武器"),
    ("副武器", "副武器"),
    ("环 · 佩", "环"),
    ("冠胄 · 胸甲", "冠胄"),
    ("胫甲 · 腕甲", "胫甲"),
]


class SchoolRulePanel(QWidget):
    """单流派规则编辑面板（持有 raw dict 深拷贝为工作副本）"""

    def __init__(self, key: str, manager: TuningRuleManager,
                 status_cb: Callable[[str, bool], None], parent=None):
        super().__init__(parent)
        self._key = key
        self._manager = manager
        self._status_cb = status_cb
        self._data = manager.get_raw(key)
        self._variant_keys = list(self._data.get("variants") or {})
        self._init_ui()
        self._load_variant_pages()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)

        # ── 顶部变体切换（单变体隐藏） ──
        self._variant_bar = QTabBar()
        for vk in self._variant_keys:
            raw = (self._data["variants"].get(vk) or {})
            self._variant_bar.addTab(str(raw.get("name") or vk))
        self._variant_bar.currentChanged.connect(
            lambda *_: self._load_variant_pages())
        if len(self._variant_keys) <= 1:
            self._variant_bar.setVisible(False)
        layout.addWidget(self._variant_bar)

        body = QHBoxLayout()

        # ── 左侧导航 ──
        self._nav = QListWidget()
        self._nav.addItems([title for title, _ in _NAV_ITEMS])
        self._nav.setFixedWidth(140)
        self._nav.currentRowChanged.connect(self._on_nav_changed)
        body.addWidget(self._nav)

        # ── 右侧详情页 ──
        self._stack = QStackedWidget()
        self._settings_page = SchoolSettingsPage(self._data, self._on_changed)
        self._stack.addWidget(self._wrap_scroll(self._settings_page))
        self._pool_page = VariantPoolPage(self._on_changed)
        self._stack.addWidget(self._wrap_scroll(self._pool_page))
        self._part_pages: list[PartPatternPage] = []
        has_keep_pvp = bool(self._data.get("has_keep_pvp"))
        for title, part_key in _NAV_ITEMS:
            if part_key is None:
                continue
            page = PartPatternPage(part_key, title, self._on_changed,
                                   has_keep_pvp)
            self._part_pages.append(page)
            self._stack.addWidget(self._wrap_scroll(page))
        body.addWidget(self._stack, 1)
        layout.addLayout(body)
        self._nav.setCurrentRow(0)

    @staticmethod
    def _wrap_scroll(page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    # ── 变体与导航 ──

    def _current_variant(self) -> dict:
        if not self._variant_keys:
            return {}
        vk = self._variant_keys[max(self._variant_bar.currentIndex(), 0)]
        return self._data.setdefault("variants", {}).setdefault(vk, {})

    def _load_variant_pages(self):
        variant = self._current_variant()
        self._pool_page.load(variant)
        for page in self._part_pages:
            page.load(variant)

    def _on_nav_changed(self, row: int):
        if row >= 0:
            self._stack.setCurrentIndex(row)

    # ── 收集 → 校验 → 写盘 → reload ──

    def _on_changed(self):
        err = self._manager.validate(self._data)
        if err:
            self._status_cb(f"校验失败（未保存）：{err}", True)
            return
        try:
            self._manager.save_rule(self._key, self._data)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"调律规则 {self._key} 保存失败")
            self._status_cb(f"保存失败：{e}", True)
            return
        now = datetime.now().strftime("%H:%M:%S")
        self._status_cb(f"已保存并生效（{now}）", False)
