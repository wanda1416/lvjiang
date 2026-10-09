"""登录更新保留账号、重选目标角色，且不混用普通登录与异常恢复的在线策略。"""

import pytest

from lvjiang.core.config.resolver import SYSTEM_CONFIG_DIR
from lvjiang.core.layout_manager import load_layout_by_key
from lvjiang.workflows.builtins import _registry
from lvjiang.workflows.grammar import parse_file, parse_text
from tests.workflows.conftest import make_engine


def _run(monkeypatch, *, platform="android", trigger="role", recovery=False,
         online_after_update=False, startup_ready=True):
    engine = make_engine(run_env=platform, layout=load_layout_by_key(platform))
    for name in ("subcall/login.wf", "subcall/page_detection.wf",
                 "subcall/navigation.wf", "batch/recover_to_login.wf"):
        engine._procs.update(parse_file(SYSTEM_CONFIG_DIR / "workflows" / name).procs)
    state = {"account": "account", "role": "previous_role", "page_state": 1}
    engine.variables["batch_state"] = state
    engine.user_attributes_snapshot = {"user": {"role": "target_role", "role_index": 2}}
    screen = {"page": "base", "updated": False, "online_seen": False}
    actions = []

    def running(*args):
        return True

    running._inject = None
    monkeypatch.setitem(_registry._FUNCTION_REGISTRY, "app_is_running", running)

    def app(node):
        actions.append("app_" + node.action)
        if node.action == "start":
            screen["page"] = "users" if platform == "desktop" else "startup"

    def click(node):
        target = node.target
        key = target.panel if hasattr(target, "panel") else target.entity
        actions.append(key)
        if key == "switch_role":
            if trigger == "role" and not screen["updated"]:
                screen.update(page="update", updated=True)
            elif online_after_update and not screen["online_seen"]:
                screen.update(page="online", online_seen=True)
            else:
                screen["page"] = "roles"
        elif key == "current_role":
            screen.update(page="update", updated=True)
        elif key == "confirm":
            screen["page"] = "roles" if screen["page"] == "online" else "off"
        elif key == "cancel":
            screen["page"] = "base"
        elif key == "login":
            screen["page"] = "startup"
        elif key == "back":
            screen["page"] = "base"
        elif key == "role_list":
            assert engine._resolve(target.row) == 2
        elif key == "enter":
            if trigger == "loading" and not screen["updated"]:
                screen.update(page="update", updated=True)
            else:
                screen["page"] = "main"

    def scan(node):
        page = screen["page"]
        keys = [field.value for field in node.fields]
        text = "当前有补丁可以更新，请关闭游戏重新登录。" if page == "update" else (
            "其他角色在线" if page == "online" else "")
        values = {
            "online_label": text, "loading": "", "current_role": "previous_role",
            "login": "进入游戏" if page == "users" else "",
            "switch_role": "选择角色" if page == "base" else "",
            "enter": "进入游戏" if page == "roles" else "",
            "yysls_logo": "logo" if page == "startup" and startup_ready else "",
            "menu": "菜单" if page == "main" else "",
        }
        engine.variables[node.target.name] = (
            next((values.get(key, "") for key in keys if values.get(key)), "")
            if node.by else {key: values.get(key, "") for key in keys})

    monkeypatch.setattr(engine, "_exec_android_app", app)
    monkeypatch.setattr(engine, "_exec_click", click)
    monkeypatch.setattr(engine, "_exec_scan", scan)
    monkeypatch.setattr(engine, "_exec_wait", lambda node: None)
    monkeypatch.setattr(engine, "_exec_screenshot", lambda **kwargs: None)
    if not startup_ready:
        engine._procs.update(parse_text('def leave_startup_page()\nreturn 0\nend\n').procs)
    name = "relogin_current_role" if recovery else "select_role"
    args = ["user", state] if recovery else [2, "target_role", state, True, 0]
    result = engine._run_proc(engine._procs[name], args)[0]
    return result, state, actions


@pytest.mark.parametrize("platform", ["android", "desktop"])
def test_update_restarts_original_account_and_selects_target_role(monkeypatch, platform):
    result, state, actions = _run(monkeypatch, platform=platform)
    assert result["status"] == "success"
    assert state == {"account": "account", "role": "target_role", "page_state": 2}
    assert actions[:4] == ["switch_role", "confirm", "app_stop", "app_start"]
    assert actions.count("login") == int(platform == "desktop")
    assert actions.count("role_list") == 1 and actions[-1] == "enter"
    assert "tap_user" not in actions and "cancel" not in actions


def test_update_after_entering_game_reselects_role(monkeypatch):
    result, state, actions = _run(monkeypatch, trigger="loading")
    assert result["status"] == "success"
    assert actions.count("role_list") == actions.count("enter") == 2
    assert actions.count("app_start") == 1 and state["account"] == "account"


def test_update_then_online_keeps_normal_skip_policy(monkeypatch):
    result, state, actions = _run(monkeypatch, online_after_update=True)
    assert result["status"] == "skipped" and result["message"] == "角色在线"
    assert actions.count("confirm") == 1 and actions[-1] == "cancel"
    assert state == {"account": "account", "role": "", "page_state": 1}


def test_recovery_update_reselects_role_and_keeps_online_takeover(monkeypatch):
    result, state, actions = _run(
        monkeypatch, platform="desktop", recovery=True, online_after_update=True)
    assert result == 1 and state["role"] == "target_role"
    assert actions.count("confirm") == 2 and "cancel" not in actions
    assert "tap_user" not in actions and actions.count("role_list") == 1


def test_failed_update_recovery_never_claims_login_success(monkeypatch):
    result, state, actions = _run(monkeypatch, startup_ready=False)
    assert result["status"] == "failed"
    assert state == {"account": "account", "role": "", "page_state": 0}
    assert "role_list" not in actions and "cancel" not in actions
