"""不肝商店工作流必须按当前布局的实际面板尺寸遍历。"""

import json
from pathlib import Path

_ROOT = Path(__file__).parents[2]
_DESKTOP_LAYOUT = (
    _ROOT / "config" / "system" / "layouts" / "desktop" / "bugan_detail.json"
)
_WORKFLOW = _ROOT / "config" / "system" / "workflows" / "purchase_bugan.wf"


def test_desktop_unused_second_shop_panel_is_disabled_3_by_5():
    layout = json.loads(_DESKTOP_LAYOUT.read_text(encoding="utf-8"))
    panels = {panel["key"]: panel for panel in layout["panels"]}

    panel = panels["shangpin_2"]
    assert (panel["rows"], panel["cols"]) == (3, 5)
    assert panel["disabled"] is True


def test_purchase_loops_use_layout_panel_dimensions():
    source = _WORKFLOW.read_text(encoding="utf-8")

    assert 'eval $season_rows = panel_rows("bugan_detail", "shangpin_1")' in source
    assert 'eval $season_cols = panel_cols("bugan_detail", "shangpin_1")' in source
    assert "for r in range(1, $season_rows)" in source
    assert "for c in range(1, $season_cols)" in source

    buy_panel = source.partition("def buy_panel($panel_key, $keywords)")[2]
    assert 'eval $rows = panel_rows("bugan_detail", $panel_key)' in buy_panel
    assert 'eval $cols = panel_cols("bugan_detail", $panel_key)' in buy_panel
    assert "for r in range(1, $rows)" in buy_panel
    assert "for c in range(1, $cols)" in buy_panel
    assert "for c in [1, 2, 3, 4]" not in source


def _extract_def(source: str, name: str) -> str:
    head = f"def {name}("
    body = source.partition(head)[2].partition("\nend")[0]
    return head + body + "\nend\n"


def test_waiguan_cell_match_runs_in_engine():
    """外观格子判定必须在引擎中真实可执行（回归：not(...) 括号写法曾被
    解析为函数调用，运行期报「未知内置函数: not」中断批量任务）。"""
    from tests.workflows.conftest import run

    source = _WORKFLOW.read_text(encoding="utf-8")
    def_src = _extract_def(source, "is_niaoniao_bind")
    code = def_src + '''call $hit = is_niaoniao_bind($cell)
if not $hit
    eval $result = "skip"
else
    eval $result = "buy"
end
'''
    # 命中：真实 OCR 曾把袅误识为枭，含「音+绑」仍应命中
    assert run(code, {"cell": "枭袅之音·绑 200"})["result"] == "buy"
    assert run(code, {"cell": "袅袅之音·绑 200"})["result"] == "buy"
    # 不含「绑」或不含「音」：已购/未上架/绕梁之音均跳过
    assert run(code, {"cell": "袅袅之音 200"})["result"] == "skip"
    assert run(code, {"cell": "绕梁之音·时 100"})["result"] == "skip"
    assert run(code, {"cell": ""})["result"] == "skip"


def test_workflow_has_no_parenthesized_not_condition():
    """DSL 条件不支持括号分组：not(...) 会被解析成函数调用直接报错。"""
    source = _WORKFLOW.read_text(encoding="utf-8")
    assert "not (" not in source
