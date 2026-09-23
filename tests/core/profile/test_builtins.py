from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import yaml


@pytest.fixture
def profile_func_env(tmp_path, monkeypatch):
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    import lvjiang.core.profile.repository as profile_db
    import lvjiang.core.profile.schema as profile_config

    profile_config._config = None
    profile_config._PROFILE_PATH = session_dir / "profile.yaml"
    profile_config._PROFILE_PATH.write_text(
        yaml.dump(
            {
                "regen": [
                    {
                        "key": "resource_meter",
                        "label": "资源值",
                        "cap": 600,
                        "regen_type": "realtime",
                        "regen_rate_value": 0.125,
                        "regen_rate_unit": "minute",
                        "sync_targets": [{"key": "stock:target_stock"}],
                    }
                ],
                "stock": [
                    {"key": "target_stock", "label": "同步目标"},
                ],
                "quota": [
                    {
                        "key": "weekly_progress",
                        "label": "每周进度",
                        "period": "week",
                        "reset_time": "05:00",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    profile_db._db = None
    profile_db._DB_PATH = session_dir / "profile.db"

    yield SimpleNamespace(username="u", session_dir=session_dir)

    profile_config._config = None
    profile_db._db = None


def test_profile_all_computes_realtime_regen_value(profile_func_env):
    from lvjiang.core.profile.repository import db_upsert
    from lvjiang.workflows.builtins.profile import (
        _profile_all,
        _profile_get,
    )

    updated_at = (datetime.now() - timedelta(minutes=4)).isoformat(timespec="seconds")
    db_upsert(profile_func_env.username, "regen", "resource_meter", 100, updated_at=updated_at)

    engine = SimpleNamespace(run_username=profile_func_env.username)
    by_get = _profile_get(engine, "resource_meter")
    by_all = _profile_all(engine)["regen"]["resource_meter"]["value"]

    assert 100.49 <= by_get <= 100.51
    assert by_all == pytest.approx(by_get, abs=0.02)


def test_profile_inc_preserves_realtime_fraction_progress(profile_func_env):
    from lvjiang.core.profile.repository import (
        db_read_entry,
        db_upsert,
    )
    from lvjiang.workflows.builtins.profile import _profile_inc

    updated_at = (datetime.now() - timedelta(minutes=4)).isoformat(timespec="seconds")
    db_upsert(profile_func_env.username, "regen", "resource_meter", 100, updated_at=updated_at)

    engine = SimpleNamespace(run_username=profile_func_env.username)
    returned = _profile_inc(engine, "resource_meter", -20)
    entry = db_read_entry(profile_func_env.username, "regen", "resource_meter")
    stored_ts = datetime.fromisoformat(entry["updated_at"])

    assert returned == pytest.approx(80.5, abs=0.02)
    assert entry["value"] == 80
    # 容差 2 秒：isoformat(timespec="seconds") 截断小数秒，CI 环境时序不稳定
    assert datetime.now() - timedelta(minutes=4, seconds=2) <= stored_ts
    assert stored_ts <= datetime.now() - timedelta(minutes=3, seconds=58)


def test_profile_write_builtins_record_explicit_business_sources(profile_func_env):
    from lvjiang.core.profile.repository import db_get_history
    from lvjiang.workflows.builtins.profile import (
        _profile_inc,
        _profile_observe,
        _profile_set,
    )

    engine = SimpleNamespace(run_username=profile_func_env.username)

    _profile_set(engine, "resource_meter", 100, "首次扫描")
    _profile_inc(engine, "resource_meter", -20, "资源消耗")
    _profile_observe(engine, "weekly_progress", 80, "每周任务")

    regen_history = db_get_history(
        profile_func_env.username, type_="regen", key="resource_meter"
    )
    quota_history = db_get_history(
        profile_func_env.username, type_="quota", key="weekly_progress"
    )

    assert [item["source"] for item in regen_history[:2]] == [
        "资源消耗",
        "首次扫描",
    ]
    assert quota_history[0]["source"] == "每周任务"


def test_profile_observe_rejects_regression_in_same_period(profile_func_env):
    from lvjiang.core.profile.repository import db_read_entry, db_upsert
    from lvjiang.workflows.builtins.profile import _profile_observe

    db_upsert(profile_func_env.username, "quota", "weekly_progress", 800)
    engine = SimpleNamespace(run_username=profile_func_env.username)

    result = _profile_observe(engine, "weekly_progress", 80)

    assert result == {"accepted": False, "value": 800.0, "reason": "regressed"}
    assert db_read_entry(
        profile_func_env.username, "quota", "weekly_progress"
    )["value"] == 800


def test_profile_observe_accepts_increase_in_same_period(profile_func_env):
    from lvjiang.core.profile.repository import db_read_entry, db_upsert
    from lvjiang.workflows.builtins.profile import _profile_observe

    db_upsert(profile_func_env.username, "quota", "weekly_progress", 80)
    engine = SimpleNamespace(run_username=profile_func_env.username)

    result = _profile_observe(engine, "weekly_progress", 800)

    assert result == {"accepted": True, "value": 800.0, "reason": "updated"}
    assert db_read_entry(
        profile_func_env.username, "quota", "weekly_progress"
    )["value"] == 800


def test_profile_observe_accepts_reset_value_after_period_boundary(profile_func_env):
    from lvjiang.core.profile.repository import db_read_entry, db_upsert
    from lvjiang.workflows.builtins.profile import _profile_observe

    old_ts = (datetime.now() - timedelta(days=8)).isoformat(timespec="seconds")
    db_upsert(
        profile_func_env.username,
        "quota",
        "weekly_progress",
        800,
        updated_at=old_ts,
    )
    engine = SimpleNamespace(run_username=profile_func_env.username)

    result = _profile_observe(engine, "weekly_progress", 0)

    assert result == {"accepted": True, "value": 0.0, "reason": "updated"}
    entry = db_read_entry(profile_func_env.username, "quota", "weekly_progress")
    assert entry["value"] == 0
    assert datetime.fromisoformat(entry["updated_at"]) > datetime.fromisoformat(old_ts)


def test_profile_action_can_edit_empty_realtime_regen(profile_func_env):
    from lvjiang.core.profile.repository import db_read_entry
    from lvjiang.core.profile.service import profile_action

    result = profile_action(
        profile_func_env.username,
        "resource_meter",
        model_type="regen",
        delta=10,
        current_value=0,
        expected_entry={},
        use_cas=True,
    )

    assert result == 10
    entry = db_read_entry(profile_func_env.username, "regen", "resource_meter")
    assert entry["value"] == 10


def test_realtime_sync_uses_semantic_delta_not_stored_integer_delta(profile_func_env):
    from lvjiang.core.profile.repository import (
        db_read_entry,
        db_upsert,
    )
    from lvjiang.core.profile.service import sync_write_adapter

    updated_at = (datetime.now() - timedelta(minutes=4)).isoformat(timespec="seconds")
    db_upsert(profile_func_env.username, "regen", "resource_meter", 100, updated_at=updated_at)
    db_upsert(profile_func_env.username, "stock", "target_stock", 1000)

    result = sync_write_adapter(
        profile_func_env.username,
        "regen",
        "resource_meter",
        delta=-20,
        source="test",
    )
    target = db_read_entry(profile_func_env.username, "stock", "target_stock")

    assert result is not None
    new_value, applied_delta = result
    assert new_value == pytest.approx(80.5, abs=0.02)
    assert applied_delta == pytest.approx(-20, abs=0.02)
    assert target["value"] == 1000


def test_profile_declare_creates_missing_key_and_takes_effect(profile_func_env):
    from lvjiang.core.profile.schema import get_profile_config
    from lvjiang.workflows.builtins.profile import _profile_declare, _profile_model

    engine = SimpleNamespace(run_username=profile_func_env.username)

    result = _profile_declare(
        engine, "quota", "nn_bugan_of_week",
        {"label": "不肝", "cap": 1, "increment_only": True,
         "sources": ["不肝商店"]},
    )

    assert result["ok"] is True
    assert result["created"] is True
    # 单例已刷新：后续 DSL 内置函数立即可见
    assert _profile_model(engine, "nn_bugan_of_week") == "quota"
    kd = get_profile_config().get_key("nn_bugan_of_week")
    assert kd is not None and kd.label == "不肝"
    # 定义落盘到 profile.yaml
    saved = yaml.safe_load(
        profile_func_env.session_dir.joinpath("profile.yaml").read_text(
            encoding="utf-8"))
    declared = [k for k in saved["quota"] if k["key"] == "nn_bugan_of_week"]
    assert declared and declared[0]["cap"] == 1


def test_profile_declare_keeps_existing_definition(profile_func_env):
    from lvjiang.core.profile.schema import get_profile_config
    from lvjiang.workflows.builtins.profile import _profile_declare

    engine = SimpleNamespace(run_username=profile_func_env.username)

    # target_stock 已在 fixture 的 profile.yaml 中定义为 stock
    result = _profile_declare(
        engine, "stock", "target_stock", {"label": "被覆盖的名字"})

    assert result["ok"] is True
    assert result["created"] is False
    assert result["reason"] == "already_defined"
    kd = get_profile_config().get_key("target_stock")
    assert kd.label == "同步目标"

    # 声明模型与已有定义冲突时同样不修改
    conflict = _profile_declare(
        engine, "quota", "target_stock", {"label": "冲突"})
    assert conflict["created"] is False
    assert get_profile_config().get_model_type("target_stock") == "stock"


def test_profile_declare_invalid_input_returns_error_not_raise(profile_func_env):
    from lvjiang.workflows.builtins.profile import _profile_declare

    engine = SimpleNamespace(run_username=profile_func_env.username)

    bad_model = _profile_declare(
        engine, "unknown_model", "some_key", {"label": "x"})
    assert bad_model["ok"] is False
    assert "invalid_definition" in bad_model["reason"]

    bad_def = _profile_declare(
        engine, "quota", "another_key", "not-a-dict")
    assert bad_def["ok"] is False

    empty_key = _profile_declare(engine, "quota", "", {"label": "x"})
    assert empty_key["ok"] is False

    # 失败的声明不应留下半成品定义
    from lvjiang.core.profile.schema import get_profile_config
    assert get_profile_config().get_key("another_key") is None


def test_profile_declare_rolls_back_memory_on_save_failure(
        profile_func_env, monkeypatch):
    """保存失败时必须回滚内存单例，避免「内存已定义、磁盘无」的脏状态。"""
    import lvjiang.core.profile.schema as profile_schema
    from lvjiang.core.profile.schema import get_profile_config
    from lvjiang.workflows.builtins.profile import _profile_declare, _profile_model

    def _boom(_schema):
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(profile_schema, "save_profile_config", _boom)
    engine = SimpleNamespace(run_username=profile_func_env.username)

    result = _profile_declare(
        engine, "quota", "rollback_key", {"label": "回滚"})

    assert result["ok"] is False
    assert "save_failed" in result["reason"]
    # 内存单例已从磁盘重建：key 不可见，与磁盘状态一致
    assert get_profile_config().get_key("rollback_key") is None
    assert _profile_model(engine, "rollback_key") == ""
    # 其余既有定义不受影响
    assert _profile_model(engine, "weekly_progress") == "quota"
