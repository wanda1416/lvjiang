"""Android 账户桥接：登录态走进程内内存快照。

回归点：早期每次调用都新建 ``AccountSession`` 并 ``restore()``，即
「进一次设置页 → refresh + summary 两个网络请求」。这里锁住三件事：
重复取状态只探测一次、登录后缓存直接就是已登录、登出后缓存翻成未登录。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from lvjiang.core.account.service import AccountError, current_instance_id
from lvjiang.core.ondevice import account_api


@pytest.fixture(autouse=True)
def reset_module_state(monkeypatch):
    # 会话已收敛到进程内单例（core.account.service.get_session），这里改成重置它；
    # 手机端桥接不再自己持有 _SESSION。
    from lvjiang.core.account import service

    service.reset_session()
    monkeypatch.setattr(account_api, "_STATUS_CACHE", None)
    yield
    service.reset_session()


def test_status_probes_only_once_per_process(monkeypatch) -> None:
    calls: list[int] = []

    def probe() -> dict:
        calls.append(1)
        return {"ok": True, "logged_in": True, "login": "wang", "display_name": "王"}

    monkeypatch.setattr(account_api, "_probe_status", probe)

    first = json.loads(account_api.account_status())
    second = json.loads(account_api.account_status())

    assert first == second == {"ok": True, "logged_in": True,
                               "login": "wang", "display_name": "王",
                               "instance_id": current_instance_id()}
    assert len(calls) == 1


def test_probe_prefers_memory_session_over_network(monkeypatch) -> None:
    """本进程已登录时，状态来自 account.json，不再读凭据库、更不联网。"""
    session = account_api._session()
    session.access_token = "access"
    monkeypatch.setattr(account_api, "load_profile",
                        lambda: {"login": "wang", "display_name": "王"})
    monkeypatch.setattr(session, "has_stored_session",
                        lambda: pytest.fail("已登录时不应再读凭据库"))

    status = account_api._probe_status()

    assert status["logged_in"] is True
    assert status["login"] == "wang"


def test_profile_answers_without_network(monkeypatch) -> None:
    """有 refresh 令牌 + account.json 里的账号名 → 直接已登录，不打网络。"""
    session = account_api._session()
    monkeypatch.setattr(session, "has_stored_session", lambda: True)
    monkeypatch.setattr(account_api, "load_profile",
                        lambda: {"login": "wang", "display_name": "王"})
    monkeypatch.setattr(session, "restore",
                        lambda: pytest.fail("有账号名时不该联网"))

    assert account_api._probe_status() == {
        "ok": True, "logged_in": True, "login": "wang", "display_name": "王"}


def test_stored_token_stays_logged_in_when_network_fails(monkeypatch) -> None:
    """网络失败不能把已登录翻成未登录——这正是设置页一会儿「未登录」
    一会儿「已登录」来回跳的原因。"""
    session = account_api._session()
    session.access_token = ""
    monkeypatch.setattr(session, "has_stored_session", lambda: True)
    monkeypatch.setattr(account_api, "load_profile", lambda: {})

    def offline():
        raise AccountError("和账号服务握手超时，请检查网络后重试", code="network")

    monkeypatch.setattr(session, "restore", offline)

    status = account_api._probe_status()

    assert status["logged_in"] is True
    assert status["login"] == ""


def test_login_warms_cache_without_extra_probe(monkeypatch) -> None:
    monkeypatch.setattr(account_api, "_probe_status",
                        lambda: pytest.fail("登录后不该再探测"))
    session = account_api._session()
    monkeypatch.setattr(session, "login", lambda login, password, **_kwargs: {
        "account": {"login": login, "display_name": "王"},
        "access_token": "access", "access_expires_at": "later"})

    result = json.loads(account_api.account_login("wang", "pw"))
    status = json.loads(account_api.account_status())

    assert result["ok"] is True
    assert status == {"ok": True, "logged_in": True, "online": True,
                      "login": "wang", "display_name": "王",
                      "instance_id": current_instance_id()}


def test_logout_clears_cached_status(monkeypatch) -> None:
    monkeypatch.setattr(account_api, "_STATUS_CACHE", {
        "ok": True, "logged_in": True, "login": "wang", "display_name": ""})
    session = account_api._session()
    monkeypatch.setattr(session, "logout", lambda: None)

    account_api.account_logout()

    assert json.loads(account_api.account_status())["logged_in"] is False


def test_status_always_exposes_local_device_id(monkeypatch) -> None:
    """设备 ID 是本地事实：未登录、断网也必须在。

    它在找回密码流程里是唯一的身份凭据——跟登录态一起缓存或跟着网络失败一起
    消失，用户就没法把它报给管理员了。
    """
    monkeypatch.setattr(account_api, "_STATUS_CACHE", {"ok": True, "logged_in": False})

    status = json.loads(account_api.account_status())

    assert status["logged_in"] is False
    assert status["instance_id"] == current_instance_id()


def test_devices_list_marks_self_and_online(monkeypatch) -> None:
    """「账户 → 设备」行的数据源：本机标记与在线判定都来自 /api/summary。"""
    from lvjiang.core.account.devices import ONLINE_WINDOW_MINUTES

    session = account_api._session()
    session.access_token = "access"
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(minutes=ONLINE_WINDOW_MINUTES - 1)).isoformat()
    stale = (now - timedelta(minutes=ONLINE_WINDOW_MINUTES + 1)).isoformat()
    first_seen = (now - timedelta(days=3)).isoformat()
    monkeypatch.setattr(session, "summary", lambda: {
        "instance_id": "dev-self",
        "devices": [
            {"id": "dev-self", "name": "Pixel 7", "platform": "android",
             "last_seen": fresh, "first_seen": first_seen},
            {"id": "dev-pc", "name": "书房PC", "platform": "windows", "last_seen": stale},
        ]})

    result = json.loads(account_api.account_devices())

    assert [result["ok"], result["logged_in"], result["online"]] == [True, True, 1]
    assert [item["self"] for item in result["devices"]] == [True, False]
    assert result["devices"][0]["status"] == "在线"
    assert result["devices"][1]["status"].startswith("最后在线")
    # 同型号两台手机只能靠「首次出现」区分；服务端缺这个字段时留空，不编造
    assert result["devices"][0]["since"] == "首次出现 3 天前"
    assert result["devices"][1]["since"] == ""


def test_devices_list_is_empty_when_not_logged_in(monkeypatch) -> None:
    """没登录不是错误：设备列表只能来自 summary，如实返回空表让界面说「登录后可见」。"""
    session = account_api._session()
    session.access_token = ""
    monkeypatch.setattr(session, "restore", lambda *, fetch_summary=True: None)

    result = json.loads(account_api.account_devices())

    assert result == {"ok": True, "logged_in": False, "devices": []}


def test_profile_status_surfaces_last_sync_failure(monkeypatch) -> None:
    """后台每分钟一轮同步失败，必须能从 profile 状态里读到。

    此前失败只进 ``_sync_round`` 返回的 dict 然后被丢弃，设置页「数据同步」摘要
    行永远显示「数据来源：云端」，只有手动点「立即同步」才看得到 toast。
    """
    monkeypatch.setattr(account_api, "_LAST_SYNC",
                        {"at": "10:20:30", "ok": False,
                         "message": "连不上账号服务，同步未完成"})
    monkeypatch.setattr("lvjiang.core.account.service.profile_sync_state", lambda: {})
    monkeypatch.setattr(account_api, "_author_stamp", lambda: "inst")

    result = json.loads(account_api.account_profile_status())

    assert result["ok"] is True
    assert result["last_sync_error"] == "连不上账号服务，同步未完成"
    assert result["last_sync_at"] == "10:20:30"
    assert result["syncing"] is False


def test_profile_status_has_no_error_after_a_clean_round(monkeypatch) -> None:
    monkeypatch.setattr(account_api, "_LAST_SYNC",
                        {"at": "10:21:00", "ok": True, "message": ""})
    monkeypatch.setattr("lvjiang.core.account.service.profile_sync_state", lambda: {})
    monkeypatch.setattr(account_api, "_author_stamp", lambda: "inst")

    result = json.loads(account_api.account_profile_status())

    assert result["last_sync_error"] == ""
    assert result["last_sync_at"] == "10:21:00"
