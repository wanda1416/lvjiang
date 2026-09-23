"""Profile 变更脚本队列的异步、无锁和防环契约。"""

from __future__ import annotations

import threading
from pathlib import Path

import yaml

from lvjiang.core.profile.triggers import ProfileTriggerEvent


def _configure_profile(tmp_path: Path, monkeypatch, *, script: str) -> None:
    from lvjiang.core.profile import repository, schema

    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(
        yaml.safe_dump(
            {
                "stock": [
                    {
                        "key": "source",
                        "label": "源",
                        "change_script": script,
                    },
                    {"key": "target", "label": "目标"},
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "_PROFILE_PATH", profile_path)
    monkeypatch.setattr(repository, "_DB_PATH", tmp_path / "profile.db")
    schema._config = None
    repository.reset_profile_db()


def _install_runner(tmp_path: Path, monkeypatch, wf_path: Path):
    from lvjiang.core.profile import triggers

    runner = triggers.ProfileScriptRunner(tmp_path / "users")
    monkeypatch.setattr(triggers, "_runner", runner)
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.resolve_workflow_path",
        lambda _wf_file: (wf_path, "profile/change.wf"),
    )
    return runner


def test_manual_same_value_skips_write_even_with_force(monkeypatch):
    from lvjiang.ui.profile.cell_editing import ProfileCellEditingMixin

    def unexpected_write(*args, **kwargs):
        raise AssertionError("手动同值更新不应进入写入与脚本链路")

    monkeypatch.setattr("lvjiang.core.profile.service.profile_action", unexpected_write)
    for is_action in (True, False):
        ProfileCellEditingMixin._adjust_value(
            None, "tester", "regen", "energy", None, 10.5, 0,
            is_action=is_action, force_write=True, regen_progress_source="target",
        )


def test_period_reset_zero_to_zero_still_submits_script(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    from lvjiang.core.profile import engine, repository, triggers
    from lvjiang.core.profile.models import QuotaKeyDef

    monkeypatch.setattr(repository, "_DB_PATH", tmp_path / "profile.db")
    repository.reset_profile_db()
    repository.db_upsert(
        "tester", "quota", "daily", 0,
        updated_at=(datetime.now() - timedelta(days=2)).isoformat(),
    )
    kd = QuotaKeyDef(key="daily", label="每日", period="day",
                     change_script="profile/change.wf")
    config = SimpleNamespace(get_keys_by_model=lambda model: [kd] if model == "quota" else [])
    events = []
    monkeypatch.setattr(triggers, "submit_profile_trigger", events.append)
    worker = SimpleNamespace(data_updated=SimpleNamespace(emit=lambda _: None))
    engine.ProfileEngine._tick_user(worker, "tester", config)
    assert len(events) == 1
    assert events[0].old_value == events[0].new_value == 0
    assert events[0].change_type == "tick"
    engine.ProfileEngine._tick_user(worker, "tester", config)
    assert len(events) == 1


def test_profile_write_enqueues_script_and_returns_before_execution(tmp_path, monkeypatch):
    from lvjiang.core.profile.service import profile_action, profile_read

    wf_path = tmp_path / "change.wf"
    wf_path.write_text(
        'eval profile_inc("target", 1, "关联脚本")\n',
        encoding="utf-8",
    )
    _configure_profile(tmp_path, monkeypatch, script="profile/change.wf")
    runner = _install_runner(tmp_path, monkeypatch, wf_path)

    # 若误走 execute()，这个桩会让用户锁路径直接失败；Profile 队列必须走
    # execute_unlocked()，且提交方不等待脚本结果。
    monkeypatch.setattr(
        "lvjiang.core.access.acquire_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应获取用户锁")),
    )

    assert profile_action("tester", "source", set_value=1) == 1
    runner._queue.join()
    assert profile_read("tester", "target") == 1

    runner.request_stop()
    runner.join(timeout=3)


def test_profile_script_cycle_is_rejected_before_second_write(tmp_path, monkeypatch):
    from lvjiang.core.profile.service import profile_action, profile_read

    wf_path = tmp_path / "cycle.wf"
    wf_path.write_text(
        'eval profile_inc("source", 1, "循环脚本")\n',
        encoding="utf-8",
    )
    _configure_profile(tmp_path, monkeypatch, script="profile/cycle.wf")
    runner = _install_runner(tmp_path, monkeypatch, wf_path)

    assert profile_action("tester", "source", set_value=1) == 1
    runner._queue.join()
    assert profile_read("tester", "source") == 1

    runner.request_stop()
    runner.join(timeout=3)


def test_runner_submit_is_non_blocking_and_events_run_in_fifo_order(
    tmp_path, monkeypatch
):
    from lvjiang.core.profile.triggers import ProfileScriptRunner

    runner = ProfileScriptRunner(tmp_path / "users")
    first_started = threading.Event()
    release_first = threading.Event()
    executed: list[str] = []

    def run_event(event):
        executed.append(event.key)
        if event.key == "first":
            first_started.set()
            assert release_first.wait(timeout=3)

    monkeypatch.setattr(runner, "_run_event", run_event)
    make_event = lambda key: ProfileTriggerEvent(  # noqa: E731
        username="tester",
        model="stock",
        key=key,
        old_value=0,
        new_value=1,
        delta=1,
        source="test",
        change_type="action",
        script="profile/test.wf",
        origin_key=key,
        origin_model="stock",
        path=(f"stock:{key}",),
    )

    assert runner.submit(make_event("first"))
    assert first_started.wait(timeout=3)
    assert runner.submit(make_event("second"))
    assert executed == ["first"]

    release_first.set()
    runner._queue.join()
    assert executed == ["first", "second"]
    runner.request_stop()
    runner.join(timeout=3)


def test_juezhang_script_increments_niaoniao_only_when_crossing_threshold(
    tmp_path, monkeypatch
):
    from lvjiang.core.profile import repository, schema, triggers
    from lvjiang.core.profile.service import profile_action, profile_read

    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(
        yaml.safe_dump(
            {
                "quota": [
                    {
                        "key": "juezhang_of_season",
                        "label": "觉障段位",
                        "change_script": "profile/juezhang_reward.wf",
                    }
                ],
                "stock": [{"key": "niaoniao", "label": "袅袅之音"}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "_PROFILE_PATH", profile_path)
    monkeypatch.setattr(repository, "_DB_PATH", tmp_path / "profile.db")
    schema._config = None
    repository.reset_profile_db()
    runner = triggers.ProfileScriptRunner(tmp_path / "users")
    monkeypatch.setattr(triggers, "_runner", runner)

    profile_action("tester", "juezhang_of_season", set_value=3199)
    runner._queue.join()
    assert profile_read("tester", "niaoniao") is None

    profile_action("tester", "juezhang_of_season", set_value=3200)
    runner._queue.join()
    assert profile_read("tester", "niaoniao") == 1

    profile_action("tester", "juezhang_of_season", set_value=3201)
    runner._queue.join()
    assert profile_read("tester", "niaoniao") == 1

    profile_action("direct-crossing", "juezhang_of_season", set_value=3000)
    runner._queue.join()
    profile_action("direct-crossing", "juezhang_of_season", set_value=3500)
    runner._queue.join()
    assert profile_read("direct-crossing", "niaoniao") == 1

    runner.request_stop()
    runner.join(timeout=3)
