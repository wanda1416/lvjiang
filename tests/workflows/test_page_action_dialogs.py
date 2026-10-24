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
