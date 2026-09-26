"""每日签到工作流的配置配对和通用领奖流程测试。"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lvjiang.core.config import load_user_config
from lvjiang.core.layout_manager import load_layout_by_key
from lvjiang.core.layout_models import FoundRegion
from lvjiang.workflows.engine import WorkflowEngine
from lvjiang.workflows.metadata import parse_metadata_file

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / "config/system/workflows/daily_checkin.wf"


class CheckinGame:
    def __init__(self, monkeypatch, *, activities: str, rewards: str,
                 missing_rewards: tuple[str, ...] = ()):
        config = load_user_config()
        self.engine = WorkflowEngine(
            capture=MagicMock(), ocr=MagicMock(), input_ctrl=MagicMock(),
            layout=load_layout_by_key("desktop"),
            input_sim=config.input_sim, delay_params=config.delay_params,
            run_env="desktop",
        )
        self.engine.variables = {
            "activity_names": activities,
            "reward_keywords": rewards,
        }
        self.events: list[tuple[str, str, str]] = []
        self.missing_rewards = set(missing_rewards)
        original_call = self.engine._exec_call_proc

        def call(node):
            if node.name == "nav_main_to_menu":
                self.events.append(("navigate", "", "menu"))
                self.engine.variables[node.result_var] = 0
            else:
                original_call(node)

        def scan(node):
            key = str(node.fields[0].value)
            target = str(self.engine._resolve(node.by.target))
            self.events.append(("scan", key, target))
            self.engine.variables[node.target.name] = key

        def find(node):
            area = str(node.search_region)
            target = str(self.engine._resolve(node.by.target))
            self.events.append(("find", area, target))
            if area == "checkin" and target in self.missing_rewards:
                self.engine.variables[node.var_name] = ""
            else:
                self.engine.variables[node.var_name] = FoundRegion(
                    x_ratio=0.1, y_ratio=0.1, w_ratio=0.1, h_ratio=0.1,
                    text=target,
                )

        def click(node):
            target = node.target
            if hasattr(target, "name"):
                found = self.engine.variables[target.name]
                self.events.append(("click_found", "", found.text))
            else:
                self.events.append((
                    "click", str(target.scene), str(target.entity)))

        monkeypatch.setattr(self.engine, "_exec_call_proc", call)
        monkeypatch.setattr(self.engine, "_exec_scan", scan)
        monkeypatch.setattr(self.engine, "_exec_find", find)
        monkeypatch.setattr(self.engine, "_exec_click", click)
        monkeypatch.setattr(self.engine, "_exec_drag", lambda node: None)
        monkeypatch.setattr(self.engine, "_exec_wait", lambda node: None)

    def run(self):
        self.engine.execute(str(WORKFLOW_PATH))
        return self


def test_parameters_are_multiline_and_have_public_defaults():
    parameters = parse_metadata_file(WORKFLOW_PATH)["parameters"]
    assert [(item["name"], item["multiline"], item["default"])
            for item in parameters] == [
        ("activity_names", True, "朝夕共赏"),
        ("reward_keywords", True, "签到"),
    ]


@pytest.mark.parametrize(
    ("activities", "rewards"),
    [
        ("朝夕共赏\n金秋共贺", "签到"),
        ("朝夕共赏\n\n金秋共贺", "签到\n领取\n领取"),
        ("朝夕共赏\n金秋共贺", "签到\n \n"),
    ],
)
def test_invalid_pair_config_aborts_before_navigation(monkeypatch, activities, rewards):
    game = CheckinGame(
        monkeypatch, activities=activities, rewards=rewards,
    ).run()
    assert game.events == []


def test_each_pair_finds_and_clicks_ocr_result_then_returns_to_list(monkeypatch):
    game = CheckinGame(
        monkeypatch,
        activities=" 朝夕共赏 \r\n金秋共贺 ",
        rewards="签到\r\n 领取",
        missing_rewards=("领取",),
    ).run()

    assert [event for event in game.events if event[0] == "find"] == [
        ("find", "reward_list", "朝夕共赏"),
        ("find", "checkin", "签到"),
        ("find", "reward_list", "金秋共贺"),
        ("find", "checkin", "领取"),
    ]
    assert [event[2] for event in game.events if event[0] == "click_found"] == [
        "朝夕共赏", "签到", "金秋共贺",
    ]
    assert game.events.count(("click", "activity_main", "back")) == 3
    assert game.events[-1] == ("click", "game_menu_page", "back")


def test_activity_layout_uses_generic_keys_and_desktop_has_no_space_binding():
    scene = (ROOT / "config/system/scenes/activity_main.yaml").read_text(
        encoding="utf-8")
    assert "key: zhaoxi" not in scene
    assert "key: qiandao" not in scene
    assert "key: liebiao" not in scene
    assert scene.count("key: checkin") == 2
    assert "key: reward_list" in scene

    for platform in ("android", "desktop"):
        layout = json.loads((
            ROOT / f"config/system/layouts/{platform}/activity_main.json"
        ).read_text(encoding="utf-8"))
        by_key = {item["key"]: item for item in layout["regions"]}
        assert {"reward_list", "checkin", "back"} == set(by_key)
        if platform == "desktop":
            assert "activation_key" not in by_key["checkin"]
