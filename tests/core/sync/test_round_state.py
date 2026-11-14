"""一轮同步共用云端版本行：轮内只请求一次、写后失效、失败不缓存、按版本跳过拉取。"""
from __future__ import annotations

import hashlib

import pytest

from lvjiang.core.sync import round_state
from lvjiang.core.sync.client import SyncError


class Counter:
    def __init__(self, payload=None, fail=False):
        self.calls = 0
        self.payload = payload or {"protocol_version": 2, "history_head": 3}
        self.fail = fail

    def __call__(self, route, data, token):
        assert route == "/api/sync/state"
        self.calls += 1
        if self.fail:
            raise SyncError("账号服务暂时不可用")
        return dict(self.payload)


def test_outside_round_every_call_requests():
    poster = Counter()
    round_state.cloud_state("t", poster)
    round_state.cloud_state("t", poster)
    assert poster.calls == 2


def test_round_caches_until_invalidate():
    poster = Counter()
    with round_state.sync_round():
        round_state.cloud_state("t", poster)
        round_state.cloud_state("t", poster)
        assert poster.calls == 1
        round_state.invalidate()
        round_state.cloud_state("t", poster)
        assert poster.calls == 2
    round_state.cloud_state("t", poster)                 # 出轮后不再缓存
    assert poster.calls == 3


def test_nested_round_reuses_outer_cache():
    poster = Counter()
    with round_state.sync_round():
        round_state.cloud_state("t", poster)
        with round_state.sync_round():
            round_state.cloud_state("t", poster)
        round_state.cloud_state("t", poster)
    assert poster.calls == 1


def test_failure_is_not_cached():
    poster = Counter(fail=True)
    with round_state.sync_round():
        with pytest.raises(SyncError):
            round_state.cloud_state("t", poster)
        poster.fail = False
        assert round_state.cloud_state("t", poster)["history_head"] == 3
        assert poster.calls == 2


def test_version_ignores_missing_and_bool_fields():
    assert round_state.version({}, "users_rev") is None
    assert round_state.version({"users_rev": True}, "users_rev") is None
    assert round_state.version({"users_rev": 0}, "users_rev") == 0
    assert round_state.version({"definition_sha256": "ab"}, "definition_sha256") == "ab"


def test_definition_unchanged_skips_download(monkeypatch, tmp_path):
    """版本行的 definition_sha256 与本地一致 → 不下载整份定义。"""
    from lvjiang.core.sync import definition_sync

    yaml_path = tmp_path / "profile.yaml"
    yaml_path.write_text("quota: []\n", encoding="utf-8")
    local_sha = hashlib.sha256(yaml_path.read_bytes()).hexdigest()
    routes: list[str] = []

    def fake_post(route, data, token):
        routes.append(route)
        if route == "/api/sync/state":
            return {"definition_sha256": local_sha}
        raise AssertionError(route)

    monkeypatch.setattr(definition_sync, "post", fake_post)
    monkeypatch.setattr(definition_sync, "profile_sync_state", lambda: {})
    monkeypatch.setattr(definition_sync, "save_profile_sync_state", lambda s: None)
    monkeypatch.setattr(definition_sync, "_local_sha256", lambda path: local_sha)
    report = definition_sync.sync_definition("t", yaml_path)
    assert report.errors == []
    assert routes == ["/api/sync/state"]
