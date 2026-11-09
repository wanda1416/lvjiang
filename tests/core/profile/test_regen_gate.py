"""周期恢复闸门：本次进程完成首次同步前不得推算/物化周期恢复。

回归 2026-11-09 事故：``ProfileEngine`` 启动即 tick，而首次 pull 在 60~80 秒后，
窗口内用过期锚点算出的准点恢复被写成绝对值 ``set`` 上行，覆盖了另一台设备上
真实发生的消耗。这里验证两件事：

1. 闸门本身：未登录放行（无上行、无污染面），已登录且未完成首次拉取则拦住；
2. 闸门确实作用在两处写入路径上（``ProfileProcessor.tick`` 与
   ``profile_action`` 的固化），且「干净拉取」才置位。

所有用例都跑在 tmp_path 的 profile.yaml / profile.db 上，不触碰真实 session 目录。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from lvjiang.core.profile import regen_gate, repository, schema
from lvjiang.core.profile.regen_gate import period_recovery_allowed

ENERGY_CAP = 100
ENERGY_AMOUNT = 10


@pytest.fixture
def env(tmp_path, monkeypatch) -> Path:
    """隔离 profile 定义 + 账本 + 账号配置路径，断言不会落到真实 session 目录。"""
    from lvjiang.core.account import service as account_service

    session_dir = tmp_path / "session"
    session_dir.mkdir()

    profile_yaml = session_dir / "profile.yaml"
    profile_yaml.write_text(
        yaml.dump(
            {
                "regen": [
                    {"key": "energy", "label": "体力", "cap": ENERGY_CAP,
                     "regen_type": "boundary", "regen_amount": ENERGY_AMOUNT,
                     "regen_period": "day", "reset_time": "05:00"},
                    {"key": "stamina", "label": "心力", "cap": 600,
                     "regen_type": "realtime", "regen_rate_value": 0.125,
                     "regen_rate_unit": "minute", "regen_period": "day",
                     "reset_time": "05:00"},
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "_PROFILE_PATH", profile_yaml, raising=False)
    monkeypatch.setattr(schema, "_config", None, raising=False)
    monkeypatch.setattr(repository, "_DB_PATH", session_dir / "profile.db", raising=False)
    monkeypatch.setattr(repository, "_db", None, raising=False)
    monkeypatch.setattr(
        account_service, "account_config_path",
        lambda: session_dir / "account.json", raising=False)

    regen_gate.reset()
    yield session_dir
    regen_gate.reset()
    repository.reset_profile_db()

    # 兜底：确认隔离真的生效，用例不可能写到真实配置目录
    assert session_dir in Path(repository._DB_PATH).parents
    assert session_dir in Path(schema._PROFILE_PATH).parents


def _stale_anchor() -> str:
    """昨天的 05:00 边界之后——即"今天这一轮恢复还没算过"。"""
    return (datetime.now() - timedelta(days=1)).replace(
        hour=6, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def _seed(value: float) -> None:
    repository.db_upsert("u1", "regen", "energy", value, updated_at=_stale_anchor())


def _processor():
    from lvjiang.core.profile.engine import ProfileProcessor

    processor = ProfileProcessor(SimpleNamespace(list_users=lambda: ["u1"]))
    processor._last_clean = 1e18          # 跳过过期提醒清理，减少无关落盘
    return processor


# ─── 闸门本身 ────────────────────────────────────────────────

def _session(monkeypatch, state: bool | None) -> None:
    """把登录态探针设成指定值并清掉闸门缓存（state: True/False/None）。"""
    from lvjiang.core.account import service as account_service

    monkeypatch.setattr(account_service, "stored_session_state", lambda: state)
    regen_gate.reset()



def test_authenticated_blocks_until_first_pull(monkeypatch):
    _session(monkeypatch, True)
    assert not period_recovery_allowed()

    regen_gate.mark_pull_completed()
    assert period_recovery_allowed()


def test_unauthenticated_allows_local_recovery(monkeypatch):
    """未登录不会上行，本地推进没有污染面；否则不登录的用户永远不恢复。"""
    _session(monkeypatch, False)
    assert period_recovery_allowed()


# ─── 作用点一：ProfileEngine.tick ────────────────────────────


def test_tick_skips_periodic_recovery_before_first_pull(env, monkeypatch):
    _session(monkeypatch, True)
    _seed(90)

    _processor().tick()

    entry = repository.db_read_entry("u1", "regen", "energy")
    assert entry["value"] == 90
    assert entry["updated_at"] == _stale_anchor()
    assert repository.db_get_history("u1", "regen", "energy") == []


def test_tick_materializes_after_first_pull(env, monkeypatch):
    """同一场景，仅把闸门打开——证明"拦住"的是闸门而不是别的条件。"""
    _session(monkeypatch, True)
    regen_gate.mark_pull_completed()
    _seed(90)

    _processor().tick()

    entry = repository.db_read_entry("u1", "regen", "energy")
    assert entry["value"] == 90 + ENERGY_AMOUNT
    rows = repository.db_get_history("u1", "regen", "energy")
    assert len(rows) == 1 and rows[0]["new_value"] == 100


# ─── 作用点二：profile_action 的 regen 固化 ──────────────────


def test_profile_action_skips_solidify_when_gate_closed(env, monkeypatch):
    """同步完成前点一次消耗：不得顺带把「过期基线算出的恢复」写成 set。"""
    from lvjiang.core.profile.service import profile_action

    _session(monkeypatch, True)
    _seed(90)

    profile_action("u1", "energy", delta=-30, source="做菜")

    entry = repository.db_read_entry("u1", "regen", "energy")
    assert entry["value"] == 60                      # 90 - 30，没有夹带 +10
    rows = repository.db_get_history("u1", "regen", "energy")
    assert len(rows) == 1
    assert rows[0]["change_type"] == "delta"
    assert rows[0]["source"] == "做菜"
    # 不变式：new == old + delta
    assert rows[0]["new_value"] == rows[0]["old_value"] - 30


def test_profile_action_solidifies_when_gate_open(env, monkeypatch):
    from lvjiang.core.profile.service import profile_action

    _session(monkeypatch, True)
    regen_gate.mark_pull_completed()
    _seed(90)

    profile_action("u1", "energy", delta=-30, source="做菜")

    entry = repository.db_read_entry("u1", "regen", "energy")
    assert entry["value"] == 90 + ENERGY_AMOUNT - 30
    rows = repository.db_get_history("u1", "regen", "energy")
    assert any(row["change_type"] == "set" and row["delta_value"] == ENERGY_AMOUNT
               for row in rows), "闸门打开时应先物化这次准点恢复"


# ─── 干净拉取才置位 ─────────────────────────────────────────


@pytest.fixture
def pull_env(env, monkeypatch):
    """把 pull 所需的网络与落盘全部替换掉，避免读写真实 account.json。"""
    from lvjiang.core.profile import snapshot_sync

    state: dict = {}
    monkeypatch.setattr(snapshot_sync, "remote_head", lambda token: None)
    monkeypatch.setattr(snapshot_sync, "remote_snapshots", lambda token: [])
    monkeypatch.setattr(snapshot_sync, "profile_sync_state", lambda: dict(state))
    monkeypatch.setattr(
        snapshot_sync, "save_profile_sync_state",
        lambda value: state.update(value), raising=False)
    return snapshot_sync, state


def test_pull_marks_completed_on_clean_run(pull_env, monkeypatch):
    snapshot_sync, state = pull_env
    monkeypatch.setattr(snapshot_sync, "post",
                        lambda route, body, token=None: {"ops": [], "last_remote_id": 0})
    regen_gate.reset()

    snapshot_sync.pull_and_replay("token", tolerate_no_snapshot=True)

    assert regen_gate.pull_completed()
    assert state["last_pull_ok_at"]


def test_pull_does_not_mark_completed_when_single_op_fails(pull_env, monkeypatch):
    """单条 op 回放失败时游标照样前移（该行就此跳过），因此绝不能置位。"""
    snapshot_sync, state = pull_env
    bad_op = {"instance_id": "dev-a", "local_id": 7, "username": "u1",
              "type": "regen", "key": "energy", "op": "set",
              "value": "not-a-number", "happened_at": "2026-11-09T05:00:00"}
    monkeypatch.setattr(snapshot_sync, "post",
                        lambda route, body, token=None: {"ops": [bad_op], "last_remote_id": 7})
    regen_gate.reset()

    report = snapshot_sync.pull_and_replay("token", tolerate_no_snapshot=True)

    assert report.errors
    assert not regen_gate.pull_completed()
    assert "last_pull_ok_at" not in state


def test_pull_pending_does_not_mark_completed(pull_env, monkeypatch):
    """非初始设备未允许覆盖时 pull 提前返回，闸门必须保持关闭。"""
    snapshot_sync, state = pull_env
    monkeypatch.setattr(snapshot_sync, "remote_snapshots", lambda token: [{"id": 8}])
    monkeypatch.setattr(snapshot_sync, "overwrite_warnings", lambda token: [{"user": "u1"}])
    regen_gate.reset()

    report = snapshot_sync.pull_and_replay("token")

    assert report.pending
    assert not regen_gate.pull_completed()
    assert "last_pull_ok_at" not in state


# ─── 实时恢复：闸门关时不落库，但消耗仍按实时值计算 ─────────────


def test_realtime_recovery_not_persisted_but_progress_conserved(env, monkeypatch):
    """闸门关闭时实时固化不落库；本次消耗按带小数的实时值算，进度不丢。"""
    from lvjiang.core.profile.service import profile_action

    _session(monkeypatch, True)

    # 0.125/分钟 × 12.8 分钟 = +1.6 点 → 实时值 11.6
    started = datetime.now() - timedelta(minutes=12.8)
    repository.db_upsert("u1", "regen", "stamina", 10,
                         updated_at=started.isoformat(timespec="seconds"))

    profile_action("u1", "stamina", delta=-1, source="试剑")

    entry = repository.db_read_entry("u1", "regen", "stamina")
    assert entry["value"] == 10                      # 11.6 − 1 = 10.6，入库取整为 10
    backdated = datetime.fromisoformat(entry["updated_at"])
    # 0.6 点进度回拨进锚点：0.6 × 60s / 0.125 = 288s
    assert 280 <= (datetime.now() - backdated).total_seconds() <= 300

    rows = repository.db_get_history("u1", "regen", "stamina")
    assert len(rows) == 1 and rows[0]["change_type"] == "delta"
    assert rows[0]["old_value"] == 10 and rows[0]["new_value"] == 10
    assert rows[0]["delta_value"] == 0               # 净值为入库值之差
    assert all(row["change_type"] != "set" for row in rows), "闸门关时不得写固化 set"


# ─── 登录态缓存：主动作废 + 「读不到」按有登录态处理 ────────────


def test_session_cache_invalidation_and_unknown_is_fail_closed(env, monkeypatch):
    from lvjiang.core.account import service as account_service

    current = {"state": False}
    monkeypatch.setattr(account_service, "stored_session_state", lambda: current["state"])
    regen_gate.reset()
    assert period_recovery_allowed()                 # 确实没有凭据 → 放行

    current["state"] = True                          # 登录了，但缓存还是旧结论
    assert period_recovery_allowed()
    regen_gate.invalidate_session_cache()
    assert not period_recovery_allowed()             # 有凭据 → 拦住

    current["state"] = None                          # 凭据库读不到 → 保守拦住
    regen_gate.invalidate_session_cache()
    assert not period_recovery_allowed()


def test_login_path_invalidates_gate_cache(env, monkeypatch):
    """登录/登出统一走 _store_refresh_token，必须作废闸门的登录态缓存。"""
    from lvjiang.core.account import service as account_service

    class Backend:
        def __init__(self):
            self.tokens: dict[str, str] = {}

        def get_password(self, _service, user):
            return self.tokens.get(user)

        def set_password(self, _service, user, token):
            self.tokens[user] = token

        def delete_password(self, _service, user):
            self.tokens.pop(user, None)

    backend = Backend()
    monkeypatch.setattr(account_service, "_credentials", lambda: backend)
    monkeypatch.setattr(account_service, "_keyring_user", lambda: "u-test")
    monkeypatch.setattr(account_service, "stored_session_state", lambda: True)
    regen_gate.reset()
    regen_gate._session_cache = (time.monotonic(), False)   # 人为塞一个旧结论

    assert period_recovery_allowed()                  # 用旧缓存放行
    account_service._store_refresh_token("refresh-x")  # 登录
    assert regen_gate._session_cache is None           # 缓存已作废
    assert not period_recovery_allowed()
