from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pytest

from lvjiang.apps.yysls.core.gather import (
    GatherClickBuffer,
    GatherRoute,
    GatherStep,
    GatherStore,
    route_to_dsl,
    viewport_signature,
)
from lvjiang.apps.yysls.core.gather_recorder import GatherInputRecorder
from lvjiang.apps.yysls.workflows.implementations.auto_gather import AutoGatherWorkflow
from lvjiang.core.layout_models import Layout, Region
from lvjiang.workflows.engine.signals import _BreakSignal
from lvjiang.workflows.grammar import parse_text


def make_route(name="测试路线"):
    return GatherRoute(name=name, start_note="测试起点", layout_key="desktop",
                       steps=[GatherStep(0.4, 0.5)])


def test_f1_records_current_mouse_position_and_latest_map_view():
    buffer = GatherClickBuffer()
    step = buffer.mark(0.4, 0.6)
    assert (step.x, step.y, step.viewport) == (0.4, 0.6, "")


@pytest.mark.parametrize("x,y", [(-0.1, 0.5), (1.1, 0.5)])
def test_outside_cursor_does_not_record(x, y):
    buffer = GatherClickBuffer()
    with pytest.raises(ValueError):
        buffer.mark(x, y)


def test_recorder_f1_reads_current_cursor_without_mouse_click():
    layout = Layout(key="desktop")
    layout.regions["map_gather"] = [
        Region(key="map_area", x_ratio=0, y_ratio=0, w_ratio=1, h_ratio=1)
    ]
    capture = SimpleNamespace(get_capture_size=lambda: (100, 100))
    recorder = GatherInputRecorder(
        capture, layout, {"left": 100, "top": 200},
        connected=lambda: True, failed=lambda message: None,
        cursor_position=lambda: (140, 250),
    )
    step = recorder.mark_current()

    assert (step.x, step.y) == pytest.approx((0.4, 0.5))


def test_save_merges_owned_route_and_rejects_stale_edit(tmp_path):
    root = tmp_path / "workflows" / "gather"
    first = GatherStore(root)
    a, b = make_route("甲"), make_route("乙")
    previous = first.save(a, None)
    second = GatherStore(root)
    second.save(b, None)
    a.name = "甲改名"
    first.save(a, previous)
    fresh = GatherStore(root)
    routes, errors = fresh.routes()
    assert not errors
    assert {r.name for r in routes} == {"甲改名", "乙"}
    with pytest.raises(ValueError, match="其他窗口"):
        second.save(a, previous)
    assert {r.name for r in second.routes()[0]} == {"甲改名", "乙"}


def test_bad_route_is_isolated(tmp_path):
    root = tmp_path / "workflows" / "gather"
    store = GatherStore(root)
    store.save(make_route(), None)
    (root / "broken.wf").write_text("press M\n", encoding="utf-8")
    routes, errors = store.routes()
    assert len(routes) == len(errors) == 1


def make_workflow(monkeypatch, scenes, *, selected=True):
    layout = Layout(key="desktop")
    layout.regions["map_gather"] = [
        Region(key=key, x_ratio=0, y_ratio=0, w_ratio=1, h_ratio=1)
        for key in ("map_area", "map_view", "travel", "confirm_direct", "home_controls", "motion")]
    wf = AutoGatherWorkflow(None, None, None, layout)
    wf._engine = SimpleNamespace(run_env="desktop")
    route = make_route()
    route.travel_timeout = 10
    wf.configure(route, selected_target=selected)
    clock = [0.0]
    observations = iter(scenes)
    state = ["map"]
    actions = []

    def frame():
        wf._checkpoint()
        state[0] = next(observations, state[0])
        return np.zeros((32, 48, 3), np.uint8)

    def text(frame, key):
        return {("map", "travel"): "识途/前往", ("confirm", "confirm_direct"): "识途直达",
                ("home", "home_controls"): "TAB Num2"}.get((state[0], key), "")

    monkeypatch.setattr(wf, "_now", lambda: clock[0])
    monkeypatch.setattr(wf, "_sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(wf, "_frame", frame)
    monkeypatch.setattr(wf, "_text", text)
    monkeypatch.setattr(wf, "_press", lambda key: actions.append(key))
    monkeypatch.setattr(wf, "click_at", lambda *args, **kw: actions.append("click"))
    monkeypatch.setattr(wf, "_ratio_to_screen", lambda x, y: (10, 10))
    return wf, actions, route


def test_unreachable_timeout_never_collects_or_skips(monkeypatch):
    wf, actions, route = make_workflow(monkeypatch, ["map"])
    wf.route.steps.append(deepcopy(wf.route.steps[0]))
    assert "超时" in wf.run()["error"]
    assert actions == ["V", "F"]
    assert wf.output["steps"] == []


def test_replay_presses_v_then_f_and_records_only_after_collection(monkeypatch):
    wf, actions, _ = make_workflow(monkeypatch, ["map", "map", "home", "map"])
    completed = []
    wf.completed = completed.append
    result = wf.run()
    assert actions == ["V", "F", "1"]
    assert result["steps"][0]["collection_triggered"] is True
    assert result["steps"][0]["travel_seconds"] >= 0
    assert completed == [result]


def test_viewport_mismatch_never_clicks(monkeypatch):
    wf, actions, _ = make_workflow(monkeypatch, ["map"], selected=False)
    wf.route.steps[0].viewport = viewport_signature(np.full((32, 48, 3), 255, np.uint8))
    assert "视口" in wf.run()["error"]
    assert actions == []


def test_stop_before_input_and_other_environment_rejected(monkeypatch):
    wf, actions, _ = make_workflow(monkeypatch, ["map"])
    wf._stop_check = lambda: True
    with pytest.raises(_BreakSignal):
        wf.run()
    assert not actions
    wf._engine.run_env = "android"
    assert "桌面" in wf.run()["error"]


def test_execution_snapshot_never_writes_back(monkeypatch):
    wf, _, original = make_workflow(monkeypatch, ["map"])
    initial = asdict(original)
    wf.route.steps.reverse()
    wf.route.steps[0].travel_seconds = 50
    assert asdict(original) == initial
    original.steps[0].x = 0.1
    assert wf.route.steps[0].x == 0.4


def test_pause_time_is_excluded(monkeypatch):
    wf, _, _ = make_workflow(monkeypatch, ["map"])
    clock = [5.0]
    wf._paused_seconds = 0.0
    monkeypatch.setattr("lvjiang.apps.yysls.workflows.implementations.auto_gather.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(wf, "_wait_if_paused", lambda: clock.__setitem__(0, clock[0] + 100))
    wf._checkpoint()
    assert wf._paused_seconds == 100


def test_empty_route_is_not_runnable():
    with pytest.raises(ValueError, match="录制采集点"):
        GatherRoute().validate(runnable=True)


def test_existing_route_defaults_auto_travel_to_v():
    data = asdict(make_route())
    data.pop("travel_key")
    assert GatherRoute.from_dict(data).travel_key == "V"


def test_existing_route_defaults_travel_confirmation_to_f():
    data = asdict(make_route())
    data.pop("confirm_key")
    assert GatherRoute.from_dict(data).confirm_key == "F"


def test_route_compiles_to_clean_semantic_wf():
    route = make_route()
    route.steps[0].travel_seconds = 12.5
    body = [line for line in route_to_dsl(route).splitlines()
            if line and not line.startswith("#")]
    assert body == [
        'press "M"',
        "click (0.400000, 0.500000)",
        'press "V"',
        "wait 0.800",
        'press "F"',
        "wait 12.500",
        'press "1"',
        "wait 6.000",
    ]
    assert len(parse_text(route_to_dsl(route)).body) == 8


def test_replay_preserves_order_without_mutating_saved_route(monkeypatch):
    wf, actions, original = make_workflow(monkeypatch, ["map"], selected=False)
    wf.route.steps.append(GatherStep(0.6, 0.7, wf.route.steps[0].viewport))
    before = asdict(wf.route)
    monkeypatch.setattr(wf, "_arrive", lambda step: 7.5)
    result = wf.run()
    assert "error" not in result
    assert [s["index"] for s in result["steps"]] == [1, 2]
    assert actions == ["click", "1", "click", "1"]
    assert asdict(wf.route) == before
    assert original.steps[0].travel_seconds == 0


def test_unconfigured_task_returns_actionable_error(monkeypatch):
    wf, _, _ = make_workflow(monkeypatch, ["map"])
    del wf.route
    del wf.progress
    assert "采集页" in wf.run()["error"]


def test_missing_home_state_does_not_count_as_arrival(monkeypatch):
    wf, actions, _ = make_workflow(monkeypatch, ["map"])
    frame_number = [0]

    def moving_frame():
        frame_number[0] += 1
        return np.full((32, 48, 3), (frame_number[0] % 2) * 255, np.uint8)

    monkeypatch.setattr(wf, "_frame", moving_frame)
    monkeypatch.setattr(wf, "_ensure_map", lambda: moving_frame())
    monkeypatch.setattr(wf, "_text", lambda frame, key: "")
    assert "超时" in wf.run()["error"]
    assert actions == ["V", "F"]


def test_gather_history_is_not_duplicated_in_console():
    from lvjiang.ui.main.run_control import _log_workflow_result
    assert not _log_workflow_result("auto_gather", {"events": []}, interrupted=False)


def test_disabled_recognition_region_rejects_before_navigation(monkeypatch):
    wf, actions, _ = make_workflow(monkeypatch, ["map"])
    next(region for region in wf._layout.regions["map_gather"]
         if region.key == "travel").disabled = True
    assert "标定" in wf.run()["error"]
    assert not actions
