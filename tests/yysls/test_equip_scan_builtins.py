"""装备全量扫描的窗口级背包游标测试。"""

from pathlib import Path

import pytest

# 导入以注册内置函数
import lvjiang.apps.yysls.workflows.builtins.equip_funcs as equip_funcs
from lvjiang.workflows.builtins import get_function
from lvjiang.workflows.grammar.parser.api import parse_file


def _fn(name):
    fn = get_function(name)
    assert fn is not None, f"内置函数 {name} 未注册"
    return fn


class MockEngine:
    def __init__(self):
        self.context = {}


@pytest.fixture
def engine():
    return MockEngine()


def _init(engine):
    return _fn("bag_cursor_init")(engine)


def _visit_window(engine, fingerprints):
    return [_fn("bag_cursor_visit")(engine, fp) for fp in fingerprints]


class TestBagCursorVisit:
    def test_init_creates_window_state(self, engine):
        assert _init(engine) == ""
        assert engine.context["_bag_cursor"] == {
            "seen": set(), "window": [], "new_count": 0,
            "idle": 0, "rounds": 0,
        }

    def test_new_row_and_seen_anchor_skip(self, engine):
        _init(engine)
        assert _fn("bag_cursor_visit")(engine, "fp1") == "new"
        assert _fn("bag_cursor_finish_window")(engine, 3, 3) == "scroll"
        assert _fn("bag_cursor_visit")(engine, "fp1") == "skip"
        assert _fn("bag_cursor_visit")(engine, "fp2") == "new"

    def test_empty_fingerprint_ends(self, engine):
        _init(engine)
        assert _fn("bag_cursor_visit")(engine, "") == "end"
        assert _fn("bag_cursor_visit")(engine, None) == "end"

    def test_not_initialized_ends(self, engine):
        assert _fn("bag_cursor_visit")(engine, "fp1") == "end"

    def test_skip_does_not_increment_new_count(self, engine):
        _init(engine)
        _fn("bag_cursor_visit")(engine, "fp1")
        _fn("bag_cursor_finish_window")(engine, 3, 3)
        assert _fn("bag_cursor_visit")(engine, "fp1") == "skip"
        assert engine.context["_bag_cursor"]["new_count"] == 0

    def test_reinit_clears_previous_slot_state(self, engine):
        _init(engine)
        _fn("bag_cursor_visit")(engine, "fp1")
        _fn("bag_cursor_finish_window")(engine, 3, 3)
        _init(engine)
        assert engine.context["_bag_cursor"]["seen"] == set()
        assert engine.context["_bag_cursor"]["rounds"] == 0
        assert _fn("bag_cursor_visit")(engine, "fp1") == "new"


class TestBagCursorFinishWindow:
    def test_window_with_new_items_scrolls_and_resets(self, engine):
        _init(engine)
        assert _visit_window(engine, ["a", "b", "c"]) == [
            "new", "new", "new"]
        assert _fn("bag_cursor_finish_window")(engine, 3, 3) == "scroll"
        cursor = engine.context["_bag_cursor"]
        assert cursor["window"] == []
        assert cursor["new_count"] == 0
        assert cursor["idle"] == 0
        assert cursor["rounds"] == 1

    def test_full_window_two_idle_rounds_end(self, engine):
        _init(engine)
        _visit_window(engine, ["a", "b", "c"])
        assert _fn("bag_cursor_finish_window")(engine, 3, 3) == "scroll"

        assert _visit_window(engine, ["a", "b", "c"]) == [
            "skip", "skip", "skip"]
        assert _fn("bag_cursor_finish_window")(engine, 3, 3) == "scroll"
        assert engine.context["_bag_cursor"]["idle"] == 1

        _visit_window(engine, ["a", "b", "c"])
        assert _fn("bag_cursor_finish_window")(engine, 3, 3) == "end"

    def test_partial_window_zero_new_ends_immediately(self, engine):
        _init(engine)
        _visit_window(engine, ["a", "b", "c"])
        assert _fn("bag_cursor_finish_window")(engine, 3, 3) == "scroll"
        _visit_window(engine, ["b", "c"])
        assert _fn("bag_cursor_finish_window")(engine, 2, 3) == "end"

    def test_new_item_resets_idle(self, engine):
        _init(engine)
        _visit_window(engine, ["a", "b", "c"])
        _fn("bag_cursor_finish_window")(engine, 3, 3)
        _visit_window(engine, ["a", "b", "c"])
        _fn("bag_cursor_finish_window")(engine, 3, 3)
        assert engine.context["_bag_cursor"]["idle"] == 1
        _visit_window(engine, ["b", "c", "d"])
        assert _fn("bag_cursor_finish_window")(engine, 3, 3) == "scroll"
        assert engine.context["_bag_cursor"]["idle"] == 0

    def test_empty_window_ends(self, engine):
        _init(engine)
        assert _fn("bag_cursor_finish_window")(engine, 3, 3) == "end"

    def test_not_initialized_ends(self, engine):
        assert _fn("bag_cursor_finish_window")(engine, 3, 3) == "end"

    def test_max_scroll_rounds_fuse(self, engine, monkeypatch):
        monkeypatch.setattr(equip_funcs, "_MAX_SCROLL_ROUNDS", 1)
        _init(engine)
        _visit_window(engine, ["a"])
        assert _fn("bag_cursor_finish_window")(engine, 3, 3) == "scroll"
        _visit_window(engine, ["b"])
        assert _fn("bag_cursor_finish_window")(engine, 3, 3) == "end"


def test_equip_scan_workflow_parses():
    root = Path(__file__).resolve().parents[2]
    program = parse_file(root / "config/system/workflows/equip_scan.wf")
    assert program is not None


def test_equip_scan_uses_window_protocol_and_correct_detail_scenes():
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/equip_scan.wf").read_text(
        encoding="utf-8")
    assert "bag_cursor_visit($fp)" in text
    assert "bag_cursor_finish_window($rows, $rows)" in text
    assert 'panel_rows("bag_equip_detail", "bag_grid")' in text
    assert 'call scan_slot_bag("ring", "ring", "weapon", $min_level)' in text
    assert ('call scan_slot_bag("pendant", "pendant", "weapon", '
            '$min_level)') in text
    assert "bag_cursor_next" not in text


def test_equip_scan_seen_row_skips_remaining_columns():
    """每行仅首列调用游标；第 2～末列只位于 new 分支内。"""
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/equip_scan.wf").read_text(
        encoding="utf-8")
    assert text.count("bag_cursor_visit($fp)") == 1
    new_branch = text.index('if $signal equals "new"')
    remaining_cols = text.index("eval $c = 2")
    assert remaining_cols > new_branch


def test_equip_scan_level_threshold_ends_current_slot_without_cast():
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/equip_scan.wf").read_text(
        encoding="utf-8")
    assert "int($equip.level)" not in text
    assert "$equip.level < $min_level" in text
    assert 'eval $signal = "level_end"' in text
    assert 'if $signal equals "end" or $signal equals "level_end"' in text


def test_proc_writes_shared_session_not_isolated_local_bag():
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/equip_scan.wf").read_text(
        encoding="utf-8")
    assert "if not session.bag_items" in text
    assert "eval session.bag_items = {}" in text
    assert "eval session.bag_items.$group = $items" in text
    assert "eval $bag.$group" not in text
    assert "eval session.bag_items = $bag" not in text


def test_each_slot_replaces_atomically_then_saves():
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/equip_scan.wf").read_text(
        encoding="utf-8")
    replace_at = text.index("eval session.bag_items.$group = $items")
    save_at = text.index("eval save()", replace_at)
    assert save_at > replace_at


def test_min_level_is_explicit_proc_parameter():
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/equip_scan.wf").read_text(
        encoding="utf-8")
    assert "def scan_slot_bag($slot, $group, $detail_kind, $min_level)" in text
    calls = [line.strip() for line in text.splitlines()
             if line.strip().startswith("call scan_slot_bag(")]
    assert len(calls) == 7
    assert all(line.endswith(", $min_level)") for line in calls)
