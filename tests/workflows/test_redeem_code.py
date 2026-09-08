"""真实 DSL 控制流配合模拟游戏页面验证兑换码状态机，不调用设备。"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from loguru import logger

from lvjiang.core.config import load_user_config
from lvjiang.core.layout_manager import load_layout_by_name
from lvjiang.workflows.engine import WorkflowEngine
from lvjiang.workflows.metadata import parse_metadata

WORKFLOW_PATH = Path(__file__).resolve().parents[2] / "config/system/workflows/redeem_code.wf"


class RedeemGame:
    def __init__(self, monkeypatch, *, platform="desktop", codes="ABC",
                 outcomes=("success",), manual="input", cancel_fails=False,
                 open_fails=False, other_fails=False, repair_after=1):
        self.state = "main"
        self.platform = platform
        self.input_focused = False
        self.events = []
        self.warnings = []
        self.repair_messages = []
        self.repair_after = repair_after
        self.outcomes = iter(outcomes)
        self.manual = manual
        self.cancel_fails = cancel_fails
        self.open_fails = open_fails
        self.other_fails = other_fails
        config = load_user_config()
        self.engine = WorkflowEngine(
            capture=MagicMock(), ocr=MagicMock(), input_ctrl=MagicMock(),
            layout=load_layout_by_name("桌面布局" if platform == "desktop" else "默认布局"),
            input_sim=config.input_sim, delay_params=config.delay_params, run_env=platform,
        )
        self.engine.variables = {"redeem_code": codes}
        original_call = self.engine._exec_call_proc

        def call(node):
            if node.name == "nav_main_to_menu":
                self.events.append(("navigate", "menu"))
                self.state = "menu"
                self.engine.variables[node.result_var] = 0
            elif node.name in ("is_in_menu_page", "is_in_main_page"):
                self.engine.variables[node.result_var] = self.state == (
                    "menu" if node.name == "is_in_menu_page" else "main")
            else:
                original_call(node)

        monkeypatch.setattr(self.engine, "_exec_call_proc", call)
        monkeypatch.setattr(self.engine, "_exec_click", self.click)
        monkeypatch.setattr(self.engine, "_exec_scan", self.scan)
        monkeypatch.setattr(self.engine, "_exec_press", self.press)
        monkeypatch.setattr(self.engine, "_exec_paste", self.paste)
        monkeypatch.setattr(self.engine, "_exec_wait", lambda _node: None)
        monkeypatch.setattr(logger, "warning", lambda message: self.warnings.append(str(message)))
        self.engine._ui_callback = self.pause

    def click(self, node):
        scene, key = node.target.scene, node.target.entity
        self.events.append(("click", key))
        if scene == "game_menu_page":
            self.state = "settings" if key == "settings" else "main"
        elif key == "store":
            self.state = "settings"
        elif key == "other":
            self.state = "settings" if self.other_fails else "other"
        elif key == "redeem_code":
            if self.state == "other" and not self.open_fails:
                self.state = "input"
        elif key == "input_area":
            assert self.state == "input"
            self.input_focused = True
        elif key == "redeem_label":
            assert self.state == "input"
            self.input_focused = False
        elif key == "confirm":
            assert self.state in ("input", "used")
            if self.platform == "desktop":
                assert not self.input_focused, "确认前必须移除输入框焦点"
            if self.state == "input":
                self.state = {"success": "other", "used": "used", "stuck": "input"}[
                    next(self.outcomes)]
        elif key == "cancel":
            assert self.state in ("input", "used")
            if not self.cancel_fails:
                self.state = "other"
        elif key == "back":
            assert self.state == "other", "弹窗未关闭不得返回设置上层"
            self.state = "menu"

    def scan(self, node):
        key = node.fields[0].value
        assert key != "redeem_code_label", "背景标签不能用于判断兑换码弹窗"
        self.events.append(("scan", key))
        visible = {
            "store": self.state == "settings",
            "redeem_label": self.state in ("input", "used"),
        }[key]
        assert node.by.target.value == {
            "store": "存储", "redeem_label": "兑换奖励",
        }[key]
        self.engine.variables[node.target.name] = key if visible else ""

    def press(self, node):
        assert node.key == "E" and self.state == "settings"
        self.events.append(("press", "E"))
        self.state = "settings" if self.other_fails else "other"

    def paste(self, node):
        assert self.state == "input" and self.input_focused
        self.events.append(("paste", self.engine._resolve(node.value)))

    def pause(self, action, **kwargs):
        assert action == "pause"
        message = kwargs.get("message", "")
        if "兑换窗口还在" in message:
            assert self.state in ("input", "used")
            self.repair_messages.append(message)
            self.events.append(("pause", "repair"))
            if len(self.repair_messages) >= self.repair_after:
                self.state = "other"
            return ""
        assert self.state == "input"
        self.events.append(("pause", "input"))
        self.state = {"input": "input", "completed": "other", "used": "used"}[self.manual]
        return ""

    def run(self):
        self.engine.execute(str(WORKFLOW_PATH))
        return self

    @property
    def clicks(self):
        return [value for action, value in self.events if action == "click"]


def test_redeem_code_parameter_supports_multiline():
    parameter = parse_metadata(WORKFLOW_PATH.read_text(encoding="utf-8"))["parameters"][0]
    assert parameter["type"] == "text"
    assert parameter["multiline"] is True
    assert parameter["default"] == ""


@pytest.mark.parametrize("codes", ["", "  ", "\r\n \n\t\r"])
def test_desktop_blank_codes_abort_before_navigation(monkeypatch, codes):
    game = RedeemGame(monkeypatch, codes=codes).run()
    assert game.events == []
    game.engine._capture.capture.assert_not_called()


def test_desktop_processes_lines_and_cancels_used_code_once(monkeypatch):
    game = RedeemGame(
        monkeypatch, codes=" A \r\n\r\n B \n C\rD ",
        outcomes=("success", "used", "success", "success"),
    ).run()
    assert [value for kind, value in game.events if kind == "paste"] == ["A", "B", "C", "D"]
    assert game.clicks == [
        "settings", "store",
        "redeem_code", "input_area", "redeem_label", "confirm",
        "redeem_code", "input_area", "redeem_label", "confirm", "cancel",
        "redeem_code", "input_area", "redeem_label", "confirm",
        "redeem_code", "input_area", "redeem_label", "confirm",
        "back", "back",
    ]
    assert len(game.warnings) == 1 and "已兑换" in game.warnings[0]
    assert game.state == "main"


@pytest.mark.parametrize("manual, confirms, cancels", [
    ("input", 1, 0), ("completed", 0, 0), ("used", 1, 1),
])
def test_android_only_handles_one_manual_entry(monkeypatch, manual, confirms, cancels):
    game = RedeemGame(
        monkeypatch, platform="android", codes="A\nB\nC", manual=manual,
    ).run()
    assert game.clicks.count("redeem_code") == 1
    assert game.clicks.count("confirm") == confirms
    assert game.clicks.count("cancel") == cancels
    assert len(game.warnings) == cancels
    assert game.events.count(("pause", "input")) == 1
    assert not any(kind in ("paste", "press") for kind, _ in game.events)
    assert game.state == "main"


def test_android_used_code_after_script_confirm(monkeypatch):
    game = RedeemGame(monkeypatch, platform="android", codes="", outcomes=("used",)).run()
    assert game.clicks.count("cancel") == 1
    assert len(game.warnings) == 1
    assert game.state == "main"


@pytest.mark.parametrize("options", [
    {"open_fails": True},
    {"other_fails": True},
])
def test_unopened_redeem_page_stops_following_codes(monkeypatch, options):
    game = RedeemGame(monkeypatch, codes="A\nB", **options).run()
    assert game.clicks.count("redeem_code") <= 1
    assert "back" not in game.clicks
    assert ("paste", "B") not in game.events


def test_finish_checks_redeem_label_before_confirm_and_after_cancel(monkeypatch):
    game = RedeemGame(monkeypatch, outcomes=("used",)).run()
    events = game.events
    confirm = events.index(("click", "confirm"))
    cancel = events.index(("click", "cancel"))
    assert events[confirm - 1] == ("scan", "redeem_label")
    assert events[confirm + 1:cancel] == [("scan", "redeem_label")]
    assert events[cancel + 1] == ("scan", "redeem_label")


def test_desktop_removes_focus_immediately_after_every_paste(monkeypatch):
    game = RedeemGame(monkeypatch, codes="A\nB", outcomes=("success", "used")).run()
    for index, (action, _value) in enumerate(game.events):
        if action == "paste":
            assert game.events[index + 1] == ("click", "redeem_label")


def test_confirm_without_closing_title_cancels_and_continues(monkeypatch):
    game = RedeemGame(monkeypatch, codes="A\nB", outcomes=("stuck", "success")).run()
    assert game.clicks.count("cancel") == 1
    assert ("paste", "B") in game.events
    assert game.state == "main"


@pytest.mark.parametrize("outcome", ["used", "stuck"])
@pytest.mark.parametrize("repair_after", [1, 2])
def test_cancel_failure_pauses_until_manual_close_then_continues(
        monkeypatch, outcome, repair_after):
    game = RedeemGame(
        monkeypatch, codes="A\nB", outcomes=(outcome, "success"),
        cancel_fails=True, repair_after=repair_after,
    ).run()
    assert len(game.repair_messages) == repair_after
    assert all("手动取消" in message and "检查修复" in message
               for message in game.repair_messages)
    for index, event in enumerate(game.events):
        if event == ("pause", "repair"):
            assert game.events[index + 1] == ("scan", "redeem_label")
    last_pause = max(index for index, event in enumerate(game.events)
                     if event == ("pause", "repair"))
    assert game.events.index(("paste", "B")) > last_pause
    assert game.clicks.count("cancel") == 1
    assert game.state == "main"
