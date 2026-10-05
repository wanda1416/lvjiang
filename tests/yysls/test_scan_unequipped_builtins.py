"""装备全量扫描的窗口级背包游标测试。"""

from pathlib import Path

import pytest

# 导入以注册内置函数
import lvjiang.apps.yysls.workflows.builtins.equip_funcs as equip_funcs
from lvjiang.workflows.builtins import get_function


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

    def test_confirmed_empty_and_zero_new_ends_after_one_probe(self, engine):
        """新增窗口后的首次无新增空槽确认即可结束，不再多拖一轮。"""
        _init(engine)
        _visit_window(engine, ["a", "b"])
        assert _fn("bag_cursor_finish_window")(engine, 3, 3, True) == "scroll"

        assert _visit_window(engine, ["a", "b"]) == ["skip", "skip"]
        assert _fn("bag_cursor_finish_window")(engine, 3, 3, True) == "end"

    def test_confirmed_empty_with_new_row_keeps_legacy_scroll(self, engine):
        """当前窗口仍有新装备时，空槽不应阻止下一次探测滚动。"""
        _init(engine)
        _visit_window(engine, ["a"])
        assert _fn("bag_cursor_finish_window")(engine, 3, 3, True) == "scroll"

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


    def test_max_scroll_rounds_fuse(self, engine, monkeypatch):
        monkeypatch.setattr(equip_funcs, "_MAX_SCROLL_ROUNDS", 1)
        _init(engine)
        _visit_window(engine, ["a"])
        assert _fn("bag_cursor_finish_window")(engine, 3, 3) == "scroll"
        _visit_window(engine, ["b"])
        assert _fn("bag_cursor_finish_window")(engine, 3, 3) == "end"


def test_scan_unequipped_only_truncates_on_confirmed_empty_cells():
    """OCR 扫到任意详情文本时不触发新增截断，继续沿用旧游标规则。"""
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/scan_unequipped.wf").read_text(
        encoding="utf-8")

    assert "eval $hit_confirmed_empty = 0" in text
    assert text.count("eval $hit_confirmed_empty = 1") == 2
    assert text.count("if not $detail_open") == 2
    assert "$rows, $rows, $hit_confirmed_empty)" in text


def test_scan_unequipped_level_threshold_ends_current_slot_without_cast():
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/scan_unequipped.wf").read_text(
        encoding="utf-8")
    assert "int($equip.level)" not in text
    assert "$equip.level < $min_level" in text
    assert 'eval $signal = "level_end"' in text
    assert 'if $signal == "end" or $signal == "level_end"' in text


def test_each_item_is_persisted_immediately():
    root = Path(__file__).resolve().parents[2]
    text = (root / "config/system/workflows/scan_unequipped.wf").read_text(
        encoding="utf-8")
    collect_at = text.index("eval $items.$fp = $equip")
    write_at = text.index("eval write_bag_item($group, $equip)", collect_at)
    assert write_at > collect_at


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
