"""分组拖动必须保存顺序，且不能切换当前分组或覆盖列配置。"""

from PyQt6.QtWidgets import QTableWidget

from lvjiang.core.config import InterfaceStore
from lvjiang.core.profile import store
from lvjiang.ui.profile.tab import ProfileTab


def test_group_drag_preserves_selection_contents_and_saved_order(qapp, tmp_path, monkeypatch):
    session = InterfaceStore(tmp_path / "interface.json")
    monkeypatch.setattr(store, "get_interface_store", lambda: session)
    groups = {"甲组": {"columns": ["stock:a"]}, "乙组": {"columns": ["quota:b"]}}
    session.set_node("profile", {
        "overview_groups": groups, "overview_active_group": "乙组",
    })
    session.set_node("alert_history", {"test": "keep"})
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
    assert store.get_groups() == groups
    assert session.get_node("alert_history") == {"test": "keep"}
    reloaded = InterfaceStore(tmp_path / "interface.json")
    monkeypatch.setattr(store, "get_interface_store", lambda: reloaded)
    tab._build_groups()
    assert [tab._tab_widget.tabText(i) for i in range(2)] == ["乙组", "甲组"]
    assert tab._get_current_group_name() == "乙组"
    tab.close()
