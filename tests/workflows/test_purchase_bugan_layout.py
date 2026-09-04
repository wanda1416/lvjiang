"""不肝商店工作流必须按当前布局的实际面板尺寸遍历。"""

import json
from pathlib import Path

_ROOT = Path(__file__).parents[2]
_DESKTOP_LAYOUT = (
    _ROOT / "config" / "system" / "layouts" / "桌面布局" / "bugan_detail.json"
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
