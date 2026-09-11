"""批量登录子过程的行为回归。

这些用例直接加载生产 `subcall/login.wf` 里的过程执行，而不是在源码文本里找
子串——文本断言锁的是代码长什么样，改个等待秒数、换个写法就误报，却拦不住
真正的行为回归（`wait_until_role_offline` 忘了点取消、返回码写反）。

OCR 与点击通过替换 BaseWorkflow 上的两个方法注入：布局取真实的默认布局，
所以区域 key 拼错同样会在这里炸出来。
"""

import time
from pathlib import Path

import pytest

from lvjiang.core.config import load_user_config
from lvjiang.core.config.resolver import SYSTEM_CONFIG_DIR
from lvjiang.core.layout_manager import load_layout_by_name
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
    """真实默认布局 + 真实等待参数；`wait @page_refresh` 比对的就是后者。"""
    return make_engine(
        layout=load_layout_by_name("默认布局"),
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
