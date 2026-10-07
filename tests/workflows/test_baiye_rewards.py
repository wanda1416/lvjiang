"""百业奖励的安全操作契约：只在证据明确时点击和记录。"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lvjiang.core.layout_manager import load_layout_by_key
from lvjiang.workflows.builtins import _registry
from lvjiang.workflows.grammar import parse_file
from tests.workflows.conftest import make_engine

_WORKFLOWS = Path(__file__).parents[2] / "config/system/workflows"


@pytest.fixture
def probe(monkeypatch):
    engine = make_engine(run_env="android", layout=load_layout_by_key("android"))
    engine._procs = dict(parse_file(_WORKFLOWS / "weekly_baiye_rewards.wf").procs)
    engine._procs.update(parse_file(_WORKFLOWS / "subcall/visual_state.wf").procs)
    responses = {}
    clicks = []
    observations = []

    def scan(node):
        name = node.target.name if hasattr(node, "target") else node.var_name
        answer = responses[name]
        engine.variables[name] = answer() if callable(answer) else answer

    def click(node):
        target = node.target
        reference = getattr(target, "reference", None)
        clicks.append((engine._resolve(reference) if reference else None,
                       getattr(target, "entity", None)))

    def observe(key, value, source):
        observations.append((key, value, source))
        return {"accepted": True, "value": value, "reason": "updated"}

    def install(name, fn):
        fn._inject = None
        monkeypatch.setitem(_registry._FUNCTION_REGISTRY, name, fn)

    install("profile_get", lambda *args: 0)
    install("profile_observe", observe)
    install("pixel_ratios", lambda *args: {"gold": 0})
    monkeypatch.setattr(engine, "_exec_scan", scan)
    monkeypatch.setattr(engine, "_exec_find", scan)
    monkeypatch.setattr(engine, "_exec_click", click)
    monkeypatch.setattr(engine, "_exec_drag", MagicMock())
    monkeypatch.setattr(engine, "_exec_wait", MagicMock())
    monkeypatch.setattr(engine, "_exec_screenshot", MagicMock())

    def call(name, *args):
        return engine._run_proc(engine._procs[name], list(args))[0]

    return engine, responses, clicks, observations, install, call


def test_dividend_unknown_or_unavailable_never_claims_or_marks_done(probe):
    _engine, scans, clicks, observed, install, call = probe
    scans["card"] = {"weekly_dividend_label": "每周 | 分红"}
    for gold, expected in [(0, 0), (0.2, -1)]:
        install("pixel_ratios", lambda *args, gold=gold: {"gold": gold})
        assert call("claim_baiye_dividend") == expected
    assert clicks == [(None, "dividend_tab"), (None, "dividend_tab")]
    assert observed == []


def test_dividend_requires_obtain_result_and_glow_change_before_recording(probe):
    _engine, scans, clicks, observed, install, call = probe
    scans.update(card={"weekly_dividend_label": "每周分红"}, obtained="")
    install("pixel_ratios", lambda *args: {"gold": 0.9})
    assert call("claim_baiye_dividend") == -1
    assert clicks[-1] == (None, "weekly_dividend")
    assert (None, "reward_close") not in clicks
    assert observed == []

    scans.update(obtained="reward_title", tabs={"home_tab": "首 | 页", "dividend_tab": "分红"})
    colors = iter([0.9, 0])
    install("pixel_ratios", lambda *args: {"gold": next(colors)})
    assert call("claim_baiye_dividend") == 1
    assert observed == [("baiye_dividend_of_week", 1, "百业分红")]


def test_inventory_rejects_wrong_period_total_and_regression(probe):
    _engine, _scans, _clicks, observed, install, call = probe
    key = "baiye_raoliang_of_month"
    assert call("observe_baiye_stock", "周库存 0/2", key, 2, "月库存", "银百叶") == -1
    assert call("observe_baiye_stock", "月库存 0/3", key, 2, "月库存", "银百叶") == -1
    assert observed == []
    install("profile_observe", lambda *args: {
        "accepted": False, "value": 2, "reason": "regressed"})
    assert call("observe_baiye_stock", "月库存 1/2", key, 2, "月库存", "银百叶") == -1


def test_purchase_records_inventory_change_and_never_assumes_click_succeeded(probe):
    _engine, scans, clicks, observed, install, call = probe
    scans.update(item=True, named={"item_name": "百业惊喜礼盒"}, inventory={"stock": "周库存 3/3"},
                 quantity={"quantity": "3"}, button="purchase")
    install("pixel_ratios", lambda *args: {"gold": 0.9})
    args = ("chijin", "百业惊喜礼盒", "baiye_chijin_box_of_week", 3, "周库存", "赤金小铺")
    assert call("buy_baiye_item", *args) == 0
    assert clicks.count((None, "purchase")) == 1
    assert [value for _key, value, _source in observed] == [0]

    readings = iter([{"stock": "周库存 3/3"}, {"stock": "周库存 0/3"}])
    scans["inventory"] = lambda: next(readings)
    assert call("buy_baiye_item", *args) == 1
    assert observed[-1] == ("baiye_chijin_box_of_week", 3, "赤金小铺")


def test_shared_month_record_skips_both_raoliang_entrances(probe):
    _engine, _scans, clicks, observed, install, call = probe
    install("profile_get", lambda key: 2 if key == "baiye_raoliang_of_month" else 0)
    for category, source in [("reputation", "百业声望"), ("silver", "银百叶")]:
        assert call("buy_baiye_item", category, "绕梁之音", "baiye_raoliang_of_month",
                    2, "月库存", source) == 0
    assert clicks == []
    assert observed == []


def test_activities_skip_ended_and_stop_at_first_unavailable_without_scrolling(probe):
    engine, scans, clicks, observed, install, call = probe
    ended = iter(["ended", "", ""])
    colors = iter([0.9, 0, 0])  # card2: 领取前/后；card3: 不可领。
    scans.update(ended=lambda: next(ended), title={"title": "示例活动"},
                 obtained="reward_title", tabs={"home_tab": "首页", "dividend_tab": "分红"})
    install("pixel_ratios", lambda *args: {"gold": next(colors)})
    assert call("claim_baiye_activities") == 0
    assert clicks == [("card_2", "claim"), (None, "reward_close")]
    engine._exec_drag.assert_not_called()
    assert observed == []


def test_empty_profile_enters_shop_but_full_quotas_still_check_activities(probe):
    from lvjiang.workflows.engine.signals import _ReturnSignal
    from lvjiang.workflows.grammar import parse_text

    engine, scans, clicks, _observed, install, _call = probe
    stubs = parse_text('''
    def declare_profiles($keys)
        return 0
    end
    def nav_main_to_menu()
        return 0
    end
    def is_in_menu_page()
        return 1
    end
    def is_in_main_page()
        return 1
    end
    ''')
    engine._procs.update(stubs.procs)
    program = parse_file(_WORKFLOWS / "weekly_baiye_rewards.wf")
    scans.update(entry=True, tabs={"home_tab": "首页", "dividend_tab": "分红"},
                 card={"weekly_dividend_label": "每周分红"}, shop_page="", list_page={"activity_list_tab": "活动 | 列表"},
                 ended="")
    install("profile_get", lambda *args: None)
    with pytest.raises(_ReturnSignal) as first:
        engine._exec_body(program.body)
    assert first.value.value == -1
    assert (None, "shop") in clicks

    clicks.clear()
    install("profile_get", lambda *args: 3)
    with pytest.raises(_ReturnSignal) as second:
        engine._exec_body(program.body)
    assert second.value.value == 0
    assert (None, "shop") not in clicks
    assert (None, "activity_list_tab") in clicks
