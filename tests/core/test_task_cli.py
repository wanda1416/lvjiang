"""命令行入口的参数解析、脚本解析与无界面交互契约。

这些用例只覆盖纯函数与分流逻辑，不启动运行时，也不读写 config/session。
"""
from __future__ import annotations

import argparse
import json

import pytest

from lvjiang.core.task_cli import (
    EXIT_INTERRUPTED,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_USAGE,
    CliError,
    HeadlessCallbacks,
    HeadlessPause,
    RunRequest,
    _console_level,
    _list_scripts,
    _outcome,
    _print_schema,
    apply_overrides,
    build_parser,
    cli_main,
    coerce_param,
    find_script,
    parse_assignments,
    resolve_selection,
)

SCRIPTS = [
    {"id": "haoyun_lianji", "name": "好运连叽摇骰子", "wf_file": "activity/好运连叽.wf",
     "parameters": [], "env": ["android"], "scope": "daily", "runnable": True},
    {"id": "other", "name": "另一个脚本", "wf_file": "other.wf",
     "parameters": [], "env": [], "scope": "daily", "runnable": True},
]


def test_find_script_by_id_and_name():
    assert find_script(SCRIPTS, "haoyun_lianji")["id"] == "haoyun_lianji"
    assert find_script(SCRIPTS, "好运连叽摇骰子")["id"] == "haoyun_lianji"


def test_find_script_reports_missing_and_duplicate_names():
    with pytest.raises(CliError) as missing:
        find_script(SCRIPTS, "不存在")
    assert missing.value.code == EXIT_NOT_FOUND
    duplicated = SCRIPTS + [dict(SCRIPTS[0], id="another")]
    with pytest.raises(CliError) as ambiguous:
        find_script(duplicated, "好运连叽摇骰子")
    assert ambiguous.value.code == EXIT_USAGE


def test_param_coercion_follows_definition_type():
    assert coerce_param({"name": "n", "type": "number"}, "7") == 7
    assert coerce_param({"name": "b", "type": "bool"}, "yes") is True
    assert coerce_param({"name": "b", "type": "bool"}, "0") is False
    assert coerce_param({"name": "s", "type": "select",
                         "options": ["a", "b"]}, "b") == "b"
    assert coerce_param({"name": "t", "type": "text"}, "任意 文本") == "任意 文本"
    assert coerce_param({"name": "g", "type": "checkgroup"}, '{"a": 1}') == {"a": True}
    with pytest.raises(CliError):
        coerce_param({"name": "n", "type": "number"}, "abc")
    with pytest.raises(CliError):
        coerce_param({"name": "s", "type": "select", "options": ["a"]}, "x")
    with pytest.raises(CliError):
        coerce_param({"name": "n", "type": "number", "min": 3}, "1")


def test_overrides_reject_undeclared_parameters():
    definitions = [{"name": "times", "type": "number"}]
    assert apply_overrides(definitions, {"times": "5"}) == {"times": 5}
    with pytest.raises(CliError) as error:
        apply_overrides(definitions, {"unknown": "1"})
    assert error.value.code == EXIT_USAGE


def test_assignments_require_key_value_shape():
    assert parse_assignments(["a=1", "b=空 格"]) == {"a": "1", "b": "空 格"}
    with pytest.raises(CliError):
        parse_assignments(["broken"])


def test_headless_callback_never_blocks_on_dialogs():
    yes = HeadlessCallbacks(assume_yes=True)
    assert yes("confirm", message="继续？") is True
    assert HeadlessCallbacks()("confirm", message="继续？") is False
    assert HeadlessCallbacks()("notify", message="提示") is None
    assert HeadlessCallbacks()("input", prompt="名称") is None
    with pytest.raises(HeadlessPause):
        HeadlessCallbacks()("pause", message="需要人工处理")


def test_parser_exec_action_defaults():
    args = build_parser().parse_args(["--exec", "--id", "haoyun_lianji"])
    assert args.exec is True
    assert args.script_id == "haoyun_lianji"
    assert args.target == "auto"
    assert args.layout == ""
    assert args.timeout == 0
    assert args.yes is False
    assert args.dry_run is False


def test_parser_requires_exactly_one_action():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--exec", "--list"])


def test_parser_list_action_lists_or_shows_schema():
    assert build_parser().parse_args(["--list"]).list_scripts is True
    detail = build_parser().parse_args(["--list", "--name", "好运连叽摇骰子"])
    assert detail.list_scripts is True
    assert detail.script_name == "好运连叽摇骰子"


def test_parser_all_is_alias_for_all_plugins():
    # --all 与 --all-plugins 等价，都必须落到 all_plugins=True
    assert build_parser().parse_args(["--all", "--exec", "--id", "haoyun_lianji"]).all_plugins is True
    assert build_parser().parse_args(["--all-plugins", "--exec", "--id", "haoyun_lianji"]).all_plugins is True


def test_console_level_quiet_only_silences_stdout_noise():
    args = argparse.Namespace(quiet=False, log_level="")
    assert _console_level(args) == "INFO"
    args.quiet = True
    assert _console_level(args) == "ERROR"
    args.quiet = False
    args.log_level = "DEBUG"
    assert _console_level(args) == "DEBUG"


def test_outcome_keeps_stable_json_shape():
    started = __import__("datetime").datetime.now().astimezone()
    payload = _outcome(ok=True, config=SCRIPTS[0], username="宛元芷",
                       target="adb:1", layout="android", started=started,
                       result={"ok": 1})
    assert payload["ok"] is True and payload["code"] == EXIT_OK
    assert payload["script"] == "haoyun_lianji"
    assert payload["result"] == {"ok": 1}
    assert set(payload) >= {"script", "name", "user", "target", "layout",
                            "started_at", "duration_ms", "result_path",
                            "result", "error"}


def test_list_and_schema_do_not_touch_config(monkeypatch):
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.discover_scripts", lambda: SCRIPTS)
    assert _list_scripts(as_json=True) == EXIT_OK
    assert _print_schema("haoyun_lianji", "", as_json=True) == EXIT_OK


def test_cli_main_returns_usage_code_for_bad_params(monkeypatch):
    called = []
    monkeypatch.setattr(
        "lvjiang.core.task_cli.execute",
        lambda request: called.append(request) or {"code": EXIT_OK})
    code = cli_main(
        ["--exec", "--id", "haoyun_lianji", "--params-json", "[]"],
        bootstrap=lambda *a, **kw: None)
    assert code == EXIT_USAGE
    assert called == []


def test_cli_main_runs_and_maps_interrupted(monkeypatch):
    monkeypatch.setattr(
        "lvjiang.core.task_cli.execute",
        lambda request: {"code": EXIT_INTERRUPTED, "ok": False,
                         "script": "haoyun_lianji", "name": "好运连叽摇骰子",
                         "user": "u", "target": "adb:1", "layout": "android",
                         "duration_ms": 1, "result_path": "", "error": "中断"})
    code = cli_main(
        ["--exec", "--id", "haoyun_lianji", "--json"],
        bootstrap=lambda *a, **kw: None)
    assert code == EXIT_INTERRUPTED


def test_cli_main_emits_json_error_line(monkeypatch, capsys):
    def boom(request):
        raise CliError("执行失败", EXIT_USAGE)

    monkeypatch.setattr("lvjiang.core.task_cli.execute", boom)
    code = cli_main(
        ["--exec", "--id", "a", "--json", "--user", "u"],
        bootstrap=lambda *a, **kw: None)
    assert code == EXIT_USAGE
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is False and payload["code"] == EXIT_USAGE


def test_run_request_defaults_are_isolated():
    first, second = RunRequest(script_id="a"), RunRequest(script_id="b")
    assert first.params == {} and second.params == {}
    assert first.stop_event is not second.stop_event


def test_resolve_selection_requires_exactly_one():
    with pytest.raises(CliError) as none:
        resolve_selection()
    assert none.value.code == EXIT_USAGE
    with pytest.raises(CliError) as both:
        resolve_selection(script_id="a", script_name="b")
    assert both.value.code == EXIT_USAGE
