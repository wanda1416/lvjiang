"""批量登录子过程的行为回归。

这些用例直接加载生产 `subcall/login.wf` 里的过程执行，而不是在源码文本里找
子串——文本断言锁的是代码长什么样，改个等待秒数、换个写法就误报，却拦不住
真正的行为回归（`wait_until_role_offline` 忘了点取消、返回码写反）。

OCR 与点击通过替换 BaseWorkflow 上的两个方法注入：布局取真实的android，
所以区域 key 拼错同样会在这里炸出来。
"""

import time
from pathlib import Path

import pytest

from lvjiang.core.config import load_user_config
from lvjiang.core.config.resolver import SYSTEM_CONFIG_DIR
from lvjiang.core.layout_manager import load_layout_by_key
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine

_LOGIN_WF: Path = SYSTEM_CONFIG_DIR / "workflows" / "subcall" / "login.wf"

_CANCEL = ("game_login_page", "cancel")
_ENTRY = ("game_login_page", "switch_role")


class _Screen:
    """按 (场景, 区域) 给出 OCR 文本，并记录实际点击。

    `online_times` 控制「其他角色在线」还会被读到几次，用来回放“等着等着
    角色就下线了”这种时序；耗尽后该区域读为空。
    """

    def __init__(self, texts: dict[tuple[str, str], str] | None = None,
                 *, online_times: int = 0):
        self._texts = dict(texts or {})
        self.online_times = online_times
        self.clicks: list[tuple[str, str]] = []
        self.waits: list[float] = []

    def install(self, engine) -> None:
        workflow = engine._ensure_workflow()
        workflow.ocr_scene_by = self._ocr
        workflow.click_region = self._click
        workflow.wait_seconds = self.waits.append

    def _ocr(self, scene, keys, value, mode, **kwargs):
        for key in keys:
            if (scene, key) == ("game_login_page", "online_label"):
                if self.online_times <= 0:
                    continue
                self.online_times = self.online_times - 1
                return "其他角色在线"
            text = self._texts.get((scene, key), "")
            if text and (mode != "contains" or str(value) in text):
                return text
        return ""

    def _click(self, scene, key, **kwargs):
        self.clicks.append((scene, key))


def _engine():
    """真实android + 真实等待参数；`wait @page_refresh` 比对的就是后者。"""
    return make_engine(
        layout=load_layout_by_key("android"),
        delay_params=load_user_config().delay_params,
        run_env="android",
    )


def _procs() -> dict:
    return dict(parse_text(_LOGIN_WF.read_text(encoding="utf-8")).procs)


def _wait_until_offline(*, skip: bool, deadline: float, online_times: int):
    """回放 wait_until_role_offline，返回 (返回码, batch_state, 画面记录)。"""
    screen = _Screen(online_times=online_times)
    engine = _engine()
    screen.install(engine)
    engine._procs = _procs()
    engine.variables = {
        "batch_state": {"account": "acc", "role": "role", "page_state": 2},
        "skip_online_role": skip,
        "deadline": deadline,
    }
    engine._exec_body(parse_text(
        'call $result = wait_until_role_offline('
        '$batch_state, $skip_online_role, $deadline)\n'
    ).body)
    return engine.variables["result"], engine.variables["batch_state"], screen


def test_role_not_online_leaves_state_untouched():
    """没有在线提示时不点任何东西，也不动 batch_state。"""
    result, state, screen = _wait_until_offline(
        skip=True, deadline=0, online_times=0)

    assert result == 0
    assert screen.clicks == []
    assert state["page_state"] == 2


def test_online_role_cancels_prompt_before_skipping():
    """跳过模式下必须先取消弹窗——弹窗留着会挡住下一个用户的登录页判定。"""
    result, state, screen = _wait_until_offline(
        skip=True, deadline=0, online_times=1)

    assert result == 1
    assert screen.clicks == [_CANCEL]
    # 复位回登录页状态，调用方据此以 skipped 收尾
    assert state["page_state"] == 1
    assert state["role"] == ""


def test_waiting_reclicks_the_entry_until_the_role_goes_offline():
    """等待模式下自己重试到角色下线：取消弹窗 → 等一会儿 → 重新点回入口。

    重新点击是必须的：在线提示只在点过角色入口之后才会出现，不重新点就永远
    读不到它已经消失，会一路空等到期限。
    """
    result, state, screen = _wait_until_offline(
        skip=False, deadline=time.time() + 3600, online_times=2)

    assert result == 0
    assert screen.clicks == [_CANCEL, _ENTRY, _CANCEL, _ENTRY]
    assert len(screen.waits) >= 2, "每次重试前都要等一段时间，不能空转"
    # 等到了可登录，不该留下“已放弃”的状态
    assert state["page_state"] == 2


def test_online_role_gives_up_after_deadline():
    """等待模式超过期限后按跳过处理，不能无限等下去。"""
    result, state, screen = _wait_until_offline(
        skip=False, deadline=0, online_times=99)

    assert result == 1
    assert screen.clicks == [_CANCEL]
    assert state["page_state"] == 1


@pytest.mark.parametrize("missing", ["account", "role", "role_index"])
def test_prepare_user_fails_fast_on_incomplete_user_profile(missing):
    """用户资料缺字段时立即失败，绝不能带着空值往下走去点账号列表。"""
    attributes = {
        "account": "acc", "role": "role", "role_index": "1", "tail": "1234",
    }
    attributes.pop(missing)
    screen = _Screen()
    engine = _engine()
    screen.install(engine)
    engine.user_attributes_snapshot = {"u1": attributes}
    engine.variables = {"batch_state": {}}
    engine._procs = _procs()
    engine._exec_body(parse_text(
        'call $result = prepare_user("u1", $batch_state, true, 0)\n').body)

    assert engine.variables["result"]["status"] == "failed"
    assert screen.clicks == []


def _all_procs() -> dict:
    """login.wf 会调用 page_detection/navigation 里的过程，一并注册。"""
    procs = {}
    for name in ("subcall/page_detection.wf", "subcall/navigation.wf",
                 "subcall/login.wf"):
        path = SYSTEM_CONFIG_DIR / "workflows" / name
        procs.update(parse_text(path.read_text(encoding="utf-8")).procs)
    return procs


def test_login_always_goes_through_the_role_selection_page():
    """登录必须走「切换角色」→ 角色列表 → 进入游戏，不走当前角色快捷入口。

    快捷入口点的是登录页上停留的角色：同账号多角色时，它会在目标角色还没
    选中的情况下直接进游戏，静默进错角色，还把 batch_state.role 记成目标。
    """
    screen = _Screen({
        # 角色列表已就绪（is_in_role_select_page 认「进入游戏」）
        ("game_login_page", "enter"): "进入游戏",
        # login_to_main_page：加载条已消失，主页入口可读
        ("game_main_page", "menu"): "菜单",
    })
    engine = _engine()
    screen.install(engine)
    # 面板格子点击走引擎自己的坐标换算（需要真实 input_sim），
    # 这里在换算入口截下来只记账，不去碰输入后端。
    def panel_cell(scene, panel, row, col, **kwargs):
        screen.clicks.append((scene, f"{panel}[{row}][{col}]"))
        return None, None

    engine._panel_cell_to_screen = panel_cell
    engine._procs = _all_procs()
    engine.variables = {"batch_state": {"account": "acc", "page_state": 1}}
    engine._exec_body(parse_text(
        'call $result = select_role(2, "role", $batch_state, true, 0)\n').body)

    assert engine.variables["result"]["status"] == "success"
    assert screen.clicks == [
        ("game_login_page", "switch_role"),
        ("game_login_page", "role_list[2][1]"),
        ("game_login_page", "enter"),
    ]
    assert engine.variables["batch_state"]["page_state"] == 2


def _run_login_prepare(*, platform="desktop", initial_page="other",
                       users_ready=True, logo_ready=True, restart=True,
                       state_account="", user_found=True):
    """用页面状态回放真实 DSL 的启动、选账号与返回路径。"""
    engine = make_engine(
        layout=load_layout_by_key(platform),
        delay_params=load_user_config().delay_params,
        run_env=platform,
    )
    engine._procs = _all_procs()
    engine._procs["select_role"] = parse_text(
        'def select_role($index, $role, $state, $skip, $wait)\n'
        '    return {"status": "success", "message": "", "state": $state}\n'
        'end\n').procs["select_role"]
    engine.user_attributes_snapshot = {"u1": {
        "account": "acc", "role": "role", "role_index": "1", "tail": "1234",
    }}
    state = {"account": state_account, "role": "", "page_state": 0}
    engine.variables = {"batch_state": state}
    page = initial_page
    actions = []
    prompts: list[str] = []
    workflow = engine._ensure_workflow()
    original_call = workflow.call_function

    def call_function(name, args, engine=None):
        nonlocal page
        if name == "app_is_running":
            actions.append("app_is_running")
            return False
        if name == "app_start":
            actions.append("app_start")
            page = "users" if platform == "desktop" else "startup"
            return True
        if name == "pause":
            actions.append("pause")
            prompts.append(str(args[0]) if args else "")
            return ""
        return original_call(name, args, engine=engine)

    def fake_scan(node):
        key = node.fields[0].value if node.fields else ""
        matches = {
            "switch_role": page == "base",
            "switch_user": page == "accounts",
            "yysls_logo": page == "startup" and logo_ready,
            "login": page == "users" and users_ready,
        }
        engine.variables[node.target.name] = key if matches.get(key) else ""

    def fake_click(node):
        nonlocal page
        target = node.target
        key = getattr(target, "entity", getattr(target, "name", ""))
        actions.append(key)
        if key == "user_icon":
            page = ("users" if platform == "desktop" and
                    "switch_user" in actions else "accounts")
        elif key == "switch_user":
            page = "users" if platform == "android" else "base"
        elif key == "login":
            # 只有 PC 重启后的首次账号登录进入启动页；普通账号切换和
            # Android 登录都直接回到登录主页。
            page = ("startup" if platform == "desktop" and
                    "app_start" in actions else "base")
        elif key == "back":
            page = "base"

    def fake_find(node):
        # 账号列表翻遍也没命中目标尾号，是需要人工接手的典型现场。
        engine.variables[node.var_name] = "1234" if user_found else ""

    workflow.call_function = call_function
    engine._exec_scan = fake_scan
    engine._exec_click = fake_click
    engine._exec_find = fake_find
    engine._exec_wait = lambda _node: None
    engine._exec_body(parse_text(
        f'call $result = prepare_user("u1", $batch_state, true, 0, 1, '
        f'{str(restart).lower()})\n').body)
    return engine.variables["result"], state, actions, prompts


def test_pc_restart_uses_existing_users_view_and_logs_in_before_role_selection():
    result, state, actions, _prompts = _run_login_prepare()

    assert result["status"] == "success"
    assert actions == ["app_is_running", "app_start", "tap_user",
                       "found_user", "login", "back"]
    assert state == {"account": "acc", "role": "", "page_state": 1}


def test_pc_non_restart_account_switch_never_checks_or_leaves_startup_page():
    result, state, actions, _prompts = _run_login_prepare(
        initial_page="base", restart=False, state_account="other")

    assert result["status"] == "success"
    assert actions == ["more_user", "user_icon", "switch_user", "user_icon",
                       "tap_user", "found_user", "login"]
    assert state == {"account": "acc", "role": "", "page_state": 1}


def test_android_account_switch_keeps_direct_login_path():
    result, state, actions, _prompts = _run_login_prepare(
        platform="android", initial_page="base", restart=False,
        state_account="other")

    assert result["status"] == "success"
    assert actions == ["more_user", "user_icon", "switch_user",
                       "tap_user", "found_user", "login"]
    assert state == {"account": "acc", "role": "", "page_state": 1}


def test_android_restart_keeps_original_startup_then_account_switch_path():
    result, state, actions, _prompts = _run_login_prepare(platform="android")

    assert result["status"] == "success"
    assert actions == ["app_is_running", "app_start", "back", "more_user",
                       "user_icon", "switch_user", "tap_user", "found_user", "login"]
    assert state == {"account": "acc", "role": "", "page_state": 1}


@pytest.mark.parametrize("missing", ["users", "logo"])
def test_pc_restart_missing_page_pauses_without_committing_account(missing):
    result, state, actions, _prompts = _run_login_prepare(
        users_ready=missing != "users", logo_ready=missing != "logo")

    assert result["status"] == "failed"
    assert "pause" in actions
    assert state == {"account": "", "role": "", "page_state": 0}
    if missing == "users":
        assert "tap_user" not in actions
    else:
        assert "back" not in actions


def test_startup_page_at_entry_returns_to_login_without_restarting():
    result, _state, actions, _prompts = _run_login_prepare(
        initial_page="startup", restart=False, state_account="acc")

    assert result["status"] == "success"
    assert actions == ["back"]


@pytest.mark.parametrize("scenario", [
    # 账号列表里找不到目标尾号：人工要照着提示选中正确账号
    {"user_found": False, "initial_page": "base", "restart": False,
     "state_account": "other"},
    # 起点既不在登录页也不在启动页，且不允许重启
    {"initial_page": "other", "restart": False},
])
def test_manual_pause_prompts_name_the_account_and_role_to_log_in(scenario):
    """停下来求助时必须写清该登录哪个账号、哪个角色。

    这条流程不对人工操作做二次校验——选没选对只有人能判断。那么提示就是
    唯一的依据：批量里多个用户轮流跑，隔几分钟回来看到一句「请手动选定账号」
    根本没法操作，照着当前屏幕随手点一个就会在错账号上跑完整个任务。
    """
    _result, _state, actions, prompts = _run_login_prepare(**scenario)

    assert "pause" in actions
    joined = " | ".join(prompts)
    assert "acc" in joined, joined
    assert "role" in joined, joined
