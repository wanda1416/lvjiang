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
        '    eval $state.login_args = [$skip, $wait, $roll, $restart]\n'
        '    return {"status": "success", "state": $state}\n'
        'end\n'
    )
    engine._procs = dict(stub.procs)
    engine.variables = {
        "profile_key": "weekly", "batch_state": {},
        "batch_unit_members": [{"username": "u1"}, {"username": "u2"}],
        # 批量层按 wf 声明的参数注入；写死这几个值会让用户配置失效。
        "skip_online_role": False, "online_role_max_wait": 30,
        "max_roll_account": 12, "allow_restart_app": False,
    }
    try:
        engine._exec_body(program.body)
    except _ReturnSignal as signal:
        seen.append(signal.value)
    assert seen[0]["username"] == "u2"
    assert "retry_after" not in seen[0]
    assert seen[0]["state"]["login_args"] == [False, 30, 12, False]


def test_declares_the_same_login_parameters_as_prepare_item():
    """两个条目准备 wf 透传同一组登录参数；漏声明会让配置静默走默认值。"""
    from lvjiang.workflows.metadata import metadata_for_script_config

    def names(path: Path) -> set[str]:
        metadata, _warning = metadata_for_script_config(path)
        return {item["name"] for item in metadata.get("parameters") or []}

    shared = names(Path("config/system/workflows/batch/prepare_item.wf"))
    assert shared <= names(_PATH)
