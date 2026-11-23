"""手机前台心跳：进程重启后先换令牌再同步，且一轮失败不能弄死节拍。

回归点三件事：重启后进程内存没有 access 令牌（先 restore）、任何一轮的意外
异常不能让节拍线程永久停摆（此前只剩一次 traceback 和「再也不自动同步」）、
失败原因要留下来给「数据同步」摘要行显示。
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from lvjiang.core.account.service import AccountError
from lvjiang.core.ondevice import account_api


class _Session:
    def __init__(self, token="", stored=True, restored="access"):
        self.access_token = token
        self.stored = stored
        self.restored = restored
        self.restore_calls = 0
        self.beats = 0

    def has_stored_session(self):
        return self.stored

    def restore(self, *, fetch_summary=True):
        self.restore_calls += 1
        assert fetch_summary is False
        if self.restored is None:
            return None
        if isinstance(self.restored, Exception):
            raise self.restored
        self.access_token = self.restored
        return {"ok": True}

    def heartbeat(self):
        self.beats += 1


def test_heartbeat_restores_token_before_sync(monkeypatch):
    session = _Session()
    synced: list[str] = []
    monkeypatch.setattr(account_api, "_session", lambda: session)
    monkeypatch.setattr(account_api, "_sync",
                        lambda token, **_kwargs: synced.append(token))

    assert account_api._heartbeat_tick(0) == 1

    assert session.restore_calls == 1
    assert synced == ["access"]


def test_heartbeat_skips_minute_when_restore_fails(monkeypatch):
    session = _Session(restored=None)
    monkeypatch.setattr(account_api, "_session", lambda: session)
    monkeypatch.setattr(account_api, "_sync",
                        lambda token, **_kwargs: (_ for _ in ()).throw(AssertionError(token)))

    assert account_api._heartbeat_tick(4) == 4
    assert session.beats == 0


def test_heartbeat_skips_minute_when_restore_raises(monkeypatch):
    session = _Session(restored=AccountError("账号服务暂时不可用"))
    monkeypatch.setattr(account_api, "_session", lambda: session)
    monkeypatch.setattr(
        account_api, "_sync",
        lambda token, **_kwargs: (_ for _ in ()).throw(AssertionError(token)))

    assert account_api._heartbeat_tick(0) == 0


def test_sync_returns_none_when_a_round_is_already_running(monkeypatch):
    """心跳与手动「立即同步」撞车时，心跳这一分钟直接跳过（不排队、不并发）。"""
    monkeypatch.setattr(account_api, "_sync_round",
                        lambda token: pytest.fail("锁被占用时不得再开一轮"))

    assert account_api._SYNC_LOCK.acquire(blocking=False)
    try:
        assert account_api._sync("token", wait=0) is None
    finally:
        account_api._SYNC_LOCK.release()


def test_manual_sync_reports_busy_instead_of_piling_up(monkeypatch):
    """手动同步拿不到锁时给可行动的话，而不是让界面无声等到超时。"""
    session = account_api._session()
    session.access_token = "access"
    monkeypatch.setattr(account_api, "_sync", lambda token, **_kwargs: None)

    result = account_api.account_sync()

    assert json.loads(result) == {"ok": False,
                                  "message": "另一轮同步正在进行，请稍候再试"}


def test_heartbeat_records_failed_round_for_status(monkeypatch):
    """失败原因必须留在模块状态里，否则摘要行只能显示「数据来源：云端」。"""
    session = _Session()
    monkeypatch.setattr(account_api, "_session", lambda: session)
    monkeypatch.setattr(account_api, "_LAST_SYNC", None)
    monkeypatch.setattr(account_api, "_sync", lambda token, **_kwargs: {
        "synced": False, "message": "连不上账号服务，同步未完成"})

    account_api._heartbeat_tick(0)

    assert account_api._LAST_SYNC["ok"] is False
    assert account_api._LAST_SYNC["message"] == "连不上账号服务，同步未完成"
    assert account_api._LAST_SYNC["at"]


def test_heartbeat_loop_keeps_running_after_unexpected_error(monkeypatch):
    """`_sync` 里逃出来的非 AccountError（sqlite / IO）不能让节拍线程停摆。

    此前 `_heartbeat_tick` 只捕获 AccountError，而 `_sync` 链内的 SyncError 早
    被就地收进 report.errors——能穿透的正是这类意外异常，结果是线程直接结束，
    只有下次 onResume 才可能重启，期间「自动同步」完全不存在。
    """
    session = _Session()
    rounds: list[int] = []

    def boom(token, **_kwargs):
        rounds.append(1)
        raise RuntimeError("database is locked")

    stop = threading.Event()
    monkeypatch.setattr(account_api, "_session", lambda: session)
    monkeypatch.setattr(account_api, "_sync", boom)
    monkeypatch.setattr(account_api, "SYNC_INTERVAL_S", 0.01)
    monkeypatch.setattr(account_api, "_heartbeat_stop", stop)

    loop = threading.Thread(target=account_api._heartbeat_loop, daemon=True)
    loop.start()
    deadline = time.monotonic() + 5
    while len(rounds) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    loop.join(2)

    assert len(rounds) >= 3          # 抛了三次异常仍然在跑
    assert not loop.is_alive()
