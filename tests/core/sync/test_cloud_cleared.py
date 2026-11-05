"""云端清空状态的明确判定 + 本机自动复位。

核心约束：**「问不到」绝不能当成「云端为空」**——否则一次网络抖动就会把
本地同步状态清掉，用户被迫重做初始同步。
"""
from __future__ import annotations

import pytest

from lvjiang.core.sync import initial
from lvjiang.core.sync.client import SyncError


def _stub(monkeypatch, response):
    def fake_post(route, data, token):
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(initial, "post", fake_post)


def test_not_inited_true_only_on_explicit_flag(monkeypatch):
    _stub(monkeypatch, {"not_inited": True, "snapshot_count": 0, "users": 0})
    assert initial.cloud_not_inited("t") is True


def test_not_inited_false_when_cloud_has_data(monkeypatch):
    _stub(monkeypatch, {"not_inited": False, "snapshot_count": 1, "users": 3})
    assert initial.cloud_not_inited("t") is False


def test_network_failure_is_unknown_not_empty(monkeypatch):
    """网络/鉴权失败必须返回 None（未知），不能当成云端为空。"""
    _stub(monkeypatch, SyncError("账号服务暂时不可用"))
    assert initial.cloud_not_inited("t") is None


@pytest.mark.parametrize("payload", [
    {},                                  # 老服务端没有该字段
    {"not_inited": "true"},              # 类型不对
    {"not_inited": 1},
    {"empty": True},                     # 只有旧字段
])
def test_missing_or_malformed_flag_is_unknown(monkeypatch, payload):
    _stub(monkeypatch, payload)
    assert initial.cloud_not_inited("t") is None


def test_reset_local_after_cloud_clear_returns_notice(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(initial, "clear_local_sync_state", lambda: calls.append("clear"))
    monkeypatch.setattr(initial, "set_sync_established", lambda v: calls.append(str(v)))

    message = initial.reset_local_after_cloud_clear()

    assert calls == ["clear", "False"]
    assert "初始同步" in message
