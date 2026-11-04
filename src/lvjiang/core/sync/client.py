"""文档通道传输层：manifest / pull / push；409 冲突把服务端现行版带给调用方。"""
from __future__ import annotations

import json
from urllib import error as urlerror
from urllib import request as urlrequest

from ..account.service import ACCOUNT_SERVICE

_USER_AGENT = "lvjiang-account-client"


class SyncError(Exception):
    """文档通道请求失败；message 可直接展示。"""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


class SyncConflict(SyncError):
    """push 乐观锁冲突；remote 是服务端现行版（含 payload_base64）。"""

    def __init__(self, remote: dict):
        super().__init__("云端文档已被另一设备更新", code="conflict")
        self.remote = remote


def _request(method: str, route: str, data: dict | None, token: str) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": _USER_AGENT, "Authorization": f"Bearer {token}"}
    request = urlrequest.Request(
        ACCOUNT_SERVICE + route,
        data=None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8"),
        headers=headers, method=method)
    try:
        with urlrequest.build_opener().open(request, timeout=30) as response:
            raw = response.read(32_000_000)
    except urlerror.HTTPError as exc:
        try:
            details = json.loads(exc.read(32_000_000).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, OSError):
            details = {}
        if exc.code == 409 and isinstance(details, dict) and details.get("error") == "conflict":
            raise SyncConflict(details) from None
        code = details.get("error") if isinstance(details, dict) and isinstance(details.get("error"), str) else ""
        # 与账户层共用一套中文文案：界面只展示 message，不把 error code 抛给用户。
        from ..account.service import ERROR_MESSAGES

        message = ERROR_MESSAGES.get(str(code)) or f"同步请求失败（HTTP {exc.code}）"
        raise SyncError(message, code=str(code)) from None
    except (urlerror.URLError, TimeoutError, OSError):
        raise SyncError("账号服务暂时不可用，同步未完成") from None
    try:
        result = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise SyncError("同步响应格式无效") from None
    if not isinstance(result, dict):
        raise SyncError("同步响应格式无效")
    return result


def manifest(token: str) -> list[dict]:
    return list(_request("POST", "/api/sync/manifest", {}, token).get("documents") or [])


def post(route: str, data: dict, token: str) -> dict:
    """向账号服务发一次带 Bearer 的 POST；供 profile 账本通道复用。"""
    return _request("POST", route, data, token)


def pull(token: str, label: str, kind: str) -> dict:
    return _request("POST", "/api/sync/pull", {"label": label, "kind": kind}, token)


def pull_all(token: str) -> list[dict]:
    """一次拉取账号下全部文档 payload（批量接口，避免逐份请求）。"""
    return list(_request("POST", "/api/sync/documents/pull", {}, token).get("documents") or [])


def push(token: str, label: str, kind: str, sha256: str,
         payload_base64: str, expected_sha256: str | None = None) -> dict:
    data = {"label": label, "kind": kind, "sha256": sha256, "payload_base64": payload_base64}
    if expected_sha256 is not None:
        data["expected_sha256"] = expected_sha256
    return _request("POST", "/api/sync/push", data, token)
