"""江湖号令结果只使用既有观察，不参与任务执行决策。"""
from collections import Counter

import pytest

from lvjiang.core.config.resolver import SYSTEM_CONFIG_DIR
from lvjiang.workflows.engine.signals import _ReturnSignal
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine

WORKFLOW = SYSTEM_CONFIG_DIR / "workflows/daily_jianghu.wf"


def mixed_run(monkeypatch, source=None):
    program = parse_text(source if source is not None else WORKFLOW.read_text(encoding="utf-8"))
    engine = make_engine(run_env="android")
    engine.variables.update({"claim_reward": True, "max_refresh": 2, "do_kanbao": False})
    engine._procs = {**program.procs, **parse_text("def declare_profiles($keys)\nreturn 1\nend\ndef detect_jianghu_card_reward_state($label)\nreturn \"unclaimed\"\nend\n").procs}
    phase = 1
    probes = Counter()
    scans = Counter()
    trace = []
    original_run = engine._run_proc

    def run_proc(proc, args):
        nonlocal phase
        name = proc.name
        if name == "declare_profiles":
            return 1, {}
        if name == "ensure_at_haoling":
            if args[0] == "补扫":
                phase = 2
            trace.append((name, *args))
            return 0, {}
        if name == "sync_haoling_of_week":
            return 100, {}
        if name == "is_in_haoling_page":
            return 0, {}
        if name == "is_task_completed":
            key = phase, args[0]
            index = probes[key]
            probes[key] += 1
            done = args[0] == "card_1" or (phase == 2 and args[0] == "card_2")
            done |= (phase == 1 and args[0] == "card_2" and index > 0)
            done |= (phase == 2 and args[0] == "card_4" and index > 0)
            done |= (phase == 2 and args[0] == "card_6" and index > 1)
            trace.append((name, args[0], done))
            return int(done), {}
        if name == "detect_jianghu_card_reward_state":
            trace.append((name, *args))
            return "unclaimed", {}
        if name == "claim_reward":
            trace.append((name, *args))
            return None, {}
        if name in {"action_huanzhuang", "action_heying", "action_yinjiu"}:
            trace.append((name, *args))
            if name == "action_yinjiu":
                if "record_jianghu_outcome" in engine._procs:
                    original_run(engine._procs["record_jianghu_outcome"], ["blocked", "item_not_found"])
                return 1, {}
            return None, {}
        return original_run(proc, args)

    def scan(node):
        card = engine.variables["label"]
        scans[phase, card] += 1
        text = {
            (1, "card_2"): "换装原任务", (1, "card_3"): "醉意原任务",
            (1, "card_4"): "东方原任务", (1, "card_5"): "觉障林原任务",
            (1, "card_6"): "其他任务", (2, "card_3"): "醉意原任务",
            (2, "card_4"): "合影新任务", (2, "card_5"): "东方新任务",
            (2, "card_6"): "其他新任务" if scans[phase, card] == 1 else "换装新任务",
        }[phase, card]
        engine.variables[node.target.name] = {"label": text}
        trace.append(("scan", phase, card, text))

    monkeypatch.setattr(engine, "_run_proc", run_proc)
    monkeypatch.setattr(engine, "_exec_scan", scan)
    monkeypatch.setattr(engine, "_exec_click", lambda node: trace.append(("click", str(node.target))))
    monkeypatch.setattr(engine, "_exec_wait", lambda node: trace.append(("wait", str(node.delay))))
    monkeypatch.setattr(engine, "_exec_wait_stable", lambda node: trace.append(("wait_stable", str(node.timeout))))
    engine._exec_body(program.body)
    return engine, trace


def test_summary_keeps_outcomes_and_stable_keys_without_diagnostic_payload(monkeypatch):
    engine, trace = mixed_run(monkeypatch)
    result = engine.output["jianghu"]
    assert result["status"] == "partial"
    assert set(result["tasks"]) == {"huanzhuang", "yinjiu", "kanbao", "juezhanglin", "heying"}
    assert result["tasks"]["juezhanglin"] == {
        "name": "觉障林", "records": [{"status": "not_implemented", "reason": "暂未实现"}]}
    assert result["tasks"]["yinjiu"]["records"][0]["reason"] == "未找到黄泉酿"
    assert result["unidentified"][0]["status"] == "already_done"
    assert result["unidentified"][0]["reward"] == "claim_attempted"
    assert len(result["tasks"]["yinjiu"]["records"]) == 1
    assert len(result["tasks"]["huanzhuang"]["records"]) == 1
    assert sum(event[0] == "action_yinjiu" for event in trace) == 2
    assert sum(event[0] == "action_huanzhuang" for event in trace) == 2
    assert result["tasks"]["heying"]["records"][0]["status"] == "completed"
    assert set(result) <= {"status", "reason", "tasks", "unidentified", "reputation"}


def test_partial_summary_survives_nested_updates_and_stop(monkeypatch):
    engine = make_engine()
    engine._procs = parse_text(WORKFLOW.read_text()).procs
    engine._exec_body(parse_text('''global $jianghu_result, $jianghu_item, $claim_reward
    eval $claim_reward = false
    call $jianghu_result = new_jianghu_result()
    collect $jianghu_result.summary as "jianghu"
    call $jianghu_item = begin_jianghu_record(false, "1")
    call record_jianghu_task("huanzhuang", "换装")
    call identify_jianghu_record(false, "1", "换装", true)
    call record_jianghu_outcome("executing", "")
    call record_jianghu_action_returned()
    ''').body)
    result = engine.output["jianghu"]
    assert result["status"] == "running"
    assert result["tasks"]["huanzhuang"]["records"] == [
        {"status": "executed_unverified", "reason": "执行后未确认完成"}]
    monkeypatch.setattr(engine, "_stop_check", lambda: True)
    engine._exec_body(parse_text("call finish_jianghu_result()\n").body)
    assert result["status"] == "running"


def test_explicit_failure_summary_preserves_legacy_return():
    program = parse_text(WORKFLOW.read_text())
    engine = make_engine()
    engine._procs = {**program.procs, **parse_text("def declare_profiles($keys)\nreturn -1\nend\n").procs}
    with pytest.raises(_ReturnSignal) as caught:
        engine._exec_body(program.body)
    assert caught.value.value == -1
    result = engine.output["jianghu"]
    assert result["status"] == "failed" and result["reason"] == "档案声明失败"
    assert result["tasks"] == {} and result["unidentified"] == []


def test_drink_failure_then_completion_keeps_problem_without_stage_events():
    engine = make_engine()
    engine._procs = parse_text(WORKFLOW.read_text()).procs
    engine._exec_body(parse_text('''global $jianghu_result, $jianghu_item, $claim_reward
    eval $claim_reward = false
    call $jianghu_result = new_jianghu_result()
    collect $jianghu_result.summary as "jianghu"
    call $jianghu_item = begin_jianghu_record(false, "1")
    call record_jianghu_task("yinjiu", "饮酒")
    call identify_jianghu_record(false, "1", "醉意", true)
    call record_jianghu_outcome("failed", "item_navigation_failed")
    call record_jianghu_outcome("completed", "completion_observed")
    ''').body)
    record = engine.output["jianghu"]["tasks"]["yinjiu"]["records"][0]
    assert record["status"] == "completed"
    assert record["problem"] == "背包导航失败"
    assert "events" not in record
