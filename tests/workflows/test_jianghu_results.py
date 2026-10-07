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


def test_first_pass_details_and_rescan_only_append_new_actionable_tasks(monkeypatch):
    engine, trace = mixed_run(monkeypatch)
    result = engine.output["jianghu"]
    assert result["status"] == "partial" and result["finished"]
    assert result["passes"] == 2
    assert result["counts"] == {"already_done": 1, "completed": 2, "blocked": 1,
                                "disabled": 1, "not_implemented": 1, "refresh_exhausted": 1}
    assert set(result["tasks"]) == {"huanzhuang", "yinjiu", "kanbao", "juezhanglin", "heying"}
    records = [*result["unidentified"], *(record for group in result["tasks"].values() for record in group["records"])]
    first = sorted((record for record in records if record["pass"] == 1), key=lambda record: record["card"])
    assert first[0]["outcome"] == "already_done" and first[0]["task"] == ""
    assert first[1]["reward"]["status"] == "claim_attempted"
    assert first[2]["action"] == {"status": "blocked", "reason": "item_not_found"}
    assert first[3]["task_name"] == "看报" and first[3]["outcome"] == "disabled"
    assert first[4]["outcome"] == "not_implemented"
    assert first[5]["refresh_count"] == 2
    extra = [record for record in records if record["pass"] == 2]
    assert [(item["pass"], item["task_text"]) for item in extra] == [
        (2, "合影新任务")]
    # 已知饮酒任务的补扫仍遵循旧动作流程，但不重复追加或改写首轮结果。
    assert sum(event[0] == "action_yinjiu" for event in trace) == 2
    assert len(result["tasks"]["yinjiu"]["records"]) == 1
    # 同类型任务即使换了位置、文案变化，补扫也不会重复写入。
    assert len(result["tasks"]["huanzhuang"]["records"]) == 1
    assert sum(event[0] == "action_huanzhuang" for event in trace) == 2


def test_nested_updates_keep_partial_output_when_processing_is_stopped(monkeypatch):
    source = WORKFLOW.read_text(encoding="utf-8")
    program = parse_text(source)
    engine = make_engine()
    engine._procs = program.procs
    setup = parse_text('''global $jianghu_result, $jianghu_item, $claim_reward
    eval $claim_reward = false
    call $jianghu_result = new_jianghu_result()
    collect $jianghu_result as "jianghu"
    call $jianghu_item = begin_jianghu_record(false, "1")
    call record_jianghu_task("huanzhuang", "换装")
    call identify_jianghu_record(false, "1", "换装", true)
    call record_jianghu_outcome("executing", "")
    call record_jianghu_action_returned()
    ''')
    engine._exec_body(setup.body)
    result = engine.output["jianghu"]
    assert not result["finished"] and result["status"] == "running"
    record = result["tasks"]["huanzhuang"]["records"][0]
    assert record["outcome"] == "executed_unverified"
    assert record["completion"] == "unknown"
    assert record["reward"]["status"] == "disabled"
    # 子过程提交共享对象后，停止发生在下一个语句前；收集数据没有被局部 output 覆盖。
    monkeypatch.setattr(engine, "_stop_check", lambda: True)
    engine._exec_body(parse_text("call finish_jianghu_result()\n").body)
    assert not result["finished"]


def test_explicit_early_return_keeps_legacy_return_and_collects_failure(monkeypatch):
    program = parse_text(WORKFLOW.read_text(encoding="utf-8"))
    engine = make_engine()
    engine._procs = {**program.procs, **parse_text("def declare_profiles($keys)\nreturn -1\nend\n").procs}
    with pytest.raises(_ReturnSignal) as caught:
        engine._exec_body(program.body)
    assert caught.value.value == -1
    result = engine.output["jianghu"]
    assert result["status"] == "failed"
    assert result["reason"] == "profile_declaration_failed"
    assert result["tasks"] == {} and result["unidentified"] == []


def test_drink_failures_record_reason_without_changing_old_return_codes(monkeypatch):
    program = parse_text(WORKFLOW.read_text(encoding="utf-8"))
    engine = make_engine(run_env="android")
    engine._procs = {**program.procs, **parse_text('''
    def nav_back_to_main()
       return 0
    end
    ''').procs}
    monkeypatch.setattr(engine, "_exec_click", lambda node: None)
    monkeypatch.setattr(engine, "_exec_wait", lambda node: None)
    monkeypatch.setattr(engine, "_exec_wait_stable", lambda node: None)
    monkeypatch.setattr(engine, "_exec_recognize", lambda node: engine.variables.update({node.target.name: None}))
    setup = parse_text('''global $jianghu_result, $jianghu_item, $claim_reward, $min_yinjiu_confidence
    eval $claim_reward = false
    eval $min_yinjiu_confidence = "0.65"
    call $jianghu_result = new_jianghu_result()
    collect $jianghu_result as "jianghu"
    call $jianghu_item = begin_jianghu_record(false, "6")
    call record_jianghu_task("yinjiu", "饮酒")
    call identify_jianghu_record(false, "6", "醉意", true)
    ''')
    # 两个真实分支的旧返回不同：缺道具返回 1，导航失败仍返回 0。
    # 新增记录只解释失败，不修正这个旧契约或更改后续领奖控制。
    for nav_result, expected_return, expected_reason in (
        (0, 1, "item_not_found"), (-1, 0, "item_navigation_failed"),
    ):
        engine._procs.update(parse_text(f"def nav_main_to_item()\nreturn {nav_result}\nend\n").procs)
        engine._exec_body(setup.body)
        value, _ = engine._run_proc(engine._procs["action_yinjiu"], ["card_6", "6"])
        assert value == expected_return
        record = engine.output["jianghu"]["tasks"]["yinjiu"]["records"][0]
        assert record["reason"] == expected_reason
        assert record["completion"] == "unknown"
