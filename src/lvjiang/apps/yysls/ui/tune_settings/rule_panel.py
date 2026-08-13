"""单规则面板

左侧固定导航 + 右侧 QStackedWidget。
负责 收集 → 校验 → 写盘 → reload 流程：控件变更即校验，校验通过
才写盘并 reload；失败时不写盘、状态栏红字提示。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from loguru import logger
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.evaluator.tuning_rules import (
    DYNAMIC_AFFIXES,
    GENERIC_ATTR,
    TuningRuleManager,
    rule_affix_candidates,
    specific_attr_names,
)

from .....i18n import tr
from .common_judge_page import CommonJudgePage
from .part_pattern_page import PartPatternPage
from .pool_page import PoolPage
from .rule_settings_page import RuleSettingsPage

# 左侧导航条目：(标题, 部位 key（None = 非部位页）)
_NAV_ITEMS: list[tuple[str, str | None]] = [
    ("规则设置", None),
    ("词条库设置", None),
    ("通用判定", None),
    ("主武器", "主武器"),
    ("副武器", "副武器"),
    ("环 · 佩", "环"),
    ("冠胄 · 胸甲", "冠胄"),
    ("胫甲 · 腕甲", "胫甲"),
]  # runtime tr()

# 分割线插入位置：「词条库设置」之下（全局区 / 判定区语义分隔）
_SEP_ROW = 2


def add_nav_separator(nav: QListWidget) -> None:
    """导航列表分割线：不可选中的横线项，用于语义分区"""
    item = QListWidgetItem()
    item.setFlags(Qt.ItemFlag.NoItemFlags)
    item.setSizeHint(QSize(0, 9))
    nav.addItem(item)
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    nav.setItemWidget(item, line)


class RulePanel(QWidget):
    """单规则编辑面板（持有 raw dict 深拷贝为工作副本）"""

    # 对话框在创建面板时注入，用于重命名后更新 Tab 文本
    _dialog_rename_cb = None

    def __init__(self, key: str, manager: TuningRuleManager,
                 status_cb: Callable[[str, bool | str], None],
                 on_delete: Callable[[str], None] | None = None,
                 parent=None):
        super().__init__(parent)
        self._key = key
        self._manager = manager
        self._status_cb = status_cb
        self._on_delete = on_delete
        self._data = manager.get_raw(key)
        # 规则可引用词表（校验与池/转律库候选的统一来源）
        self._candidates = self._build_candidates()
        # 判定区（首词条/四档条件）候选：收窄为当前可用词条库。
        # 各页共享同一 list 实例，池变更后在 _on_changed 原地刷新，
        # 选择对话框打开时即读到最新池内容
        self._judge_candidates: list[str] = list(
            self._data.get("affix_pool") or [])
        self._init_ui()
        self._load_pages()

    def _build_candidates(self) -> list[str]:
        """含通用属性玩法（混搭流）时候选排除动态词条
        （不做动态归类，引用属死引用；保存校验为兜底）"""
        names = rule_affix_candidates()
        playstyles = self._data.get("playstyles") or {}
        has_generic = any(
            (str((ps or {}).get("attr") or GENERIC_ATTR).strip()
             or GENERIC_ATTR) == GENERIC_ATTR
            for ps in playstyles.values())
        if has_generic:
            names = [n for n in names if n not in DYNAMIC_AFFIXES]
        return names

    @property
    def rule_key(self) -> str:
        return self._key

    @property
    def rule_name(self) -> str:
        return str(self._data.get("name", ""))

    def set_rule_name(self, name: str):
        """对话框左侧导航双击重命名 → 写入工作副本并保存"""
        self._data["name"] = name
        self._settings_page.set_name(name)
        self._on_changed()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)

        body = QHBoxLayout()

        # ── 左侧导航 ──
        self._nav = QListWidget()
        for row, (title, _) in enumerate(_NAV_ITEMS):
            self._nav.addItem(tr(title))
            if row == _SEP_ROW - 1:
                add_nav_separator(self._nav)
        self._nav.setFixedWidth(140)
        self._nav.currentRowChanged.connect(self._on_nav_changed)
        body.addWidget(self._nav)

        # ── 右侧详情页 ──
        self._stack = QStackedWidget()
        self._settings_page = RuleSettingsPage(
            self._data, self._on_changed, on_delete=self._request_delete,
            on_rename=self._rename_rule,
            on_enable_changed=self._on_rule_enable_changed)
        # 回填启用状态（从 tune_config.tuning_rules 读取）
        self._settings_page.set_enabled(self._is_rule_enabled())
        self._stack.addWidget(self._wrap_scroll(self._settings_page))
        self._pool_page = PoolPage(self._candidates, self._on_changed)
        self._stack.addWidget(self._wrap_scroll(self._pool_page))
        self._common_page = CommonJudgePage(self._judge_candidates,
                                            self._on_changed)
        self._stack.addWidget(self._wrap_scroll(self._common_page))
        self._part_pages: list[PartPatternPage] = []
        for title, part_key in _NAV_ITEMS:
            if part_key is None:
                continue
            page = PartPatternPage(part_key, title, self._judge_candidates,
                                   self._on_changed)
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

    # ── 导航 ──

    def _load_pages(self):
        self._pool_page.load(self._data)
        self._common_page.load(self._data)
        for page in self._part_pages:
            page.load(self._data)

    def _on_nav_changed(self, row: int):
        item = self._nav.item(row)
        if row < 0 or item is None or not item.flags():
            return  # 分割线项不响应
        self._stack.setCurrentIndex(row if row < _SEP_ROW else row - 1)

    # ── 收集 → 校验 → 写盘 → reload ──

    def _on_changed(self):
        # 池可能刚被编辑：原地刷新判定区共享候选
        self._judge_candidates[:] = list(self._data.get("affix_pool") or [])
        err = self._manager.validate(self._data)
        if err:
            self._status_cb(tr("校验失败（未保存）：{err}").format(err=err), True)
            return
        try:
            self._manager.save_rule(self._key, self._data)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"调律规则 {self._key} 保存失败")
            self._status_cb(tr("保存失败：{e}").format(e=e), True)
            return
        now = datetime.now().strftime("%H:%M:%S")
        warn = self._soft_pool_warning()
        if warn:
            self._status_cb(tr("已保存并生效（{now}）").format(now=now) + "；" + warn, "warn")
        else:
            self._status_cb(tr("已保存并生效（{now}）").format(now=now), False)

    def _soft_pool_warning(self) -> str | None:
        """软校验（不阻止保存）：可用词条库同时含动态属攻与
        真实属攻（非无相）时提醒统一风格，避免名实混用"""
        pool = set(self._data.get("affix_pool") or [])
        if pool & set(DYNAMIC_AFFIXES) and pool & set(specific_attr_names()):
            return tr("发现同时配置 动态属攻和真实属攻，建议修正")
        return None

    def _request_delete(self):
        """规则设置页「删除本规则」→ 交由对话框执行删除并移除 Tab"""
        if self._on_delete is not None:
            self._on_delete(self._key)

    def _rename_rule(self, old_key: str, new_key: str, new_name: str):
        """规则设置页 key 变更 → 重命名文件并通知对话框更新 Tab"""
        try:
            self._manager.rename_rule(old_key, new_key)
        except Exception as e:  # noqa: BLE001
            self._status_cb(tr("重命名失败：{e}").format(e=e), True)
            raise
        self._key = new_key
        # 对话框在创建面板时注入 _dialog_rename_cb，用于更新 Tab 文本
        cb = self._dialog_rename_cb
        if cb is not None:
            cb(old_key, new_key, new_name)

    def _is_rule_enabled(self) -> bool:
        """从 tune_config.tuning_rules 读取当前规则启用状态"""
        try:
            from lvjiang.apps.yysls.evaluator.tuning_rules import get_tune_config
            tuning_rules = get_tune_config().tuning_rules
            return tuning_rules.get(self._key, True)
        except Exception:
            return True

    def _on_rule_enable_changed(self, enabled: bool):
        """规则设置页启用复选框变更 → 更新 tune_config 并 reload"""
        try:
            self._manager.set_rule_enabled(self._key, enabled)
            now = datetime.now().strftime("%H:%M:%S")
            status = tr("已启用") if enabled else tr("已禁用")
            self._status_cb(f"{status}（{now}）", False)
        except Exception as e:  # noqa: BLE001
            self._status_cb(tr("设置启用状态失败：{e}").format(e=e), True)

