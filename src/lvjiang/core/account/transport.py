"""账号服务的 HTTP 传输：请求组装、重试与错误分类。

**错误分类是这里最重要的一件事**：只有服务端明确说「这个凭据/会话不行」时才允许
清本地登录态；网络失败、5xx、网关拦截都只说明「现在问不到」，把它们当成「密码
错了」会让一次网关抖动就把用户踢下线。

本模块不读模块级全局：地址、超时、文案表与底层 opener 都由装配层
（:mod:`.service`）传入，测试替换 ``service._request`` / ``service._open_once``
即可拦住整条链路。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from http.client import HTTPException
from urllib import error as urlerror
from urllib import request as urlrequest

from loguru import logger

from .errors import AccountError

#: 自定义 UA：urllib 默认的 Python-urllib 会被 Cloudflare Bot 防护在边缘直接
#: 403（请求到不了 Worker），网页与内容服务客户端同理。
USER_AGENT = "lvjiang-account-client"

#: 一次失败后的重试间隔（秒）。手机弱网/IPv6-only 握手超时很常见。
RETRY_DELAY_S = 0.8


def network_hint(error: BaseException) -> str:
    """把底层网络异常翻成用户能照着做的中文（原文只进日志）。"""
    text = str(error)
    if "handshake" in text.lower():
        return "和账号服务握手超时"
    if "Network is unreachable" in text or "Errno 101" in text:
        return "手机当前网络连不到账号服务"
    if "timed out" in text.lower() or isinstance(error, TimeoutError):
        return "连接账号服务超时"
    if "Name or service not known" in text or "getaddrinfo" in text:
        return "域名解析失败"
    return "连不上账号服务"


def open_once(
    request: urlrequest.Request,
    timeout: float | None,
    *,
    default_timeout: float,
) -> bytes:
    """发一次请求；网络类异常原样抛出，交给调用方决定是否重试。"""
    with urlrequest.build_opener().open(
            request, timeout=timeout or default_timeout) as response:
        return response.read(1_000_001)


def http_error(
    exc: urlerror.HTTPError,
    *,
    messages: dict[str, str],
    rejected: frozenset[str],
) -> AccountError:
    """把 HTTP 错误分成三类，**只有第一类允许清本地登录态**。

    1. 认证被拒：响应体里带 Worker 的显式错误码（``invalid_credentials`` /
       ``token_expired`` / ``account_disabled`` / ``device_revoked``）；
    2. 服务不可用：5xx（Cloudflare 502 之类）、非 JSON 响应；
    3. 其它请求错误：网关/防火墙拦截（典型：Cloudflare Bot 防护返回 403 HTML）。

    第 2、3 类都是「现在拿不到答案」，绝不能当成「你密码错了」。
    """
    code = ""
    try:
        details = json.loads(exc.read(65_536).decode("utf-8"))
        if isinstance(details, dict) and isinstance(details.get("error"), str):
            code = details["error"]
    except (ValueError, UnicodeDecodeError, OSError):
        pass
    if exc.code == 426 and not code:
        code = "client_upgrade_required"
    if exc.code >= 500:
        return AccountError(messages["service_unavailable"],
                            code=code or "service_unavailable")
    if code == "client_upgrade_required":
        return AccountError(
            messages.get(code, "云端已启用全文档端到端加密，请升级客户端后再同步"),
            code=code,
        )
    if code in rejected:
        return AccountError(messages.get(code, f"账号服务请求失败（HTTP {exc.code}）"),
                            code=code)
    if code:
        return AccountError(messages.get(code, f"账号服务请求失败（HTTP {exc.code}）"),
                            code=code)
    return AccountError(f"账号服务拒绝了请求（HTTP {exc.code}），请稍后重试",
                        code="rejected")


def build_request(
    method: str,
    url: str,
    data: dict | None,
    token: str,
) -> urlrequest.Request:
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urlrequest.Request(
        url,
        data=None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8"),
        headers=headers, method=method)


def request(
    method: str,
    url: str,
    data: dict | None,
    *,
    token: str,
    timeout: float | None,
    default_timeout: float,
    open_once: Callable[..., bytes],
    http_error: Callable[[urlerror.HTTPError], AccountError],
) -> dict:
    """发一次业务请求并解析 JSON 响应。

    网络类失败重试一次：手机（尤其 IPv6-only 的 5G）握手超时、短暂不可达很常见，
    一次失败就报错会让用户看到「什么都没发生」。服务端返回的错误不重试。
    """
    prepared = build_request(method, url, data, token)
    errors: list[Exception] = []
    for attempt in range(2):
        try:
            raw = open_once(prepared, timeout)
            break
        except urlerror.HTTPError as exc:
            raise http_error(exc) from None
        except (urlerror.URLError, TimeoutError, OSError, HTTPException) as exc:
            errors.append(exc)
            if attempt == 0:
                logger.warning("账号服务连接失败（重试一次）：{}", exc)
                time.sleep(RETRY_DELAY_S)
    else:                                        # 两次都是网络类失败
        logger.warning("账号服务连接失败（重试后仍失败）：{}", errors[-1])
        raise AccountError(f"{network_hint(errors[-1])}，请检查网络后重试",
                           code="network") from None
    try:
        result = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise AccountError("账号服务响应格式无效", code="service_unavailable") from None
    if not isinstance(result, dict):
        raise AccountError("账号服务响应格式无效", code="service_unavailable")
    return result
