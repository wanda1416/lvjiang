"""空白单元格输入 0 必须留下记录。

空白表示这个用户没有该 key 的 entry，但引擎读到的同样是 0。曾经「输入 0」会
被同值无操作直接丢掉：不写库、不刷新，用户无法表达「明确为零」，单元格下次
刷新又变回空白。
"""
from __future__ import annotations

import pytest
import yaml

from lvjiang import constants
from lvjiang.core.config import InterfaceStore
from lvjiang.core.profile import layout, repository, schema
from lvjiang.ui.profile.tab import ProfileTab


@pytest.fixture
def interface(monkeypatch):
    session = InterfaceStore(constants.SESSION_CONFIG_DIR / "interface.json")
    monkeypatch.setattr("lvjiang.core.profile.store.get_interface_store", lambda: session)
    monkeypatch.setattr(layout, "get_interface_store", lambda: session)
    return session


@pytest.fixture
def stock_group(interface):
    """一个只含 stock key ``a`` 的隔离分组，单元格初始为空白。"""
    schema._PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    schema._PROFILE_PATH.write_text(
        yaml.safe_dump(
            {"stock": [{"key": "a", "label": "资源甲"}]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    layout.save(layout.ProfileLayout(overview=layout.OverviewLayout(
        group_order=["甲组"],
        groups={"甲组": layout.GroupLayout(columns=["a"])},
    )))
    interface.update_node("ui_state", {"profile_active_group": "甲组"})


def _tab(monkeypatch) -> ProfileTab:
    monkeypatch.setattr(ProfileTab, "_connect_profile_engine", lambda self: None)
    monkeypatch.setattr(ProfileTab, "_connect_profile_script_runner", lambda self: None)
    monkeypatch.setattr(
        ProfileTab, "_load_all_users",
        lambda self: {"甲": repository.db_read_all("甲")},
    )
    return ProfileTab(None)


def test_typing_zero_into_blank_cell_creates_entry(
    qtbot, stock_group, monkeypatch,
):
    """从空白改为 0 要落库、写历史，并在刷新后显示 0 而不是重回空白。"""
    tab = _tab(monkeypatch)
    qtbot.addWidget(tab)
    try:
        table = tab._tables["甲组"]
        item = table.item(0, 1)
        assert item is not None and item.text() == ""

        item.setText("0")

        entry = repository.db_read_entry("甲", "stock", "a")
        assert entry.get("value") == 0
        assert entry.get("event_at")
        history = repository.db_get_history("甲", "stock", "a")
        assert [(row["op_type"], row["new_value"]) for row in history] == [
            ("set", 0.0),
        ]
        assert table.item(0, 1).text() == "0"
    finally:
        tab.close()
