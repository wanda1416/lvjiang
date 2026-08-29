"""bag_traversal 遍历策略测试

DedupTraversal 用轮次脚本驱动：windows[i] = 第 i 次拖拽后窗口各行
首列指纹（0 = 初始窗口，超出脚本重复最后一窗，模拟硬到底），验证
滑动窗口去重的核心性质：滚多/滚少/漂移零纠偏、三条到底规则、调律
后指纹回读。调度器测试验证 _traverse_bag 的策略选择优先级。
"""

import pytest

from lvjiang.apps.yysls.workflows.implementations import auto_tuning
from lvjiang.apps.yysls.workflows.implementations.auto_tuning import (
    AutoTuningWorkflow,
)
from lvjiang.apps.yysls.workflows.implementations.bag_traversal import (
    DEFAULT_TRAVERSAL,
    TRAVERSALS,
    BagTraversal,
    DedupTraversal,
    PositionalTraversal,
)

WEAPON_DETAIL = AutoTuningWorkflow.WEAPON_DETAIL


class _Panel:
    rows = 3
    cols = 2


class _Align:
    def __init__(self, n_rows):
        self.n_rows = n_rows


class DedupFakeWF(AutoTuningWorkflow):
    """轮次脚本替身：drag_grid 推进轮次，_read_row 按当前窗口返回指纹"""

    def __init__(self, windows: list[list[str]]):
        self.windows = windows          # 每轮窗口各行首列指纹
        self.n_rows_map: dict[int, int] = {}   # 轮次(=drags) -> n_rows
        self.tune_map: dict[str, str] = {}     # 指纹 -> 调律后指纹
        self.recycle_fps: set[str] = set()     # 回收后格位已空的指纹
        self.processed: list[str] = []
        self.drags = 0
        self._delay_params = {}  # wait_stable 命名参数查找用

    @property
    def is_stopped(self) -> bool:
        return False

    def wait_delay(self, name: str):
        pass

    def wait_stable(self, timeout: float | str = 8.0, threshold: float = 0.02,
                    interval: float = 0.3, stable_duration: float = 0.5,
                    least: float = 0.5):
        pass

    def _find_panel(self, scene_key, panel_key):
        return _Panel()

    def align_panel(self, scene_key, panel_key):
        return _Align(self.n_rows_map.get(self.drags, 3))

    def drag_grid(self, *a, **k):
        self.drags += 1

    def _win(self) -> list[str]:
        idx = min(self.drags, len(self.windows) - 1)
        return self.windows[idx]

    def _read_row(self, detail_scene, row, col=1):
        win = self._win()
        fp = win[row - 1] if row <= len(win) else ""
        return (fp or "空", fp, {"fp": fp} if fp else {})

    def _process_equipment(self, name, equip, detail_scene,
                           row=None, col=1):
        fp = equip["fp"]
        self.processed.append(fp)
        if fp in self.recycle_fps:
            return ""   # 回收后该格已空（无装备补位）
        new_fp = self.tune_map.get(fp)
        if not new_fp:
            return fp
        # 调律改动装备：该指纹在背包中的所有后续读取都变为新指纹
        for w in self.windows:
            for i, v in enumerate(w):
                if v == fp:
                    w[i] = new_fp
        return new_fp

    def _process_row_cols(self, detail_scene, win_row, logical_row, cols):
        pass   # 列遍历不在本测试范围（有独立测试）


def test_initial_window_processed_in_order():
    """初始窗口 3 行全部按序处理"""
    wf = DedupFakeWF([["A", "B", "C"]])
    DedupTraversal().traverse(wf, WEAPON_DETAIL)
    assert wf.processed[:3] == ["A", "B", "C"]


def test_scroll_one_row_skips_duplicates():
    """正常滚一行：前两行重复跳过，仅新行被处理"""
    wf = DedupFakeWF([["A", "B", "C"], ["B", "C", "D"]])
    DedupTraversal().traverse(wf, WEAPON_DETAIL)
    assert wf.processed == ["A", "B", "C", "D"]


def test_overscroll_two_rows_no_loss():
    """滚多两行：两个新行都被处理，零遗漏、无纠偏"""
    wf = DedupFakeWF([["A", "B", "C"], ["C", "D", "E"]])
    DedupTraversal().traverse(wf, WEAPON_DETAIL)
    assert wf.processed == ["A", "B", "C", "D", "E"]


def test_fp_drift_reprocesses_once_then_heals():
    """旧行指纹漂移 → 误判新行重复处理一次；最新指纹入窗后自愈"""
    wf = DedupFakeWF([["A", "B", "C"], ["B2", "C", "D"]])   # B 漂移为 B2
    DedupTraversal().traverse(wf, WEAPON_DETAIL)
    assert wf.processed == ["A", "B", "C", "B2", "D"]
    assert wf.processed.count("B2") == 1   # 后续轮窗口重复 B2 不再误判


def test_empty_row_ends_immediately():
    """读到空行 = 背包尽头，初始扫描即结束，不再拖拽"""
    wf = DedupFakeWF([["A", "B", ""]])
    DedupTraversal().traverse(wf, WEAPON_DETAIL)
    assert wf.processed == ["A", "B"]
    assert wf.drags == 0


def test_hard_bottom_two_idle_rounds():
    """硬到底：满窗连续 2 轮零新行 → 结束，共拖拽 2 次"""
    wf = DedupFakeWF([["A", "B", "C"]])   # 拖拽后窗口不变
    DedupTraversal().traverse(wf, WEAPON_DETAIL)
    assert wf.processed == ["A", "B", "C"]
    assert wf.drags == 2


def test_partial_window_zero_new_ends_at_once():
    """非满窗且零新行 → 立即到底，无需第二轮确认"""
    wf = DedupFakeWF([["A", "B", "C"], ["B", "C"]])
    wf.n_rows_map = {1: 2}   # 第 1 次拖拽后 align 只检测到 2/3 行
    DedupTraversal().traverse(wf, WEAPON_DETAIL)
    assert wf.processed == ["A", "B", "C"]
    assert wf.drags == 1


def test_tuned_fp_reread_dedups_next_round():
    """调律改动指纹 → 回读存实际指纹，下一轮该行判重复不再处理"""
    wf = DedupFakeWF([["A", "B", "C"], ["B", "C", "D"]])
    wf.tune_map = {"C": "C2"}   # C 被调律 → 后续读取皆为 C2
    DedupTraversal().traverse(wf, WEAPON_DETAIL)
    assert wf.processed == ["A", "B", "C", "D"]
    assert "C2" not in wf.processed


def test_recycled_empty_slot_ends_traversal():
    """回收后格位已空（无补位）→ 视为背包尽头，立即结束不再拖拽"""
    wf = DedupFakeWF([["A", "B", "C"]])
    wf.recycle_fps = {"B"}
    DedupTraversal().traverse(wf, WEAPON_DETAIL)
    assert wf.processed == ["A", "B"]
    assert wf.drags == 0


def test_low_level_signal_ends_slot_without_reading_later_rows():
    """首件低等级有效装备出现后，不再读本窗口后续行，也不滚动。"""
    class _LowLevelWF(DedupFakeWF):
        def _process_equipment(self, name, equip, detail_scene,
                               row=None, col=1):
            self.processed.append(equip["fp"])
            if equip["fp"] == "B":
                self._slot_level_exhausted = True
            return equip["fp"]

    wf = _LowLevelWF([["A", "B", "C"]])
    DedupTraversal().traverse(wf, WEAPON_DETAIL)

    assert wf.processed == ["A", "B"]
    assert wf.drags == 0


def test_slot_bottom_signal_stops_before_processing_sentinel():
    """槽位读取命中见底哨兵后，不得把该格交给装备处理。"""
    class _WukuBottomWF(DedupFakeWF):
        def _read_row(self, detail_scene, row, col=1):
            result = super()._read_row(detail_scene, row, col)
            if result[1] == "WUKU":
                self._slot_level_exhausted = True
            return result

    wf = _WukuBottomWF([["A", "WUKU", "C"]])
    DedupTraversal().traverse(wf, WEAPON_DETAIL)

    assert wf.processed == ["A"]
    assert wf.drags == 0


def test_max_rounds_fuse():
    """总轮数保险丝：每轮都有新行也会在 MAX_ROUNDS 处强制收束"""
    windows = [[f"r{i}a", f"r{i}b", f"r{i}c"] for i in range(10)]
    wf = DedupFakeWF(windows)
    t = DedupTraversal()
    t.MAX_ROUNDS = 3
    t.traverse(wf, WEAPON_DETAIL)
    assert wf.drags == 3   # 第 4 轮进入前收束


# ─── 调度器：_traverse_bag 策略选择 ──────────────────────


class DispatchFakeWF(AutoTuningWorkflow):
    def __init__(self):
        pass

    @property
    def is_stopped(self) -> bool:
        return False


@pytest.fixture
def stub_session(monkeypatch, tmp_path):
    """隔离 core SessionStore：wf_configs 默认为空，可由测试改写"""
    import lvjiang.constants as constants_mod
    import lvjiang.core.config.session as store_mod
    path = tmp_path / "session.json"
    monkeypatch.setattr(constants_mod, "SESSION_PATH", path)
    store_mod.reset_session_store()
    return store_mod.get_session_store()


@pytest.fixture
def spy_traversals(monkeypatch):
    """把注册表两个策略换成 spy，记录实际被调度的 key"""
    calls: list[str] = []

    def _spy(tag):
        class _Spy(BagTraversal):
            name = tag

            def traverse(self, wf, detail_scene):
                calls.append(tag)
        return _Spy

    monkeypatch.setitem(auto_tuning.TRAVERSALS, "dedup", _spy("dedup"))
    monkeypatch.setitem(auto_tuning.TRAVERSALS, "positional",
                        _spy("positional"))
    return calls


def test_dispatch_default_is_dedup(stub_session, spy_traversals):
    assert DEFAULT_TRAVERSAL == "dedup"
    DispatchFakeWF()._traverse_bag(WEAPON_DETAIL)
    assert spy_traversals == ["dedup"]


def test_dispatch_injected_positional(stub_session, spy_traversals):
    wf = DispatchFakeWF()
    wf.ctx.scroll_strategy = "positional"
    wf._traverse_bag(WEAPON_DETAIL)
    assert spy_traversals == ["positional"]


def test_dispatch_session_config(stub_session, spy_traversals):
    """无注入时从 wf_configs["auto_tuning"].scroll_strategy 读取"""
    stub_session.set_node("wf_configs", {"auto_tuning": {"scroll_strategy": "positional"}})
    DispatchFakeWF()._traverse_bag(WEAPON_DETAIL)
    assert spy_traversals == ["positional"]


def test_dispatch_unknown_key_raises(stub_session, spy_traversals):
    """未知策略抛异常，不默认回落"""
    wf = DispatchFakeWF()
    wf.ctx.scroll_strategy = "bogus"
    with pytest.raises(ValueError, match="未知遍历策略"):
        wf._traverse_bag(WEAPON_DETAIL)


def test_registry_contains_both():
    assert TRAVERSALS["dedup"] is DedupTraversal
    assert TRAVERSALS["positional"] is PositionalTraversal
