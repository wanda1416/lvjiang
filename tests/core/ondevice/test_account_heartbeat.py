"""手机前台心跳在进程重启后先用 refresh 令牌换 access 令牌。"""
from __future__ import annotations

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
    monkeypatch.setattr(account_api, "_sync", lambda token: synced.append(token))

    assert account_api._heartbeat_tick(0) == 1

    assert session.restore_calls == 1
    assert synced == ["access"]


def test_heartbeat_skips_minute_when_restore_fails(monkeypatch):
    session = _Session(restored=None)
    monkeypatch.setattr(account_api, "_session", lambda: session)
    monkeypatch.setattr(account_api, "_sync", lambda token: (_ for _ in ()).throw(AssertionError(token)))

    assert account_api._heartbeat_tick(4) == 4
    assert session.beats == 0


def test_heartbeat_skips_minute_when_restore_raises(monkeypatch):
    session = _Session(restored=AccountError("账号服务暂时不可用"))
    monkeypatch.setattr(account_api, "_session", lambda: session)
    monkeypatch.setattr(
        account_api, "_sync",
        lambda token: (_ for _ in ()).throw(AssertionError(token)))

    assert account_api._heartbeat_tick(0) == 0
