"""scan_wallet.wf 上拉补扫后的行位移合并回归测试

线上事故：安卓端 money_2 面板 3×6 装不下全部货币资产，工作流会 drag 上拉
1 行再补扫最后一行。旧实现只把补扫到的最后一行塞回 $money2
（`eval $money2.$row_key = $money2_last.$row_key`），但整个面板上拉一行后
屏幕上每一行的内容都会整体上移，不是只多出最后一行 —— 原第 3 行的
宝钱/长鸣玉被补扫结果整行覆盖丢失，而它们此时实际显示在第 2 行、没人补上，
导致 scan_get 再也找不到它们，宝钱/长鸣玉的 safe_set 日志一条都没打出来。

本测试直接加载生产 .wf 里的 shift_rows_after_scroll / scan_targets /
scan_get / find_cell 四个子过程（不复制粘贴实现），用事故当天的真实识别
数据回放，锁定：位移合并正确、已找到的值不被冲掉、点击坐标取自重建后的数据。
"""

from pathlib import Path

from lvjiang.core.config.resolver import SYSTEM_CONFIG_DIR
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine

_WF_PATH: Path = SYSTEM_CONFIG_DIR / "workflows" / "scan_wallet.wf"

# 事故当天的真实识别结果（见 issue 日志）：滚动前第 3 行有宝钱和长鸣玉，
# 第 1 行是长鸣珠（不是目标货币），第 2 行全空。
_PRE_SCROLL = {
    "1": {
        "1": {"label": "长鸣珠", "confidence": 0.6707, "count": 146},
        "2": {}, "3": {}, "4": {}, "5": {}, "6": {},
    },
    "2": {"1": {}, "2": {}, "3": {}, "4": {}, "5": {}, "6": {}},
    "3": {
        "1": {},
        "2": {"label": "宝钱", "confidence": 0.6319, "count": 1730000},
        "3": {"label": "长鸣玉", "confidence": 0.6355, "count": 37070},
        "4": {}, "5": {}, "6": {},
    },
}

# 上拉 1 行后只补扫最后一行的结果：新出现的么玉。
_FRESH_LAST_ROW = {
    "3": {
        "1": {},
        "2": {"label": "么玉", "confidence": 0.6014, "count": 116610},
        "3": {}, "4": {}, "5": {}, "6": {},
    },
}


def _run_with_wf_procs(code: str, initial: dict) -> dict:
    """执行 DSL 片段，并注册生产 scan_wallet.wf 里定义的全部子过程"""
    engine = make_engine()
    engine.variables = dict(initial)
    procs = parse_text(_WF_PATH.read_text(encoding="utf-8")).procs
    engine._procs = dict(procs)
    engine._exec_body(parse_text(code).body)
    return engine.variables


class TestShiftRowsAfterScroll:
    def test_rows_shift_up_by_one_and_last_row_takes_fresh_scan(self):
        """旧第 r 行 → 新第 r-1 行；最后一行用补扫结果；旧第 1 行滚出丢弃。"""
        result = _run_with_wf_procs(
            'call $shifted = shift_rows_after_scroll($pre, $fresh, $rows)\n',
            {"pre": _PRE_SCROLL, "fresh": _FRESH_LAST_ROW, "rows": 3},
        )
        shifted = result["shifted"]

        # 旧第2行（全空）→ 新第1行
        assert shifted["1"] == _PRE_SCROLL["2"]
        # 旧第3行（宝钱+长鸣玉）→ 新第2行，数据必须完整保留
        assert shifted["2"]["2"]["label"] == "宝钱"
        assert shifted["2"]["2"]["count"] == 1730000
        assert shifted["2"]["3"]["label"] == "长鸣玉"
        assert shifted["2"]["3"]["count"] == 37070
        # 新第3行 = 补扫结果（么玉）
        assert shifted["3"]["2"]["label"] == "么玉"
        assert shifted["3"]["2"]["count"] == 116610
        # 旧第1行的长鸣珠已滚出屏幕，不应残留
        assert all(
            cell.get("label") != "长鸣珠"
            for row in shifted.values() for cell in row.values()
        )


class TestIncidentReplay:
    """回放事故当天的完整取值流程，锁定三个目标货币都能拿到。"""

    _CODE = (
        'call $vals = scan_targets($money2, $rows)\n'
        'call $money2 = shift_rows_after_scroll($money2, $fresh, $rows)\n'
        'call $vals_rescan = scan_targets($money2, $rows)\n'
        'if $vals.bugan <= 0\n'
        '    eval $vals.bugan = $vals_rescan.bugan\n'
        'end\n'
        'if $vals.baoqian <= 0\n'
        '    eval $vals.baoqian = $vals_rescan.baoqian\n'
        'end\n'
        'if $vals.changmingyu <= 0\n'
        '    eval $vals.changmingyu = $vals_rescan.changmingyu\n'
        'end\n'
        'call $bugan_pos = find_cell($money2, $rows, "么玉")\n'
    )

    def test_all_three_targets_survive_the_scroll(self):
        """事故核心：宝钱/长鸣玉不能因为补扫最后一行而丢失。"""
        result = _run_with_wf_procs(
            self._CODE,
            {"money2": _PRE_SCROLL, "fresh": _FRESH_LAST_ROW, "rows": 3},
        )
        vals = result["vals"]
        assert vals["bugan"] == 116610       # 滚动后才出现
        assert vals["baoqian"] == 1730000    # 滚动前就有，不能被冲掉
        assert vals["changmingyu"] == 37070  # 滚动前就有，不能被冲掉

    def test_click_position_points_at_post_scroll_location(self):
        """么玉点击坐标取自重建后的数据，指向滚动后的当前画面位置。"""
        result = _run_with_wf_procs(
            self._CODE,
            {"money2": _PRE_SCROLL, "fresh": _FRESH_LAST_ROW, "rows": 3},
        )
        assert result["bugan_pos"] == {"row": 3, "col": 2}

    def test_value_found_before_scroll_is_not_lost_when_its_row_scrolls_off(self):
        """滚动前已找到的值即使原本在旧第1行（滚动后已移出屏幕），也要保留。"""
        pre = {
            "1": {
                "1": {"label": "长鸣玉", "confidence": 0.71, "count": 999},
                "2": {}, "3": {}, "4": {}, "5": {}, "6": {},
            },
            "2": {"1": {}, "2": {}, "3": {}, "4": {}, "5": {}, "6": {}},
            "3": {"1": {}, "2": {}, "3": {}, "4": {}, "5": {}, "6": {}},
        }
        result = _run_with_wf_procs(
            self._CODE,
            {"money2": pre, "fresh": _FRESH_LAST_ROW, "rows": 3},
        )
        # 长鸣玉所在的旧第1行已滚出屏幕、不在重建后的数据里，
        # 但滚动前已经取到的值必须原样保留，不能被重扫结果的 0 冲掉
        assert result["vals"]["changmingyu"] == 999
        assert result["vals"]["bugan"] == 116610
