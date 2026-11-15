"""备注编辑走 CAS：别人改过就报冲突，不静默覆盖。

回归的是「两台设备 / 两个窗口同时编辑同一格备注」：过去后提交者直接覆盖，
谁都没被提示；现在提交时按渲染那一刻的条目做 CAS，冲突走既有失败分支提示。
"""
from __future__ import annotations

import pytest
import yaml

from lvjiang import constants
from lvjiang.core.config import InterfaceStore
from lvjiang.core.profile import layout, repository, schema
from lvjiang.ui.profile import cell_editing
from lvjiang.ui.profile.tab import ProfileTab


@pytest.fixture
def interface(monkeypatch):
    session = InterfaceStore(constants.SESSION_CONFIG_DIR / "interface.json")
    monkeypatch.setattr("lvjiang.core.profile.store.get_interface_store", lambda: session)
    monkeypatch.setattr(layout, "get_interface_store", lambda: session)
    return session


@pytest.fixture
def note_group(interface):
    """一个只含 note key ``n`` 的隔离分组，库里已有「旧文本」。"""
    schema._PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    schema._PROFILE_PATH.write_text(
        yaml.safe_dump(
            {"note": [{"key": "n", "label": "备注"}]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    layout.save(layout.ProfileLayout(overview=layout.OverviewLayout(
        group_order=["甲组"],
        groups={"甲组": layout.GroupLayout(columns=["n"])},
    )))
    repository.db_upsert("甲", "note", "n", 0, op_type="set", value_text="旧文本")
    interface.update_node("ui_state", {"profile_active_group": "甲组"})


def _tab(monkeypatch) -> ProfileTab:
    monkeypatch.setattr(ProfileTab, "_connect_profile_engine", lambda self: None)
    monkeypatch.setattr(ProfileTab, "_connect_profile_script_runner", lambda self: None)
    monkeypatch.setattr(
        ProfileTab, "_load_all_users",
        lambda self: {"甲": repository.db_read_index("甲")},
    )
    return ProfileTab(None)


def test_note_edit_rejected_when_changed_elsewhere(
    qtbot, note_group, monkeypatch,
):
    tab = _tab(monkeypatch)
    qtbot.addWidget(tab)
    warnings: list[str] = []
    monkeypatch.setattr(
        cell_editing.QMessageBox, "warning",
        lambda *args, **kwargs: warnings.append(str(args[1]) if len(args) > 1 else ""))
    try:
        table = tab._tables["甲组"]
        item = table.item(0, 1)
        assert item is not None and item.text() == "旧文本"

        # 另一个窗口先改了同一格
        repository.db_upsert("甲", "note", "n", 0, op_type="set", value_text="别人改的")

        item.setText("我改的")

        assert repository.db_read_entry("甲", "note", "n").value_text == "别人改的"
        assert warnings, "冲突必须提示用户，而不是静默覆盖"
    finally:
        tab.close()


def test_note_edit_writes_when_snapshot_still_current(
    qtbot, note_group, monkeypatch,
):
    tab = _tab(monkeypatch)
    qtbot.addWidget(tab)
    warnings: list[str] = []
    monkeypatch.setattr(
        cell_editing.QMessageBox, "warning",
        lambda *args, **kwargs: warnings.append(str(args)))
    try:
        table = tab._tables["甲组"]
        item = table.item(0, 1)
        assert item is not None

        item.setText("新文本")

        assert repository.db_read_entry("甲", "note", "n").value_text == "新文本"
        assert not warnings
        history = repository.db_get_history("甲", "note", "n")
        assert history[0].value_text == "新文本" and history[0].old_value_text == "旧文本"
    finally:
        tab.close()
