"""属性单元准备工作流按指定 Profile 值选角色。"""
from pathlib import Path

from lvjiang.workflows.engine.signals import _ReturnSignal
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine

_PATH = Path("config/system/workflows/batch/prepare_item_by_attr.wf")


def test_selects_lowest_profile_role():
    program = parse_text(_PATH.read_text(encoding="utf-8"))
    engine = make_engine(run_env="android")
    workflow = engine._ensure_workflow()
    original = workflow.call_function
    seen = []

    def call_function(name, args, engine=None):
        if name == "profile_model":
            return "quota"
        if name == "profile_get":
            return {"u1": 20, "u2": 5}[args[1]]
        return original(name, args, engine=engine)

    workflow.call_function = call_function
    stub = parse_text(
        'def prepare_user($name, $state, $skip, $wait, $roll, $restart)\n'
        '    return {"status": "success", "state": $state}\n'
        'end\n'
    )
    engine._procs = dict(stub.procs)
    engine.variables = {
        "profile_key": "weekly", "batch_state": {},
        "batch_unit_members": [{"username": "u1"}, {"username": "u2"}],
    }
    try:
        engine._exec_body(program.body)
    except _ReturnSignal as signal:
        seen.append(signal.value)
    assert seen[0]["username"] == "u2"
    assert "retry_after" not in seen[0]
