from __future__ import annotations

from PyQt6 import QtWidgets

from lvjiang.core.profile.models import (
    MODEL_QUOTA,
    MODEL_STOCK,
    QuotaKeyDef,
    StepDef,
    StockKeyDef,
)
from lvjiang.ui.profile import cell_editing
from lvjiang.ui.profile.cell_editing import ProfileCellEditingMixin
from lvjiang.ui.profile.settings_dialog import _ChangeRulesWidget


def test_change_rules_merge_legacy_vocab_and_steps(qtbot):
    widget = _ChangeRulesWidget(
        sources=["邮件赠送", "旧来源"],
        uses=["和鸣抽奖"],
        steps=[
            StepDef(1, "限时活动"),
            StepDef(-1, "和鸣抽奖"),
            StepDef(1, "邮件赠送"),
            StepDef(-10, "和鸣抽奖"),
        ],
    )
    qtbot.addWidget(widget)

    sources, uses, steps = widget.get_rules()

    assert uses == ["和鸣抽奖"]
    assert sources == ["邮件赠送", "旧来源", "限时活动"]
    assert steps == [
        StepDef(-1, "和鸣抽奖"),
        StepDef(-10, "和鸣抽奖"),
        StepDef(1, "邮件赠送"),
        StepDef(1, "限时活动"),
    ]


def test_change_rules_require_name_for_shortcut_amount(qtbot):
    widget = _ChangeRulesWidget([], [], [])
    qtbot.addWidget(widget)
    widget.add_row(widget._KIND_USE, amount=10)

    assert "填写来源或用途" in widget.validation_error()


class _Signal:
    def connect(self, _slot):
        pass


class _Action:
    def __init__(self, text: str):
        self.text = text
        self.triggered = _Signal()


class _Menu:
    last_items: list[str | None] = []

    def __init__(self, _parent=None):
        self.items: list[str | None] = []
        _Menu.last_items = self.items

    def setTitle(self, _title: str):
        pass

    def addAction(self, text: str):
        self.items.append(text)
        return _Action(text)

    def addSeparator(self):
        self.items.append(None)

    def exec(self, _pos):
        pass


class _Item:
    def __init__(self, row: int, column: int, text: str = ""):
        self._row = row
        self._column = column
        self._text = text

    def row(self):
        return self._row

    def column(self):
        return self._column

    def text(self):
        return self._text


class _Viewport:
    def mapToGlobal(self, pos):
        return pos


class _Table:
    def itemAt(self, _pos):
        return _Item(0, 1)

    def item(self, row: int, column: int):
        if (row, column) == (0, 0):
            return _Item(0, 0, "alice")
        return None

    def viewport(self):
        return _Viewport()


class _Config:
    def __init__(self, model_type: str, key_def):
        self.model_type = model_type
        self.key_def = key_def

    def get_key(self, _key: str):
        return self.key_def

    def get_model_type(self, _key: str):
        return self.model_type


class _Host:
    def _displayed_column_keys(self, _group_name: str):
        return ["currency"]


def _open_menu(monkeypatch, model_type: str, key_def) -> list[str | None]:
    monkeypatch.setattr(QtWidgets, "QMenu", _Menu)
    monkeypatch.setattr(
        "lvjiang.core.profile.get_profile_config",
        lambda: _Config(model_type, key_def),
    )
    monkeypatch.setattr(
        cell_editing,
        "db_read_all",
        lambda _username: {model_type: {"currency": {"value": 20}}},
    )

    ProfileCellEditingMixin._on_cell_context_menu(
        _Host(), object(), "default", _Table()
    )
    return _Menu.last_items


def test_quick_menu_groups_uses_before_sources_with_three_separators(monkeypatch):
    key_def = StockKeyDef(
        key="currency",
        label="货币",
        steps=[
            StepDef(1, "限时活动"),
            StepDef(-1, "和鸣抽奖"),
            StepDef(0, "忽略"),
            StepDef(-10, "和鸣抽奖"),
            StepDef(1, "邮件赠送"),
        ],
    )

    assert _open_menu(monkeypatch, MODEL_STOCK, key_def) == [
        "和鸣抽奖(-1)",
        "和鸣抽奖(-10)",
        None,
        "限时活动(+1)",
        "邮件赠送(+1)",
        None,
        "增加...",
        "减少...",
        "覆写...",
        None,
        "查看历史记录",
    ]


def test_increment_only_menu_hides_uses_and_decrease(monkeypatch):
    key_def = QuotaKeyDef(
        key="currency",
        label="货币",
        increment_only=True,
        steps=[StepDef(-1, "消耗"), StepDef(1, "获得")],
    )

    assert _open_menu(monkeypatch, MODEL_QUOTA, key_def) == [
        "获得(+1)",
        None,
        "增加...",
        "覆写...",
        None,
        "查看历史记录",
    ]
