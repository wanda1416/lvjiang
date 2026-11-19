"""袅袅之音记录：文件形状、钳位、空写与宽容读取。

记录文件是用户数据，两条底线必须守住：值不会越过清单枚数，写失败不能把
已有记录改坏；清单删掉条目不等于删用户记录。
"""
from __future__ import annotations

import json
from pathlib import Path

from lvjiang.apps.yysls.core.linger.models import (
    ItemSlot,
    LingerItem,
    LingerState,
    build_catalog,
    region_from_doc,
)
from lvjiang.apps.yysls.core.linger.store import LingerStore


def test_set_done_writes_document_shape(tmp_path: Path) -> None:
    store = LingerStore("蔡元君", tmp_path)

    state = store.set_done("hexi/ywh", 9, 14)

    assert state.done("hexi/ywh") == 9
    assert state.revision == 1
    payload = json.loads((tmp_path / "蔡元君.linger.json").read_text(encoding="utf-8"))
    assert payload["version"] == 3
    assert payload["revision"] == 1
    assert payload["items"]["hexi/ywh"]["done"] == 9
    assert payload["items"]["hexi/ywh"]["at"]
    # 读写往返后仍然是同一个值
    assert store.load().done("hexi/ywh") == 9


def test_set_done_clamps_and_skips_noop_writes(tmp_path: Path) -> None:
    store = LingerStore("alice", tmp_path)

    assert store.set_done("hexi/ywh", 99, 14).done("hexi/ywh") == 14
    assert store.set_done("hexi/ywh", 14, 14).revision == 1  # 同值不写盘
    assert store.set_done("hexi/ywh", -3, 14).revision == 2
    assert store.load().done("hexi/ywh") == 0
    # 归零的条目不留在文件里，避免记录被一堆 0 撑大
    payload = json.loads((tmp_path / "alice.linger.json").read_text(encoding="utf-8"))
    assert payload["items"] == {}


def test_load_tolerates_dirty_entries(tmp_path: Path) -> None:
    path = tmp_path / "bob.linger.json"
    path.write_text(json.dumps({
        "version": 1,
        "revision": "7",
        "items": {
            "hexi/ywh": {"done": "3", "at": "2026-11-19T04:00:00+00:00"},
            "no-slash": {"done": 1},
            "hexi/bad": "oops",
            "hexi/zero": {"done": 0},
        },
    }, ensure_ascii=False), encoding="utf-8")

    state = LingerStore("bob", tmp_path).load()

    assert state.revision == 7
    assert state.done("hexi/ywh") == 3
    assert "no-slash" not in state.items
    assert "hexi/bad" not in state.items
    assert state.done("hexi/zero") == 0


def test_missing_and_broken_files_read_as_empty(tmp_path: Path) -> None:
    store = LingerStore("nobody", tmp_path)
    assert store.load().items == {}

    (tmp_path / "nobody.linger.json").write_text("{not json", encoding="utf-8")
    assert store.load().items == {}


def test_state_keeps_keys_missing_from_catalog() -> None:
    """清单删掉条目后，用户记录原样保留（页面标成清单已移除，不自动清）。"""
    state = LingerState.from_dict({
        "version": 1,
        "revision": 2,
        "items": {"gone/old": {"done": 1}},
    })

    assert state.done("gone/old") == 1
    assert state.to_dict()["items"] == {"gone/old": {"done": 1, "at": ""}}


def test_document_channel_syncs_record_as_linger_kind(tmp_path: Path) -> None:
    """登记后的 kind 必须真的被文档通道认出来：云端键是 (用户名, linger)。"""
    from lvjiang.apps.yysls.sync import documents  # noqa: F401  导入即注册 kind
    from lvjiang.core.sync.engine import iter_local_documents

    LingerStore("alice", tmp_path).set_done("hexi/ywh", 1, 14)

    found = [
        document for document in iter_local_documents(
            users_dir=tmp_path, session_dir=tmp_path / "session")
        if document.kind == "linger"
    ]

    assert [(document.label, document.path.name) for document in found] == [
        ("alice", "alice.linger.json"),
    ]


def _named_item(count: int = 3) -> LingerItem:
    return LingerItem(id="ywh", label="以武会友", count=count, source="以武会友",
                      kind="roster")


def test_named_item_counts_only_checked_slots(tmp_path: Path) -> None:
    """逐位录入的已完成枚数由勾上的子项推出，与手填的 done 无关。"""
    store = LingerStore("alice", tmp_path)
    item = _named_item()
    slots = [
        ItemSlot(done=True, name="张三", at="2026-11-19T04:00:00+00:00"),
        ItemSlot(done=False, name="李四"),
        ItemSlot(done=True, name=""),
    ]

    state = store.set_slots("hexi/ywh", slots, item.count)

    assert state.resolve_done("hexi/ywh", item) == 2
    payload = json.loads((tmp_path / "alice.linger.json").read_text(encoding="utf-8"))
    assert payload["version"] == 3
    assert payload["items"]["hexi/ywh"]["slots"] == [
        {"done": True, "name": "张三", "at": "2026-11-19T04:00:00+00:00"},
        {"name": "李四"},
        {"done": True},
    ]


def test_slots_trim_trailing_blanks_and_drop_empty_item(tmp_path: Path) -> None:
    store = LingerStore("alice", tmp_path)
    item = _named_item()

    state = store.set_slots("hexi/ywh", [ItemSlot(done=True), ItemSlot(), ItemSlot()],
                            item.count)
    assert len(state.slots("hexi/ywh")) == 1

    state = store.set_slots("hexi/ywh", [ItemSlot(), ItemSlot(), ItemSlot()], item.count)
    assert state.slots("hexi/ywh") == []
    assert state.items == {}


def test_slots_are_clamped_to_item_count(tmp_path: Path) -> None:
    store = LingerStore("alice", tmp_path)
    item = _named_item(count=2)

    state = store.set_slots(
        "hexi/ywh", [ItemSlot(done=True), ItemSlot(done=True), ItemSlot(done=True)],
        item.count)

    assert len(state.slots("hexi/ywh")) == 2
    assert state.resolve_done("hexi/ywh", item) == 2


def test_slot_view_pads_to_count_and_keeps_order() -> None:
    state = LingerState.from_dict({
        "version": 2,
        "items": {"hexi/ywh": {"done": 1, "slots": [{"done": True, "name": "甲"}]}},
    })

    view = state.slot_view("hexi/ywh", 3)

    assert [slot.name for slot in view] == ["甲", "", ""]
    assert [slot.done for slot in view] == [True, False, False]


def test_booked_survives_later_done_changes(tmp_path: Path) -> None:
    """改已得数量不能抹掉「已入账多少」——否则下次会重复记账。"""
    store = LingerStore("alice", tmp_path)
    store.set_done("hexi/ywh", 5, 14)
    state = store.set_booked("hexi/ywh", 3, 5)
    assert state.booked("hexi/ywh") == 3

    state = store.set_done("hexi/ywh", 7, 14)

    assert state.done("hexi/ywh") == 7
    assert state.booked("hexi/ywh") == 3
    payload = json.loads((tmp_path / "alice.linger.json").read_text(encoding="utf-8"))
    assert payload["items"]["hexi/ywh"]["booked"] == 3


def test_booked_cannot_exceed_done(tmp_path: Path) -> None:
    store = LingerStore("alice", tmp_path)
    store.set_done("qinghe/map_explore", 1, 2)

    assert store.set_booked("qinghe/map_explore", 9, 1).booked("qinghe/map_explore") == 1
    # 已得降到入账以下时，入账值跟着收紧，不会出现 booked > done
    state = store.set_done("qinghe/map_explore", 0, 2)
    assert state.booked("qinghe/map_explore") == 0


def test_booked_and_pending_totals(tmp_path: Path) -> None:
    catalog = build_catalog([
        region_from_doc(
            {"id": "r", "label": "R",
             "items": [{"id": "a", "label": "A", "count": 3},
                       {"id": "b", "label": "B", "count": 2}]},
            source_name="r.yaml"),
    ])
    store = LingerStore("alice", tmp_path)
    store.set_done("r/a", 3, 3)
    store.set_done("r/b", 1, 2)
    state = store.set_booked("r/a", 2, 3)

    assert state.recorded_total(catalog) == (4, 5)
    assert state.booked_total(catalog) == 2
    assert state.pending_total(catalog) == 2

