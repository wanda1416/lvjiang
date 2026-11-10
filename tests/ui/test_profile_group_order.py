"""分组拖动必须保存顺序，且不能切换当前分组或覆盖列配置与列宽。"""

import pytest
from PyQt6.QtWidgets import QTableWidget

from lvjiang import constants
from lvjiang.core.config import InterfaceStore
from lvjiang.core.profile import layout, store
from lvjiang.ui.profile.tab import ProfileTab


@pytest.fixture
def overview_env(monkeypatch):
    """隔离的 interface.json + profile.json，并让 store/layout 都指向它们。

    两者都随 ``constants.SESSION_CONFIG_DIR`` 落在本用例的临时目录。
    """
    session = InterfaceStore(constants.SESSION_CONFIG_DIR / "interface.json")
    monkeypatch.setattr(store, "get_interface_store", lambda: session)
    monkeypatch.setattr(layout, "get_interface_store", lambda: session)
    layout.save(layout.ProfileLayout(overview=layout.OverviewLayout(
        group_order=["甲组", "乙组"],
        groups={
            "甲组": layout.GroupLayout(
                columns=["stock:a"], name_column_width=90, column_widths={"stock:a": 120},
            ),
            "乙组": layout.GroupLayout(columns=["quota:b"]),
        },
    )))
    # 活跃分组是本机视图状态，跟着界面状态走，不进结构文件。
    session.update_node("ui_state", {"profile_active_group": "乙组"})
    session.set_node("alert_history", {"test": "keep"})
    return session


def test_group_drag_preserves_selection_contents_and_saved_order(
    qapp, overview_env, monkeypatch,
):
    monkeypatch.setattr(ProfileTab, "_connect_profile_engine", lambda self: None)
    monkeypatch.setattr(ProfileTab, "_connect_profile_script_runner", lambda self: None)
    monkeypatch.setattr(
        ProfileTab, "_create_table_for_group", lambda self, name: QTableWidget(self))
    tab = ProfileTab(None)
    current = tab._tab_widget.currentWidget()
    assert tab._tab_widget.isMovable()
    tab._tab_widget.tabBar().moveTab(1, 0)
    assert tab._tab_widget.currentWidget() is current
    assert store.get_active_group() == "乙组"
    assert list(store.get_groups()) == ["乙组", "甲组"]
    assert store.get_groups() == {
        "乙组": {"columns": ["quota:b"], "show_totals": False},
        "甲组": {"columns": ["stock:a"], "show_totals": False},
    }
    # 重排分组不能顺手丢掉该分组的列宽记录。
    assert store.get_group_widths("甲组") == (90, {"stock:a": 120})
    assert overview_env.get_node("alert_history") == {"test": "keep"}

    reloaded = InterfaceStore(constants.SESSION_CONFIG_DIR / "interface.json")
    monkeypatch.setattr(store, "get_interface_store", lambda: reloaded)
    monkeypatch.setattr(layout, "get_interface_store", lambda: reloaded)
    tab._build_groups()
    assert [tab._tab_widget.tabText(i) for i in range(2)] == ["乙组", "甲组"]
    assert tab._get_current_group_name() == "乙组"
    tab.close()
