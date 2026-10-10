"""用户总览的合计行：默认隐藏、按分组保存、备注列不参与求和。

合计行只是在表尾多渲染一行，但有两条容易回退的契约：默认必须隐藏（否则
升级后每张总览表都会凭空多一行），以及备注这类自由文本列不能被当成数字相加。
"""
import pytest

from lvjiang.core.config import InterfaceStore
from lvjiang.core.profile import store
from lvjiang.core.profile.models import (
    MODEL_NOTE,
    MODEL_QUOTA,
    MODEL_STOCK,
    NoteKeyDef,
    QuotaKeyDef,
    StockKeyDef,
)
from lvjiang.core.profile.schema import ProfileSchema
from lvjiang.ui.profile import cell_formatting
from lvjiang.ui.profile.tab import ProfileTab


@pytest.fixture
def interface(tmp_path, monkeypatch):
    """隔离的 interface.json；store 的读写全部指向它。"""
    session = InterfaceStore(tmp_path / "interface.json")
    monkeypatch.setattr(store, "get_interface_store", lambda: session)
    return session


# ─── 开关的持久化 ──────────────────────────────────────────


def test_totals_defaults_to_hidden(interface):
    """从未记录过时必须隐藏，否则升级后表格会凭空多一行。"""
    assert store.get_overview_totals("甲组") is False


def test_totals_are_saved_per_group(interface):
    """开关按分组存放，打开一个分组不影响另一个。"""
    store.set_overview_totals("甲组", True)
    assert store.get_overview_totals("甲组") is True
    assert store.get_overview_totals("乙组") is False


def test_totals_survive_reload(interface, tmp_path, monkeypatch):
    """落盘后重新加载依然生效。"""
    store.set_overview_totals("甲组", True)
    reloaded = InterfaceStore(tmp_path / "interface.json")
    monkeypatch.setattr(store, "get_interface_store", lambda: reloaded)
    assert store.get_overview_totals("甲组") is True


def test_toggle_keeps_columns(interface):
    """开关只写自己那一项，不能顺带改动列配置。"""
    store.create_overview_group("甲组")
    store.insert_overview_column("甲组", 0, "a")
    store.set_overview_totals("甲组", True)
    assert store.get_groups()["甲组"]["columns"] == ["a"]


# ─── 求和规则 ──────────────────────────────────────────────


def test_note_column_is_left_empty():
    """备注是自由文本，合计行必须留空而不是填 0。"""
    kd = NoteKeyDef(key="n", label="备注")
    users = {"甲": {"note": {"n": {"value_text": "已完成"}}}}
    assert cell_formatting.sum_profile_column(kd, MODEL_NOTE, users) == ""


def test_unknown_model_is_left_empty():
    """模型类型认不出时不猜，同样留空。"""
    kd = StockKeyDef(key="a", label="资源甲")
    assert cell_formatting.sum_profile_column(kd, "", {"甲": {}}) == ""


def test_numeric_columns_are_summed():
    kd = StockKeyDef(key="a", label="资源甲")
    users = {
        "甲": {"stock": {"a": {"value": 3}}},
        "乙": {"stock": {"a": {"value": 4}}},
        "丙": {},  # 没有该 key，按不参与累加处理
    }
    assert cell_formatting.sum_profile_column(kd, MODEL_STOCK, users) == "7"


def test_empty_values_do_not_break_sum():
    kd = QuotaKeyDef(key="q", label="每日")
    users = {
        "甲": {"quota": {"q": {"value": None}}},
        "乙": {"quota": {"q": {"value": 2}}},
    }
    assert cell_formatting.sum_profile_column(kd, MODEL_QUOTA, users) == "2"


# ─── 表格渲染 ──────────────────────────────────────────────


USERS = {
    "甲": {"stock": {"a": {"value": 3}}, "note": {"n": {"value_text": "已完成"}}},
    "乙": {"stock": {"a": {"value": 4}}},
}


def _tab(monkeypatch, users=USERS) -> ProfileTab:
    """最小 ProfileTab：不连引擎也不读 DB，只用假 schema 与假用户数据。"""
    schema = ProfileSchema(keys_by_model={
        "stock": [StockKeyDef(key="a", label="资源甲")],
        "note": [NoteKeyDef(key="n", label="备注")],
    })
    monkeypatch.setattr("lvjiang.core.profile.get_profile_config", lambda: schema)
    monkeypatch.setattr(ProfileTab, "_connect_profile_engine", lambda self: None)
    monkeypatch.setattr(ProfileTab, "_connect_profile_script_runner", lambda self: None)
    monkeypatch.setattr(ProfileTab, "_load_all_users", lambda self: dict(users))
    return ProfileTab(None)


@pytest.fixture
def group_table(interface):
    interface.set_node("profile", {
        "overview_groups": {"甲组": {"columns": ["a", "n"]}},
        "overview_active_group": "甲组",
    })


def test_totals_row_is_appended_last(qtbot, interface, monkeypatch, group_table):
    """打开开关后表尾多一行，首格是「总和」，数值列求和、备注列留空。"""
    store.set_overview_totals("甲组", True)
    tab = _tab(monkeypatch)
    qtbot.addWidget(tab)
    try:
        table = tab._tables["甲组"]
        last = table.rowCount() - 1
        assert table.rowCount() == len(USERS) + 1
        assert table.item(last, 0).text() == "总和"
        assert table.item(last, 1).text() == "7"    # stock:a
        assert table.item(last, 2).text() == ""     # note:n
        assert table.totals_row == last
    finally:
        tab.close()


def test_totals_row_hidden_by_default(qtbot, interface, monkeypatch, group_table):
    """没打开开关时不渲染合计行，行号也要复位。"""
    tab = _tab(monkeypatch)
    qtbot.addWidget(tab)
    try:
        table = tab._tables["甲组"]
        assert table.rowCount() == len(USERS)
        assert table.totals_row == -1
    finally:
        tab.close()


def test_totals_row_is_not_treated_as_a_user(qtbot, interface, monkeypatch, group_table):
    """点合计行的用户名列不能把它选中：后续按行取用户名的入口都靠这个判定。"""
    store.set_overview_totals("甲组", True)
    tab = _tab(monkeypatch)
    qtbot.addWidget(tab)
    try:
        table = tab._tables["甲组"]
        assert table.is_totals_row(table.totals_row) is True
        table._select_username_row(table.totals_row, 0)
        assert table.selectedItems() == []
        table._select_username_row(0, 0)
        assert table.item(0, 0).isSelected()
    finally:
        tab.close()
