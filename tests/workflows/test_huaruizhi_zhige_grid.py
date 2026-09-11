"""weekly_huaruizhi.wf 止戈项目网格定位回归测试

渡尘墟入口不在固定格子上：止戈项目的排列会随赛季/新玩法变动，脚本必须
按文字在 3×3 网格里找，而不是写死坐标。这里直接加载生产 .wf 里的
find_zhige_cell（不复制实现），锁定命中格子、未命中和空格子三种情况。
"""

from pathlib import Path

from lvjiang.core.config.resolver import SYSTEM_CONFIG_DIR
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine

_WF_PATH: Path = SYSTEM_CONFIG_DIR / "workflows" / "weekly_huaruizhi.wf"

# 止戈页整面板 OCR 的典型结果：卡片带副标题，渡尘墟不在第一格。
_GRID = {
    "1": {"1": "试炼之地 每日", "2": "论剑台 3v3", "3": ""},
    "2": {"1": "渡尘墟 单排/组队", "2": "群芳会", "3": ""},
    "3": {"1": "", "2": "", "3": ""},
}


def _run(code: str, initial: dict) -> dict:
    """执行 DSL 片段，并注册生产 weekly_huaruizhi.wf 里定义的全部子过程"""
    engine = make_engine()
    engine.variables = dict(initial)
    engine._procs = dict(parse_text(_WF_PATH.read_text(encoding="utf-8")).procs)
    engine._exec_body(parse_text(code).body)
    return engine.variables


_CODE = 'call $pos = find_zhige_cell($grid, $rows, $cols, $item)\n'


def test_hits_the_cell_containing_the_item_name():
    """渡尘墟在第 2 行第 1 列，返回的行列必须指向该格。"""
    result = _run(_CODE, {"grid": _GRID, "rows": 3, "cols": 3, "item": "渡尘墟"})
    assert result["pos"] == {"row": 2, "col": 1}


def test_missing_item_returns_falsy():
    """网格里没有该玩法时返回空字典，调用方据此判失败而不是乱点。"""
    result = _run(_CODE, {"grid": _GRID, "rows": 3, "cols": 3, "item": "无相城"})
    assert not result["pos"]


def test_covers_the_last_cell_of_the_grid():
    """遍历要覆盖到最后一格：range 端点写错时只有右下角这格会漏。"""
    grid = {
        "1": {"1": "试炼之地", "2": "", "3": ""},
        "2": {"1": "", "2": "", "3": ""},
        "3": {"1": "", "2": "", "3": "渡尘墟 单排/组队"},
    }
    result = _run(_CODE, {"grid": grid, "rows": 3, "cols": 3, "item": "渡尘墟"})
    assert result["pos"] == {"row": 3, "col": 3}


def test_scans_rows_in_order_and_stops_at_first_hit():
    """同名文案出现在多格时取先扫到的（行优先），避免点到重复卡片。"""
    grid = {
        "1": {"1": "", "2": "渡尘墟 单排", "3": ""},
        "2": {"1": "渡尘墟 组队", "2": "", "3": ""},
        "3": {"1": "", "2": "", "3": ""},
    }
    result = _run(_CODE, {"grid": grid, "rows": 3, "cols": 3, "item": "渡尘墟"})
    assert result["pos"] == {"row": 1, "col": 2}


def test_task_returns_immediately_after_starting_match():
    source = _WF_PATH.read_text(encoding="utf-8")
    after_start = source.partition(
        "click [tongyou_main].[zhige_start] after wait @page_refresh"
    )[2]
    assert "return 0" in after_start
    assert "battle_wait" not in source
    assert "app stop" not in source


def test_entering_duchenxu_syncs_weekly_huaruizhi_progress():
    source = _WF_PATH.read_text(encoding="utf-8")
    enter_call = source.index('enter_zhige_item("渡尘墟")')
    total_click = source.index("click [tongyou_main].[huaruizhi_total]", enter_call)
    progress_scan = source.index(
        "scan [tongyou_main].[huaruizhi_of_week]", total_click)
    profile_sync = source.index(
        'sync_weekly_progress($progress.huaruizhi_of_week, "huaruizhi_of_week"',
        progress_scan,
    )
    assert enter_call < total_click < progress_scan < profile_sync


def test_full_progress_returns_before_match_setup():
    source = _WF_PATH.read_text(encoding="utf-8")
    nav_call = source.index("call $nav_result = nav_main_to_duchenxu()")
    full_check = source.index(
        "if $nav_result.huaruizhi_of_week >= 3000", nav_call)
    early_return = source.index("return 0", full_check)
    mode_setup = source.index('find [tongyou_main].[zhige_menu] as $area')

    assert nav_call < full_check < early_return < mode_setup
