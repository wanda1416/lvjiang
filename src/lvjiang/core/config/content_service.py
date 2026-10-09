"""HTTPS 内容授权接口；管理端与客户端共用，不输出请求正文。"""
from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE = 256 * 1024


class ServiceError(RuntimeError):
    def __init__(self, message: str, status: int = 0, *, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def service_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password \
            or parsed.query or parsed.fragment:
        raise ValueError("内容服务必须使用不含凭据的 HTTPS URL")
    return value.rstrip("/")


def request_json(base: str, route: str, data: dict[str, Any], *,
                 token: str = "", timeout: float = 10) -> dict[str, Any]:
    url = service_url(base) + route
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "lvjiang-content-client"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                      headers=headers, method="POST")
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except HTTPError as error:
        code = ""
        try:
            details = json.loads(error.read(MAX_RESPONSE + 1).decode("utf-8"))
            candidate = details.get("error") if isinstance(details, dict) else None
            if isinstance(candidate, str) and len(candidate) <= 64:
                code = candidate
        except (ValueError, UnicodeDecodeError, OSError):
            pass
        raise ServiceError(f"内容服务请求失败 HTTP {error.code}", error.code, code=code) from None
    except (URLError, TimeoutError, OSError):
        raise ServiceError("内容服务暂时无法连接") from None
    if len(raw) > MAX_RESPONSE:
        raise ServiceError("内容服务响应超限")
    try:
        result = json.loads(raw.decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, UnicodeDecodeError):
        raise ServiceError("内容服务响应格式无效") from None
