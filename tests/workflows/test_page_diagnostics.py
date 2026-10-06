"""Real WF branches distinguish expected misses from unresolved page failures.

Protect screenshot timing before unattended interruption/cleanup, and normal
fallbacks that must keep running without manufacturing diagnostic failures.
"""
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from lvjiang.ui.batch.batch_runner import UnattendedInterrupt
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine

_ROOT = Path(__file__).parents[2] / "config/system/workflows"


def _engine(monkeypatch, files, scan_values=None):
    engine = make_engine(run_env="desktop")
    events = []
    for file in files:
        engine._procs.update(parse_text((_ROOT / file).read_text(encoding="utf-8")).procs)
    engine._capture.capture.return_value = np.zeros((8, 8, 3), dtype=np.uint8)
    values = scan_values or {}

    def scan(node):
        engine.capture_frame(source="test_scan")
        name = node.target.name
        engine.variables[name] = values.get(name, "" if node.by else {})

    def find(node):
        engine.capture_frame(source="test_find")
        engine.variables[node.var_name] = "candidate"

    def screenshot(*, from_last=False):
        if not from_last:
            engine.capture_frame(source="screenshot")
        events.append(("screenshot", from_last, engine.last_capture_source,
                       engine.last_capture_seq))

    monkeypatch.setattr(engine, "_exec_scan", scan)
    monkeypatch.setattr(engine, "_exec_find", find)
    monkeypatch.setattr(engine, "_exec_screenshot", screenshot)
    monkeypatch.setattr(engine, "_exec_wait", lambda node: None)
    monkeypatch.setattr(engine, "_exec_press", MagicMock())
    monkeypatch.setattr(engine, "_exec_click", lambda node: events.append(
        ("click", getattr(node.target, "entity", "candidate"))))
    return engine, events


def _run(engine, source):
    engine._exec_body(parse_text(source).body)


def test_bag_tab_miss_only_records_when_fallback_also_fails(monkeypatch):
    files = ["subcall/navigation.wf", "subcall/page_detection.wf"]
    # An unreadable tab is an expected intermediate state; 整理 can confirm bag.
    engine, events = _engine(monkeypatch, files, {"zhengli_text": {"zhengli": "整理"}})
    _run(engine, 'call $result = nav_main_to_bag()\n')
    assert engine.variables["result"] == 0
    assert not any(e[0] == "screenshot" for e in events)

    engine, events = _engine(monkeypatch, files)
    _run(engine, 'call $result = nav_main_to_bag()\n')
    assert engine.variables["result"] == -1
    shots = [e for e in events if e[0] == "screenshot"]
    assert shots == [("screenshot", True, "test_scan", engine.last_capture_seq)]


def test_equipment_page_diagnostic_precedes_confirm_and_keeps_choice(monkeypatch):
    engine, events = _engine(
        monkeypatch, ["subcall/navigation.wf", "subcall/page_detection.wf"],
        {"tab_text": {"sub_baoguo": "培养"}})

    def confirm(kind, **kwargs):
        assert kind == "confirm"
        assert events[-1][0] == "screenshot"
        events.append(("confirm",))
        return False

    engine._ui_callback = confirm
    _run(engine, 'call $result = nav_main_to_equip(true)\n')
    assert engine.variables["result"] == -1
    assert sum(e[0] == "screenshot" for e in events) == 1
    assert events[-1] == ("confirm",)


def test_selected_plan_without_use_button_is_expected(monkeypatch):
    engine, events = _engine(monkeypatch, ["subcall/loadout/game_plans.wf"], {
        "detail": {"plan_title": "plan", "main_art": "art_a", "sub_art": "art_b"},
    })
    _run(engine, 'call $result = select_game_plan("plan")\n')
    assert engine.variables["result"]["name"] == "plan"
    assert not any(e[0] == "screenshot" for e in events)


def test_missing_required_modal_action_records_before_failure_return(monkeypatch):
    engine, events = _engine(monkeypatch, ["subcall/loadout/game_plans.wf"], {
        "detail": {"plan_title": "plan", "main_art": "art_a", "sub_art": "art_b"},
        "use": "use_area", "message": {"modal_message": "装备缺失"},
    })
    _run(engine, 'call $result = select_game_plan("plan")\n')
    assert engine.variables["result"] == -2
    assert events[-1] == ("screenshot", True, "test_scan", engine.last_capture_seq)
    assert sum(e[0] == "screenshot" for e in events) == 1


def _login_engine(monkeypatch, *, succeeds):
    engine, events = _engine(monkeypatch, ["subcall/login.wf"])
    original_call = engine._exec_call_proc
    attempts = []

    def call(node):
        if node.name == "is_in_main_page":
            engine.capture_frame(source="main_page_check")
            attempts.append(engine.last_capture_seq)
            engine.variables[node.result_var] = int(succeeds and len(attempts) == 2)
        else:
            original_call(node)

    monkeypatch.setattr(engine, "_exec_call_proc", call)
    return engine, events, attempts


def test_login_expected_retry_and_close_then_success_do_not_record(monkeypatch):
    engine, events, attempts = _login_engine(monkeypatch, succeeds=True)
    _run(engine, 'call login_to_main_page()\n')
    assert len(attempts) == 2
    assert ("click", "close_btn") in events
    assert not any(e[0] == "screenshot" for e in events)


def test_login_exhaustion_preserves_decision_frame_before_cleanup_and_pause(monkeypatch):
    engine, events, attempts = _login_engine(monkeypatch, succeeds=False)

    def pause(kind, **kwargs):
        assert kind == "pause"
        events.append(("pause",))
        raise UnattendedInterrupt("login failed")

    engine._ui_callback = pause
    with pytest.raises(UnattendedInterrupt):
        _run(engine, 'call login_to_main_page()\n')
    assert len(attempts) == 8
    decision = ("screenshot", True, "main_page_check", attempts[-1])
    shots = [e for e in events if e[0] == "screenshot"]
    assert shots[0] == decision
    assert len(shots) == 2
    assert shots[1][1:3] == (False, "screenshot")
    final_close = max(i for i, e in enumerate(events) if e == ("click", "close_btn"))
    assert events.index(decision) < final_close < events.index(shots[1])
    assert events[-1] == ("pause",)
