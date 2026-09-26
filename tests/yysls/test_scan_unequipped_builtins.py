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


def test_scan_unequipped_workflow_parses():
    root = Path(__file__).resolve().parents[2]
    program = parse_file(root / "config/system/workflows/scan_unequipped.wf")
    assert program is not None


def test_scan_unequipped_uses_window_protocol_and_correct_detail_scenes():
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/scan_unequipped.wf").read_text(
        encoding="utf-8")
    assert "bag_cursor_visit($fp)" in text
    assert "bag_cursor_finish_window($rows, $rows)" in text
    assert 'panel_rows("bag_equip_detail", "bag_grid")' in text
    assert ('call scan_slot_bag("ring", "ring", "weapon", $min_level, '
            '$min_affix_count)') in text
    assert ('call scan_slot_bag("pendant", "pendant", "weapon", '
            '$min_level, $min_affix_count)') in text
    assert "bag_cursor_next" not in text


def test_scan_unequipped_closes_desktop_detail_before_first_column_only():
    """桌面详情只在首列点击前及部位结束时关闭，连续列扫描不关闭。"""
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/scan_unequipped.wf").read_text(
        encoding="utf-8")
    assert text.count('press "ESC" after wait @page_refresh') == 2

    row_loop = text.index("loop while $r <= $rows")
    first_col_click = text.index(
        "click [bag_equip_detail].[bag_grid][$r][1]", row_loop)
    close_before_first_col = text.index(
        'press "ESC" after wait @page_refresh', row_loop)
    assert close_before_first_col < first_col_click

    remaining_cols = text.index("eval $c = 2", first_col_click)
    after_remaining_cols = text.index(
        'if $signal equals "end" or $signal equals "level_end"',
        remaining_cols,
    )
    assert 'press "ESC"' not in text[remaining_cols:after_remaining_cols]

    final_close = text.rindex('press "ESC" after wait @page_refresh')
    assert final_close > after_remaining_cols


def test_scan_unequipped_tracks_detail_state_from_raw_scan_content():
    """空槽关闭详情；仅类型 OCR 失败但仍有详情内容时保持打开状态。"""
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/scan_unequipped.wf").read_text(
        encoding="utf-8")

    assert text.count("eval $detail_open = $cell.detail_open") == 2
    assert "eval $detail_open = $slot_cell.detail_open" in text
    assert (
        "if $raw.equip_type or $raw.equip_level or $raw.base_attr "
        "or $raw.equip_detail"
    ) in text

    first_col_scan = text.index("call $cell = scan_cell($detail_kind)")
    state_update = text.index(
        "eval $detail_open = $cell.detail_open", first_col_scan)
    empty_guard = text.index("if not $equip.type", first_col_scan)
    assert first_col_scan < state_update < empty_guard

    remaining_cols = text.index("eval $c = 2", empty_guard)
    remaining_scan = text.index(
        "call $cell = scan_cell($detail_kind)", remaining_cols)
    remaining_state_update = text.index(
        "eval $detail_open = $cell.detail_open", remaining_scan)
    remaining_empty_guard = text.index("if not $equip.type", remaining_scan)
    assert remaining_scan < remaining_state_update < remaining_empty_guard


def test_scan_unequipped_seen_row_skips_remaining_columns():
    """每行仅首列调用游标；第 2～末列只位于 new 分支内。"""
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/scan_unequipped.wf").read_text(
        encoding="utf-8")
    assert text.count("bag_cursor_visit($fp)") == 1
    new_branch = text.index('if $signal equals "new"')
    remaining_cols = text.index("eval $c = 2")
    assert remaining_cols > new_branch


def test_scan_unequipped_level_threshold_ends_current_slot_without_cast():
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/scan_unequipped.wf").read_text(
        encoding="utf-8")
    assert "int($equip.level)" not in text
    assert "$equip.level < $min_level" in text
    assert 'eval $signal = "level_end"' in text
    assert 'if $signal equals "end" or $signal equals "level_end"' in text


def test_proc_writes_each_item_through_builtin():
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/scan_unequipped.wf").read_text(
        encoding="utf-8")
    assert "write_bag_item($group, $equip)" in text
    assert "session.bag_items" not in text


def test_each_item_is_persisted_immediately():
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/scan_unequipped.wf").read_text(
        encoding="utf-8")
    collect_at = text.index("eval $items.$fp = $equip")
    write_at = text.index("eval write_bag_item($group, $equip)", collect_at)
    assert write_at > collect_at


def test_min_level_is_explicit_proc_parameter():
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/scan_unequipped.wf").read_text(
        encoding="utf-8")
    assert ("def scan_slot_bag($slot, $group, $detail_kind, $min_level, "
            "$min_affix_count)") in text
    calls = [line.strip() for line in text.splitlines()
             if line.strip().startswith("call scan_slot_bag(")]
    assert len(calls) == 7
    assert all(line.endswith(", $min_level, $min_affix_count)") for line in calls)


def test_min_affix_count_filters_only_persistence_not_scan_dedup():
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/scan_unequipped.wf").read_text(
        encoding="utf-8")

    assert "default $min_affix_count = 0" in text
    assert text.count(
        "$equip._extra.affix_count >= $min_affix_count") == 2
    assert text.count("eval $filtered_count = $filtered_count + 1") == 2

    # 两个入库分支都必须先登记指纹。即使被过滤，装备仍参与空格残影判断，
    # 不能因为“不写入”破坏遍历的去重与终止协议。
    offsets = []
    start = 0
    while True:
        write_at = text.find("eval write_bag_item($group, $equip)", start)
        if write_at < 0:
            break
        offsets.append(write_at)
        seen_at = text.rfind("eval $items.$fp = $equip", 0, write_at)
        condition_at = text.rfind("if $min_affix_count <= 0", 0, write_at)
        assert seen_at < condition_at < write_at
        start = write_at + 1
    assert len(offsets) == 2
