"""业务结果保留既有事实，不将点击、空 OCR 或写入拒绝伪装为成功。"""
from pathlib import Path

import pytest

from lvjiang.workflows.builtins._registry import _FUNCTION_REGISTRY
from lvjiang.workflows.engine.signals import _ReturnSignal
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine
from tests.workflows.test_redeem_code import RedeemGame

WORKFLOWS = Path(__file__).resolve().parents[2] / "config/system/workflows"
HELPERS = parse_text(
    (WORKFLOWS / "subcall/result_records.wf").read_text(encoding="utf-8")).procs


def prepare(name, **variables):
    program = parse_text((WORKFLOWS / f"{name}.wf").read_text(encoding="utf-8"))
    engine = make_engine()
    engine.variables.update(variables)
    engine._procs = {**program.procs, **HELPERS}
    return engine, program


def initialize(engine):
    engine._exec_body(parse_text('global $record_result\ncall $record_result = new_record_result()\ncollect $record_result as "result"\n').body)


def test_checkin_missing_reward_is_unknown_and_click_is_only_attempt(monkeypatch):
    engine, _ = prepare("daily_checkin")
    initialize(engine)
    rewards = iter(["", "reward"])
    clicks = []

    def find(node):
        engine.variables[node.var_name] = "activity" if node.var_name == "found" else next(rewards)

    monkeypatch.setattr(engine, "_exec_wait", lambda node: None)
    monkeypatch.setattr(engine, "_exec_find", find)
    monkeypatch.setattr(engine, "_exec_click", lambda node: clicks.append(node))
    engine._exec_body(parse_text('call claim_visible_activity("活动甲", "签到")\ncall claim_visible_activity("活动乙", "签到")\n').body)
    records = engine.output["result"]["items"]["activities"]["records"]
    assert [r["status"] for r in records] == ["no_reward_detected", "claim_attempted"]
    assert [r["data"]["activity_name"] for r in records] == ["活动甲", "活动乙"]
    assert len(clicks) == 4


def test_wallet_rejected_value_preserves_old_value_and_records_units(monkeypatch):
    engine, _ = prepare("scan_wallet")
    initialize(engine)
    writes = []
    monkeypatch.setitem(_FUNCTION_REGISTRY, "profile_get", lambda key: 10)
    monkeypatch.setitem(_FUNCTION_REGISTRY, "profile_set", lambda *args: writes.append(args))
    engine._exec_body(parse_text('call safe_set("tongbao", 30, 2, "通宝(万)", "日常获取")\ncall safe_set("baoqian", 11, 2, "宝钱(万)", "不肝商店")\n').body)
    items = engine.output["result"]["items"]
    assert items["tongbao"]["name"] == "通宝(万)"
    assert items["tongbao"]["records"][0] == {
        "status": "tolerance_rejected", "reason": "识别值超过允许差值，保留旧值",
        "data": {"old_value": 10, "observed_value": 30, "tolerance": 2},
    }
    assert items["baoqian"]["records"][0]["status"] == "write_attempted"
    assert writes == [("baoqian", 11, "不肝商店")]


def test_redeem_records_submit_and_cancel_without_claiming_redemption(monkeypatch):
    game = RedeemGame(monkeypatch, codes="EXAMPLE-A\nEXAMPLE-B", outcomes=("success", "used")).run()
    result = game.engine.output["redeem_code"]
    records = result["items"]["codes"]["records"]
    assert result["status"] == "processed"
    assert [r["status"] for r in records] == [
        "submit_attempted", "cancelled_fallback"]
    assert records[-1]["data"]["code"] == "EXAMPLE-B"


def test_redeem_user_stops_without_claiming_code_was_submitted(monkeypatch):
    game = RedeemGame(monkeypatch, platform="android", continue_redeem=False).run()
    result = game.engine.output["redeem_code"]
    assert result["status"] == "user_finished"
    assert result["items"] == {}


@pytest.mark.parametrize("name", ["purchase_bugan", "purchase_niaoniao", "purchase_xinfa", "scan_wallet"])
def test_profile_declaration_failure_still_collects_result_before_any_game_action(name):
    engine, program = prepare(name)
    engine._procs.update(parse_text('def declare_profiles($keys)\nreturn -1\nend\n').procs)
    with pytest.raises(_ReturnSignal) as returned:
        engine._exec_body(program.body)
    assert returned.value.value == -1
    assert engine.output[name]["status"] == "failed"
    engine._input.click.assert_not_called()


def test_preflight_does_not_require_or_initialize_result():
    engine, _ = prepare("purchase_xinfa")
    engine._exec_body(parse_text('call record_item("xinfa", "心法心得", "unknown", "", {})\ncall record_count("attempts", 1)\n').body)
    assert engine.output == {}


def test_xinfa_normal_finish_preserves_negative_return_and_stock_observations(monkeypatch):
    engine, program = prepare("purchase_xinfa")
    engine._procs.update(parse_text('def declare_profiles($keys)\nreturn 1\nend\ndef nav_main_to_menu()\nreturn 0\nend\ndef sync_weekly_remaining($raw, $key, $fallback, $cap, $label, $source)\nreturn 20\nend\n').procs)
    monkeypatch.setitem(_FUNCTION_REGISTRY, "profile_get", lambda key: 0)
    monkeypatch.setattr(engine, "_exec_click", lambda node: None)
    monkeypatch.setattr(engine, "_exec_wait", lambda node: None)

    def scan(node):
        key = node.fields[0].value
        value = {"stock_of_week": "580/600"} if key == "stock_of_week" else ""
        if key in {"peiyang", "exchange"}:
            value = key
        engine.variables[node.target.name] = value

    monkeypatch.setattr(engine, "_exec_scan", scan)
    with pytest.raises(_ReturnSignal) as returned:
        engine._exec_body(program.body)
    assert returned.value.value == -1
    result = engine.output["purchase_xinfa"]
    assert result["status"] == "processed"
    assert result["values"]["xinfa_of_week"] == 20
    assert result["items"]["xinfa"]["records"][0]["status"] == "no_choice_detected"


def test_bugan_records_blocked_and_attempted_goods_under_stable_category(monkeypatch):
    engine, _ = prepare("purchase_bugan")
    initialize(engine)
    engine._exec_body(parse_text('global $record_category\n').body)
    monkeypatch.setitem(_FUNCTION_REGISTRY, "panel_rows", lambda *args: 1)
    monkeypatch.setitem(_FUNCTION_REGISTRY, "panel_cols", lambda *args: 2)
    monkeypatch.setattr(engine, "_exec_scan", lambda node: engine.variables.update({
        node.target.name: {"1": {"1": "宝钱 库存不足", "2": "宝钱 可购买"}}}))
    monkeypatch.setattr(engine, "_exec_click", lambda node: None)
    monkeypatch.setattr(engine, "_exec_wait", lambda node: None)
    engine._exec_body(parse_text('call $bought = buy_panel("shangpin_1", {"宝钱": true})\n').body)
    result = engine.output["result"]
    assert set(result["items"]) == {"combat"}
    assert [r["status"] for r in result["items"]["combat"]["records"]] == ["blocked", "purchase_attempted"]
    assert "values" not in result
    assert engine.variables["bought"] == 1


def test_niaoniao_profile_increment_does_not_confirm_purchase_quantity(monkeypatch):
    engine, program = prepare("purchase_niaoniao")
    engine._procs.update(parse_text('def declare_profiles($keys)\nreturn 1\nend\ndef nav_main_to_menu()\nreturn 0\nend\ndef is_in_main_page()\nreturn 1\nend\n').procs)
    increments = []
    monkeypatch.setitem(_FUNCTION_REGISTRY, "profile_get", lambda key: 1 if key == "niaoniao_of_week" else 200)
    monkeypatch.setitem(_FUNCTION_REGISTRY, "profile_inc", lambda *args: increments.append(args))
    monkeypatch.setattr(engine, "_exec_click", lambda node: None)
    monkeypatch.setattr(engine, "_exec_wait", lambda node: None)
    monkeypatch.setattr(engine, "_exec_find", lambda node: engine.variables.update({node.var_name: "bound_item"}))

    def scan(node):
        key = node.fields[0].value
        engine.variables[node.target.name] = {"item_name": "袅袅之音·绑"} if key == "item_name" else "shop"

    monkeypatch.setattr(engine, "_exec_scan", scan)
    engine._exec_body(program.body)
    result = engine.output["purchase_niaoniao"]
    assert result["status"] == "purchase_attempted"
    record = result["items"]["niaoniao"]["records"][0]
    assert record["data"]["planned_quantity"] == 1
    assert record["data"]["confirmed_quantity"] is None
    assert increments == [("niaoniao_of_week", 1, "百珍商店")]
