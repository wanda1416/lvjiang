"""总览新增列的字段选择器：类型 → 分组 → 定义 三级

key 多起来之后，类型下面直接铺一长串定义根本找不到。分组这一层来自定义本身的
``KeyDef.group``（定义对话框里能改），不另造一种归类方式。

只有默认分组时不套那一层——它只多一次点击，什么信息都不提供。
"""

import pytest
from PyQt6.QtWidgets import QWidget

from lvjiang.core.profile.models import (
    DEFAULT_KEY_GROUP,
    QuotaKeyDef,
    StockKeyDef,
)
from lvjiang.ui.profile.column_management import ProfileColumnMixin

pytestmark = pytest.mark.usefixtures('qapp')


class _Config:
    """只提供选择器用到的那两个查询"""

    def __init__(self, model_by_key: dict[str, str], keys=()):
        self._model_by_key = model_by_key
        self._by_key = {kd.key: kd for kd in keys}

    def get_model_type(self, key: str) -> str:
        return self._model_by_key.get(key, "")

    def get_key(self, key: str):
        return self._by_key.get(key)


class _Host(QWidget, ProfileColumnMixin):
    pass


def _menu_tree(button) -> dict:
    """建出菜单（不 exec——那会阻塞），把层级读成嵌套 dict"""
    def walk(m) -> dict:
        out: dict = {}
        for action in m.actions():
            sub = action.menu()
            out[action.text()] = walk(sub) if sub is not None else None
        return out

    return walk(button.build_key_menu())


def _picker(qtbot, keys, model_by_key):
    host = _Host()
    qtbot.addWidget(host)
    selected = [""]
    button = ProfileColumnMixin._create_key_picker(
        host, _Config(model_by_key, keys), keys, "", selected)
    qtbot.addWidget(button)
    return button, selected


def test_group_level_is_inserted_between_type_and_key(qtbot):
    keys = [
        QuotaKeyDef(key="a", label="甲", group="日常"),
        QuotaKeyDef(key="b", label="乙", group="周常"),
        QuotaKeyDef(key="c", label="丙", group="周常"),
    ]
    button, _ = _picker(qtbot, keys, dict.fromkeys("abc", "quota"))

    tree = _menu_tree(button)
    assert list(tree) == ["配额"]
    assert list(tree["配额"]) == ["日常", "周常"]       # 分组按定义出现顺序
    assert list(tree["配额"]["日常"]) == ["甲 (a)"]
    assert list(tree["配额"]["周常"]) == ["乙 (b)", "丙 (c)"]


def test_default_only_group_is_not_nested(qtbot):
    """只有默认分组时不套空壳：那层菜单只多一次点击。"""
    keys = [QuotaKeyDef(key="a", label="甲", group=DEFAULT_KEY_GROUP)]
    button, _ = _picker(qtbot, keys, {"a": "quota"})

    tree = _menu_tree(button)
    assert list(tree["配额"]) == ["甲 (a)"]


def test_each_model_type_groups_independently(qtbot):
    keys = [
        QuotaKeyDef(key="a", label="甲", group="日常"),
        StockKeyDef(key="b", label="乙", group="货币"),
    ]
    button, _ = _picker(
        qtbot, keys, {"a": "quota", "b": "stock"})

    tree = _menu_tree(button)
    assert list(tree["配额"]) == ["日常"]
    assert list(tree["库存"]) == ["货币"]


def test_selecting_a_key_writes_through(qtbot):
    keys = [QuotaKeyDef(key="a", label="甲", group="日常")]
    button, selected = _picker(qtbot, keys, {"a": "quota"})

    menu = button.build_key_menu()
    group_menu = menu.actions()[0].menu()
    assert group_menu is not None
    key_menu = group_menu.actions()[0].menu()
    assert key_menu is not None
    key_menu.actions()[0].trigger()

    assert selected[0] == "a"
    assert "甲" in button.text()
