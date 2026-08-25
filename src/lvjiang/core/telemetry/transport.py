"""HTTPS POST 传输层：唯一负责真正发网络请求的地方。

契约（全部由测试兜底）：
- 只接受 ``ValidatedEvent`` 的信封，不接受裸 dict；
- 目标 URL 必须是 https（本机联调例外：127.0.0.1/localhost 允许 http）；
- 响应体**完全忽略**——服务端返回任何合法 JSON 指令都不能改变客户端
  状态，统计通道绝不能变成远程控制通道；
- 任何异常（超时/DNS/证书/…）统一收敛成返回 False，绝不外抛、绝不重试。
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from loguru import logger

DEFAULT_TELEMETRY_URL = "https://lvjiang-stats.wyxj.net/v1/report"
TIMEOUT_SECONDS = 5.0
_UA = "lvjiang-telemetry-reporter"


def telemetry_url() -> str:
    """联调用：``LVJIANG_TELEMETRY_URL`` 环境变量可覆盖默认地址。"""
    return os.environ.get("LVJIANG_TELEMETRY_URL") or DEFAULT_TELEMETRY_URL


def _is_allowed_scheme(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")


def post_report(envelope: dict) -> bool:
    """POST 一份信封 dict，返回是否成功（HTTP 2xx）。不抛出任何异常。"""
    url = telemetry_url()
    if not _is_allowed_scheme(url):
        logger.debug(f"[telemetry] 拒绝非 https 地址: {url}")
        return False
    try:
        body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        req = Request(
            url, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": _UA,
            },
        )
        with urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            response.read()  # 读完但丢弃，不解析——见模块 docstring
            return 200 <= response.status < 300
    except Exception as e:  # noqa: BLE001 —— 统计上报绝不能因网络问题影响主流程
        logger.debug(f"[telemetry] 上报失败（已忽略）: {e}")
        return False
