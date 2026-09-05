"""用户总览重建时的字体继承回归测试。"""

from types import SimpleNamespace

import lvjiang.core.profile as profile_core
import lvjiang.ui.profile.tab as profile_tab
from lvjiang.ui.profile.tab import ProfileTab


def test_rebuild_after_model_edit_preserves_overview_font(qtbot, monkeypatch):
    monkeypatch.setattr(profile_tab, "get_groups", lambda: {"默认": {"columns": []}})
    monkeypatch.setattr(profile_tab, "get_active_group", lambda: "默认")
    monkeypatch.setattr(profile_tab, "set_active_group", lambda _name: None)
    monkeypatch.setattr(
        profile_core,
        "get_profile_config",
        lambda: SimpleNamespace(get_key=lambda _key: None),
    )
    monkeypatch.setattr(ProfileTab, "_connect_profile_engine", lambda _self: None)

    host = SimpleNamespace(
        user_manager=SimpleNamespace(list_users=lambda: []),
    )
    tab = ProfileTab(host)
    qtbot.addWidget(tab)
    tab.apply_content_font_size(17)

    tab._build_groups()

    assert tab._tables["默认"].font().pointSize() == 17
    assert tab._tables["默认"].horizontalHeader().font().pointSize() == 17
