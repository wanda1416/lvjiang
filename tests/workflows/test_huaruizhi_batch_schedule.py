"""花蕊织轮次调度生命周期工作流的行为回归。

条目准备要在四种现场下做出不同动作，这里逐个回放：进度已满直接跳过、
已经在登录页就直接登录、进程不在时启动、进程仍在但不在登录页时重启。

启动后不等稳定帧——登录页背景动画常驻，根本不存在稳定帧，只能轮询
启动页的返回按钮。这条由下面的用例锁住。
"""

from pathlib import Path

import pytest

from lvjiang.core.config import load_user_config
from lvjiang.core.config.resolver import SYSTEM_CONFIG_DIR
from lvjiang.core.layout_manager import load_layout_by_name
from lvjiang.workflows.engine.signals import _ReturnSignal
from lvjiang.workflows.grammar import parse_text
from lvjiang.workflows.metadata import parse_metadata_file
from tests.workflows.conftest import make_engine

_BATCH_DIR: Path = SYSTEM_CONFIG_DIR / "workflows" / "batch"
_PREPARE = _BATCH_DIR / "prepare_huaruizhi.wf"
_FINISH = _BATCH_DIR / "finish_huaruizhi.wf"

# 真实过程要连设备，替换成只记账的桩：登录本身由 login.wf 的用例覆盖。
_STUB_PREPARE_USER = (
    'def prepare_user($username, $state, $skip_online, $max_wait)\n'
    '    return {"status": "success", "message": "", "state": $state}\n'
    'end\n'
)
class _Device:
    """记录对客户端进程和画面的全部动作。"""

    def __init__(self, *, app_running: bool, weekly_progress, startup_back=True,
                 in_login_page: bool = False):
        self.app_running = app_running
        self.weekly_progress = weekly_progress
        self.startup_back = startup_back
        self.in_login_page = in_login_page
        self.calls: list[tuple] = []

    def install(self, engine) -> None:
        workflow = engine._ensure_workflow()
        passthrough = workflow.call_function

        def call_function(name, args, engine=None):
            self.calls.append((name, *args))
            if name == "profile_get":
                return self.weekly_progress
            if name == "android_app_running":
                return self.app_running
            if name in ("android_app_start", "android_app_stop"):
                self.app_running = name == "android_app_start"
                return True
            if name == "pause":
                return None
            return passthrough(name, args, engine=engine)

        workflow.call_function = call_function
        workflow.match_region_templates = self._match_templates
        workflow.click_region = lambda scene, key, **kw: self.calls.append(
            ("click", scene, key))
        workflow.wait_seconds = lambda seconds: None

    def _match_templates(self, regions, **kwargs):
        self.calls.append(("scan_image", tuple(r.key for r in regions)))
        if not self.startup_back:
            return {}
        return {region.key: {"score": 0.9} for region in regions}

    def names(self) -> list:
        """只保留会改变现场的动作，忽略 log/日志类调用。"""
        watched = {
            "android_app_running", "android_app_start", "android_app_stop",
            "profile_get", "pause", "click", "scan_image",
        }
        return [call[0] for call in self.calls if call[0] in watched]


def _run_prepare(device: _Device) -> dict:
    """执行 prepare_huaruizhi.wf 的主流程，返回它的返回协议字典。"""
    program = parse_text(_PREPARE.read_text(encoding="utf-8"))
    engine = make_engine(
        layout=load_layout_by_name("默认布局"),
        delay_params=load_user_config().delay_params,
        run_env="android",
    )
    device.install(engine)
    engine._procs = dict(program.procs)
    engine._procs["prepare_user"] = parse_text(_STUB_PREPARE_USER).procs[
        "prepare_user"]
    login_page_result = 1 if device.in_login_page else 0
    login_page_check = parse_text(
        "def is_in_login_page()\n"
        f"    return {login_page_result}\n"
        "end\n"
    )
    engine._procs["is_in_login_page"] = login_page_check.procs[
        "is_in_login_page"]
    engine.variables = {
        "batch_users": ["u1"], "batch_index": 0, "batch_state": {},
        "skip_online_role": True, "online_role_max_wait": 0,
    }
    try:
        engine._exec_body(program.body)
    except _ReturnSignal as signal:
        return signal.value
    raise AssertionError("条目准备必须返回批量生命周期协议字典")


def test_full_weekly_progress_skips_without_touching_the_client():
    """进度已满：直接跳过，连进程状态都不必查，更不能启动客户端。"""
    device = _Device(app_running=False, weekly_progress=3000)
    result = _run_prepare(device)

    assert result["status"] == "skipped"
    assert device.names() == ["profile_get"]


def test_login_page_skips_client_process_operations():
    """已经在登录页：不查询进程，也不重启客户端。"""
    device = _Device(
        app_running=True, weekly_progress=1200, in_login_page=True)
    result = _run_prepare(device)

    assert result["status"] == "success"
    assert device.names() == ["profile_get"]


def test_stopped_client_is_started_then_returned_to_login_page():
    """客户端不在：启动后轮询启动页返回按钮并点它，回到登录页。"""
    device = _Device(app_running=False, weekly_progress=0)
    result = _run_prepare(device)

    assert result["status"] == "success"
    assert device.names() == [
        "profile_get", "android_app_running", "android_app_start",
        "scan_image", "click",
    ]
    assert ("click", "game_login_page", "back") in device.calls


def test_running_client_outside_login_page_is_restarted():
    """进程存在不代表在登录页；其他页面必须重启以恢复确定的登录现场。"""
    device = _Device(app_running=True, weekly_progress=1200)
    result = _run_prepare(device)

    assert result["status"] == "success"
    assert device.names() == [
        "profile_get", "android_app_running", "android_app_stop",
        "android_app_start", "scan_image", "click",
    ]


def test_startup_back_button_never_appears_falls_back_to_manual():
    """一直找不到返回按钮时必须停下来求助，而不是继续往登录流程里冲。"""
    device = _Device(
        app_running=False, weekly_progress=0, startup_back=False)
    _run_prepare(device)

    assert "pause" in device.names()
    assert ("click", "game_login_page", "back") not in device.calls


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
