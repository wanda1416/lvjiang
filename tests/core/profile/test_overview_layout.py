"""总览可见结构（profile.json）：旧数据迁移、读写语义与列宽折算。

保护三条真实回归：

* 升级后用户手工排好的分组 / 列顺序 / 列宽必须原样搬进 ``profile.json``，
  失效列（定义里已删的 key）不能顺手清理，活跃分组要落到界面状态；
* 迁移只做一次，且旧实例没有任何结构时不得凭空创建文件；
* 列宽跨机器使用时按度量环境折算并 clamp，损坏文件不能被静默重建。
"""

import json

import pytest

from lvjiang import constants
from lvjiang.core.config import InterfaceStore
from lvjiang.core.profile import layout, store

LEGACY_PROFILE = {
    "overview_groups": {
        "甲组": {"columns": ["stock:a", "stock:gone"], "show_totals": True},
        "乙组": {"columns": ["quota:b"]},
    },
    "overview_group_order": ["乙组", "甲组"],
    "overview_active_group": "乙组",
}
LEGACY_WIDTHS = {"甲组": [90, 120, 60], "乙组": [80, 100]}


@pytest.fixture
def legacy_session(monkeypatch):
    """旧版本遗留的 interface.json：分组与列宽都在里面。"""
    session = InterfaceStore(constants.SESSION_CONFIG_DIR / "interface.json")
    monkeypatch.setattr(layout, "get_interface_store", lambda: session)
    monkeypatch.setattr(store, "get_interface_store", lambda: session)
    session.set_node("profile", dict(LEGACY_PROFILE))
    session.update_node(
        "ui_state", {"profile_overview": dict(LEGACY_WIDTHS), "main_page": {"keep": 1}})
    session.set_node("alert_history", {"k": "t"})
    return session


def test_migrates_legacy_nodes_into_profile_json(legacy_session):
    assert not layout.layout_path().exists()
    assert layout.ensure_migrated() is True

    target = layout.layout_path()
    assert target.exists()
    doc = layout.load()
    # 顺序沿用旧 order；两个分组都在。
    assert list(doc.overview.ordered()) == ["乙组", "甲组"]
    first = doc.overview.groups["甲组"]
    assert first.columns == ["stock:a", "stock:gone"]  # 失效列保留，不静默清理
    assert first.show_totals is True
    assert first.name_column_width == 90
    # 旧的按下标数组按列 key 逐项映射。
    assert first.column_widths == {"stock:a": 120, "stock:gone": 60}
    assert doc.overview.groups["乙组"].column_widths == {"quota:b": 100}
    # 活跃分组是本机视图状态：搬进 ui_state，不进结构文件。
    assert "overview_active_group" not in target.read_text(encoding="utf-8")
    ui_state = legacy_session.get_node("ui_state")
    assert ui_state["profile_active_group"] == "乙组"
    assert "profile_overview" not in ui_state
    assert ui_state["main_page"] == {"keep": 1}
    # 旧节点清理干净，不留下第二个真相。
    assert legacy_session.get_node("profile") in (None, {})
    assert legacy_session.get_node("alert_history") == {"k": "t"}
    # 迁移前留了可人工回退的备份。
    backups = list(target.parent.glob("_backup-*/profile-layout-before.json"))
    assert backups
    assert json.loads(backups[0].read_text(encoding="utf-8"))["overview_groups"]


def test_second_call_does_not_migrate_again(legacy_session):
    assert layout.ensure_migrated() is True
    payload = layout.layout_path().read_bytes()
    assert layout.ensure_migrated() is False
    assert layout.layout_path().read_bytes() == payload


def test_no_legacy_data_does_not_create_file():
    assert layout.ensure_migrated() is False
    assert not layout.layout_path().exists()
    assert store.get_groups() == {}
    # 用户真正编辑结构之后才落盘。
    store.create_overview_group("新组")
    assert layout.layout_path().exists()
    assert list(store.get_groups()) == ["新组"]


def test_widths_are_stored_per_key_and_shared_name_width():
    store.create_overview_group("甲组")
    store.create_overview_group("乙组")
    store.insert_overview_columns("甲组", None, ["a", "b"])
    store.set_group_widths("甲组", 100, {"a": 120, "b": 130})
    assert store.get_group_widths("甲组") == (100, {"a": 120, "b": 130})
    # 用户名列宽只存一份，改一个分组即对所有分组生效。
    assert store.get_group_widths("乙组")[0] == 100
    # 删除列时顺手清掉它的宽度记录。
    store.remove_overview_column("甲组", "b")
    assert store.get_group_widths("甲组") == (100, {"a": 120})


def test_rename_group_keeps_columns_and_widths():
    store.create_overview_group("甲组")
    store.insert_overview_column("甲组", 0, "a")
    store.set_group_widths("甲组", 90, {"a": 120})
    store.rename_overview_group("甲组", "乙组")
    assert store.get_groups()["乙组"]["columns"] == ["a"]
    assert store.get_group_widths("乙组") == (90, {"a": 120})


def test_replace_column_carries_width_to_new_key():
    store.create_overview_group("甲组")
    store.insert_overview_column("甲组", 0, "a")
    store.set_group_widths("甲组", 90, {"a": 120})
    store.replace_overview_column("甲组", "a", "b")
    assert store.get_groups()["甲组"]["columns"] == ["b"]
    assert store.get_group_widths("甲组") == (90, {"b": 120})


def test_scaled_width_scales_and_clamps():
    stored = layout.LayoutMetric(ui_scale=1.0, font_px=10)
    current = layout.LayoutMetric(ui_scale=1.0, font_px=20)
    assert layout.scaled_width(100, stored=stored, current=current, minimum=10) == 200
    assert layout.scaled_width(
        100, stored=stored, current=current, minimum=10, maximum=150) == 150
    # 折算后小于表头实测宽度时抬到下限（列标题永不被截断）。
    assert layout.scaled_width(40, stored=stored, current=current, minimum=100) == 100
    # 没有记录时给默认宽度。
    assert layout.scaled_width(
        0, stored=stored, current=current, minimum=10) == layout.DEFAULT_COLUMN_WIDTH
    # 文件里没有度量环境（手写或很旧的文件）时按原值使用。
    assert layout.scaled_width(
        100, stored=layout.LayoutMetric(), current=current, minimum=10) == 100


def test_broken_file_keeps_defaults_and_is_not_rebuilt():
    target = layout.layout_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{not json", encoding="utf-8")

    doc = layout.load()

    assert doc.overview.groups == {}
    assert layout.last_read_error
    # 损坏文件保留原样，等用户自己决定怎么处理。
    assert target.read_text(encoding="utf-8") == "{not json"


def test_parse_bytes_rejects_invalid_and_future_documents():
    """下载前置校验：坏内容与高版本都不能进到覆盖阶段。"""
    with pytest.raises(ValueError, match="JSON"):
        layout.parse_bytes(b"nope")
    with pytest.raises(ValueError, match="版本"):
        layout.parse_bytes(b'{"version": 99}')
    assert layout.parse_bytes(b'{"version": 1, "overview": {"groups": {}}}').version == 1


def test_backup_current_copies_existing_file():
    assert layout.backup_current() is None
    layout.save(layout.ProfileLayout(overview=layout.OverviewLayout(
        groups={"g": layout.GroupLayout(columns=["a"])})))
    backup = layout.backup_current(reason="test")
    assert backup is not None and backup.name == "profile-layout-before.json"
    assert json.loads(backup.read_text(encoding="utf-8"))["overview"]["groups"]["g"]["columns"] == ["a"]
