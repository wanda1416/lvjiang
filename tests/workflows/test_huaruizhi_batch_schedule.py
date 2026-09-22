"""花蕊织批量判定与通用生命周期工作流的行为回归。

业务 wf 的批量检查在准备前读取进度；通用条目准备按参数选择登录页恢复：
进度已满直接跳过整个用户、
已经在登录页就直接登录、进程不在时启动、进程仍在但不在登录页时重启。

启动后不等稳定帧——登录页背景动画常驻，根本不存在稳定帧；启动页
通过游戏 logo 判定，返回按钮只用来点击。
"""

from pathlib import Path

import pytest

from lvjiang.core.config import load_user_config
from lvjiang.core.config.resolver import SYSTEM_CONFIG_DIR
from lvjiang.core.layout_manager import load_layout_by_key
from lvjiang.workflows.engine.signals import _ReturnSignal
from lvjiang.workflows.grammar import parse_text
from lvjiang.workflows.metadata import parse_metadata_file
from tests.workflows.conftest import make_engine

_BATCH_DIR: Path = SYSTEM_CONFIG_DIR / "workflows" / "batch"
_PREPARE = _BATCH_DIR / "prepare_item.wf"
_HUARUIZHI = SYSTEM_CONFIG_DIR / "workflows" / "weekly_huaruizhi.wf"
_LOGIN = SYSTEM_CONFIG_DIR / "workflows" / "subcall" / "login.wf"
_FINISH = _BATCH_DIR / "finish_item.wf"

# 角色选择由 login.wf 的专项用例覆盖；这里保留真实的准备与恢复过程。
_STUB_SELECT_ROLE = (
    'def select_role($index, $role, $state, $skip_online, $max_wait)\n'
    '    return {"status": "success", "message": "", "state": $state}\n'
    'end\n'
)
_STUB_EXIT_TO_LOGIN = (
    'def exit_to_login()\n'
    '    eval mark_exit()\n'
    'end\n'
)
_STUB_DECLARE_PROFILES = (
    'def declare_profiles($keys)\n'
    '    return 0\n'
    'end\n'
)


class _Device:
    """记录对客户端进程和画面的全部动作。"""

    def __init__(self, *, app_running: bool, weekly_progress, startup_back=True,
                 in_login_page: bool = False, manual_recovery: bool = True):
        self.app_running = app_running
        self.weekly_progress = weekly_progress
        self.startup_back = startup_back
        self.in_login_page = in_login_page
        self.manual_recovery = manual_recovery
        self.started = False
        self.calls: list[tuple] = []

    def install(self, engine) -> None:
        workflow = engine._ensure_workflow()
        passthrough = workflow.call_function

        def call_function(name, args, engine=None):
            self.calls.append((name, *args))
            if name == "profile_get":
                return self.weekly_progress
            if name == "app_is_running":
                return self.app_running
            if name in ("app_start", "app_stop"):
                self.app_running = name == "app_start"
                if name == "app_start":
                    self.started = True
                return True
            if name in ("mark_exit", "pause"):
                if name == "pause" and self.manual_recovery:
                    self.in_login_page = True  # 模拟用户手动回到登录主页
                return None
            return passthrough(name, args, engine=engine)

        workflow.call_function = call_function
        workflow.match_region_templates = self._match_templates
        workflow.click_region = self._click
        workflow.ocr_scene_by = self._ocr
        workflow.wait_seconds = lambda seconds: None

    def _click(self, scene, key, **kwargs):
        self.calls.append(("click", scene, key))
        if (scene, key) == ("game_login_page", "back"):
            self.in_login_page = True

    def _ocr(self, scene, keys, value, mode, **kwargs):
        if self.in_login_page and (scene, "switch_role") in [
                (scene, key) for key in keys]:
            return "选择角色"
        return ""

    def _match_templates(self, regions, **kwargs):
        self.calls.append(("scan_image", tuple(r.key for r in regions)))
        if not self.startup_back or not self.started or self.in_login_page:
            return {}
        return {region.key: {"score": 0.9} for region in regions}

    def names(self) -> list:
        """只保留会改变现场的动作，忽略 log/日志类调用。"""
        watched = {
            "app_is_running", "app_start", "app_stop",
            "profile_get", "pause", "click", "scan_image",
        }
        return [call[0] for call in self.calls if call[0] in watched]


def _run_batch_check(device: _Device) -> dict:
    """只调用业务 wf 声明的批量检查，不执行顶层正文。"""
    program = parse_text(_HUARUIZHI.read_text(encoding="utf-8"))
    engine = make_engine(
        layout=load_layout_by_key("android"),
        delay_params=load_user_config().delay_params,
        run_env="android",
    )
    device.install(engine)
    engine._procs = dict(program.procs)
    engine._procs["declare_profiles"] = parse_text(
        _STUB_DECLARE_PROFILES).procs["declare_profiles"]
    return_value, _output = engine._run_proc(
        program.procs["check_huaruizhi_batch"], [{}])
    return return_value


def _run_prepare(device: _Device, *, restart_app: bool) -> dict:
    """执行通用 prepare_item.wf，返回它的批量生命周期协议字典。"""
    program = parse_text(_PREPARE.read_text(encoding="utf-8"))
    engine = make_engine(
        layout=load_layout_by_key("android"),
        delay_params=load_user_config().delay_params,
        run_env="android",
    )
    device.install(engine)
    engine._procs = dict(program.procs)
    login_program = parse_text(_LOGIN.read_text(encoding="utf-8"))
    engine._procs.update(dict(login_program.procs))
    engine._procs["select_role"] = parse_text(_STUB_SELECT_ROLE).procs[
        "select_role"]
    engine.user_attributes_snapshot = {"u1": {
        "account": "acc", "role": "role", "role_index": "1", "tail": "1234",
    }}
    engine.variables = {
        "batch_users": ["u1"], "batch_index": 0,
        "batch_state": {"account": "acc"},
        "skip_online_role": True, "online_role_max_wait": 0,
        "allow_restart_app": restart_app,
    }
    try:
        engine._exec_body(program.body)
    except _ReturnSignal as signal:
        return signal.value
    raise AssertionError("条目准备必须返回批量生命周期协议字典")


def _run_finish(device: _Device, *, stop_app: bool) -> dict:
    """执行 finish_item.wf，用桩记录默认退出分支。"""
    program = parse_text(_FINISH.read_text(encoding="utf-8"))
    engine = make_engine(
        layout=load_layout_by_key("android"),
        delay_params=load_user_config().delay_params,
        run_env="android",
    )
    device.install(engine)
    engine._procs = dict(program.procs)
    engine._procs["exit_to_login"] = parse_text(
        _STUB_EXIT_TO_LOGIN).procs["exit_to_login"]
    engine.variables = {"batch_state": {}, "stop_app": stop_app}
    try:
        engine._exec_body(program.body)
    except _ReturnSignal as signal:
        return signal.value
    raise AssertionError("条目收尾必须返回批量生命周期协议字典")


def test_full_weekly_progress_skips_without_touching_the_client():
    """进度已满：直接跳过，连进程状态都不必查，更不能启动客户端。"""
    device = _Device(app_running=False, weekly_progress=3000)
    result = _run_batch_check(device)

    assert result["status"] == "skipped"
    assert device.names() == ["profile_get"]


def test_login_page_skips_client_process_operations():
    """已经在登录页：不查询进程，也不重启客户端。"""
    device = _Device(
        app_running=True, weekly_progress=1200, in_login_page=True)
    result = _run_prepare(device, restart_app=True)

    assert result["status"] == "success"
    assert device.names() == []


def test_prepare_keeps_legacy_manual_recovery_when_restart_is_disabled():
    device = _Device(app_running=True, weekly_progress=0)

    result = _run_prepare(device, restart_app=False)

    assert result["status"] == "success"
    assert device.names() == ["scan_image", "pause"]


def test_stopped_client_is_started_then_returned_to_login_page():
    """客户端不在：启动后识别 logo，点击返回并确认登录主页。"""
    device = _Device(app_running=False, weekly_progress=0)
    result = _run_prepare(device, restart_app=True)

    assert result["status"] == "success"
    assert device.names() == [
        "scan_image", "app_is_running", "app_start",
        "scan_image", "click",
    ]
    assert ("click", "game_login_page", "back") in device.calls
    assert ("scan_image", ("yysls_logo",)) in device.calls


def test_running_client_outside_login_page_is_restarted():
    """进程存在不代表在登录页；其他页面必须重启以恢复确定的登录现场。"""
    device = _Device(app_running=True, weekly_progress=1200)
    result = _run_prepare(device, restart_app=True)

    assert result["status"] == "success"
    assert device.names() == [
        "scan_image", "app_is_running", "app_stop",
        "app_start", "scan_image", "click",
    ]


def test_startup_logo_never_appears_falls_back_to_manual():
    """一直找不到启动页 logo 时必须停下来求助，不得盲点返回。"""
    device = _Device(
        app_running=False, weekly_progress=0, startup_back=False,
        manual_recovery=False)
    result = _run_prepare(device, restart_app=True)

    assert result["status"] == "failed"
    assert "pause" in device.names()
    assert ("click", "game_login_page", "back") not in device.calls


def test_finish_item_defaults_to_exit_to_login():
    device = _Device(app_running=True, weekly_progress=0)
    result = _run_finish(device, stop_app=False)

    assert ("mark_exit",) in device.calls
    assert "app_stop" not in device.names()
    assert result["state"]["page_state"] == 1
    assert result["state"]["role"] == ""


def test_finish_item_can_stop_app_instead_of_exiting():
    device = _Device(app_running=True, weekly_progress=0)
    result = _run_finish(device, stop_app=True)

    assert ("mark_exit",) not in device.calls
    assert device.names() == ["app_stop"]
    assert result["state"]["page_state"] == 0
    assert result["state"]["role"] == ""


@pytest.mark.parametrize("path", [_PREPARE, _FINISH])
def test_declared_parameters_match_the_variables_used(path):
    """`#% parameters` 声明的名字必须和脚本里 `$name` 的用法对得上。

    运行时由批量层按名字注入，声明改名而正文没改（或反过来）不会报错，
    只会让参数静默变成 null —— 跳过策略直接失效且没有任何提示。
    """
    source = path.read_text(encoding="utf-8")
    declared = {
        item["name"] for item in parse_metadata_file(path).get("parameters") or []
    }
    for name in declared:
        assert f"${name}" in source, f"{path.name} 声明了 {name} 却没有使用"


def test_huaruizhi_metadata_points_to_declared_batch_check():
    metadata = parse_metadata_file(_HUARUIZHI)
    program = parse_text(_HUARUIZHI.read_text(encoding="utf-8"))

    assert metadata["batch_check"] == "check_huaruizhi_batch"
    assert metadata["batch_check"] in program.procs
