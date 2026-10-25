"""通用弹窗子过程：可选弹窗没出现时不能有任何输入副作用。

`scan_and_cancel` 被用在「顺带可能弹出、本流程不接受」的询问上（例如江湖号令
合影进入拍照时的画质选择）。弹窗没出现时必须完全无感：不点任何区域、不按键、
不等待；否则在拍照界面误按 ESC/取消会直接退出当前流程，属于会破坏主流程的
静默副作用。
"""
from pathlib import Path

from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine

WORKFLOWS = Path(__file__).resolve().parents[2] / "config/system/workflows"
PAGE_ACTION = parse_text(
    (WORKFLOWS / "subcall/page_action.wf").read_text(encoding="utf-8")).procs


def _engine(hit: str):
    engine = make_engine()
    engine._procs = dict(PAGE_ACTION)
    calls = {"scan": 0, "click": 0, "wait": 0}

    def scan(node):
        calls["scan"] += 1
        engine.variables[node.target.name] = hit

    engine._exec_scan = scan
    engine._exec_click = lambda node: calls.__setitem__("click", calls["click"] + 1)
    engine._exec_wait = lambda node: calls.__setitem__("wait", calls["wait"] + 1)
    return engine, calls


def test_scan_and_cancel_without_dialog_has_no_input_side_effect():
    engine, calls = _engine("")
    engine._exec_body(parse_text('call $done = scan_and_cancel("画质选择")\n').body)

    assert engine.variables["done"] == 0
    assert calls["click"] == 0, "弹窗没出现时不能点击/按键"
    assert calls["wait"] == 0, "弹窗没出现时不能额外等待"
    assert calls["scan"] == 1


def test_scan_and_cancel_with_dialog_cancels_once():
    engine, calls = _engine("cancel")
    engine._exec_body(parse_text('call $done = scan_and_cancel("画质选择")\n').body)

    assert engine.variables["done"] == 1
    assert calls["click"] == 1, "命中弹窗时只执行一次取消"
    assert calls["wait"] == 1


def test_close_item_prompts_stops_after_ten_closes():
    """关闭图标一直命中时必须停下来。

    浮窗点不掉或模板误匹配时，无上限的循环会把无人值守的批量任务卡在无限点击上
    直到用户按 F10。契约是「连续 10 次仍命中就记 error 并结束」，返回已关闭数量。
    """
    engine = make_engine(run_env="android")
    engine._procs = dict(PAGE_ACTION)
    calls = {"scan": 0, "click": 0}

    def scan(node):
        calls["scan"] += 1
        engine.variables[node.target.name] = "close_item"  # 永远命中

    engine._exec_scan = scan
    engine._exec_click = lambda node: calls.__setitem__("click", calls["click"] + 1)
    engine._exec_wait = lambda node: None

    engine._exec_body(parse_text('call $closed = close_item_prompts()\n').body)

    assert engine.variables["closed"] == 10
    assert calls["click"] == 10, "达到上限后不得继续点击"
    assert calls["scan"] == 10


def test_close_item_prompts_is_noop_on_desktop():
    engine = make_engine(run_env="desktop")
    engine._procs = dict(PAGE_ACTION)
    calls = {"scan": 0, "click": 0}
    engine._exec_scan = lambda node: calls.__setitem__("scan", calls["scan"] + 1)
    engine._exec_click = lambda node: calls.__setitem__("click", calls["click"] + 1)

    engine._exec_body(parse_text('call $closed = close_item_prompts()\n').body)

    assert engine.variables["closed"] == 0
    assert calls == {"scan": 0, "click": 0}
