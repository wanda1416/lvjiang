"""OpenAI 兼容 Chat Completions 适配器；不包含游戏知识或 UI。"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx


class AIError(Exception):
    """可展示的固定错误；禁止把响应正文、凭据或请求地址拼入错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AISettings:
    base_url: str = ""
    model: str = ""
    timeout: float = 30.0

    def validated(self) -> AISettings:
        url = self.base_url.strip().rstrip("/")
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise AIError("config", "接口地址无效") from None
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or port == 0):
            raise AIError("config", "请填写完整的 HTTP/HTTPS 接口根地址，不含凭据、查询参数或片段")
        if not self.model.strip():
            raise AIError("config", "请填写模型名称")
        if not 1 <= self.timeout <= 300:
            raise AIError("config", "请求超时应为 1 至 300 秒")
        return AISettings(url, self.model.strip(), self.timeout)


@dataclass(frozen=True)
class AIReply:
    text: str
    model: str
    elapsed: float
    usage: dict[str, int] | None


class AIService:
    """每次调用使用冻结的设置；总超时可取消，不自动重试或跟随重定向。"""

    def __init__(self, settings: AISettings, api_key: str = "", *,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings.validated()
        self._api_key = api_key.strip()
        self._transport = transport

    async def complete(self, messages: list[dict[str, str]]) -> AIReply:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.settings.timeout):
                async with httpx.AsyncClient(
                    timeout=self.settings.timeout, follow_redirects=False,
                    transport=self._transport,
                ) as client:
                    response = await client.post(
                        self.settings.base_url + "/chat/completions",
                        headers=headers,
                        json={"model": self.settings.model, "messages": messages, "stream": False},
                    )
        except (TimeoutError, httpx.TimeoutException):
            raise AIError("timeout", "请求超时，请检查网络或增加超时时间") from None
        except httpx.RequestError:
            raise AIError("network", "连接失败，请检查接口地址、网络和证书") from None
        status = response.status_code
        if status in {401, 403}:
            raise AIError("auth", "鉴权失败，请检查 API Key 和模型访问权限")
        if status == 404:
            raise AIError("not_found", "接口或模型不存在，请检查根地址和模型名称")
        if status == 429:
            raise AIError("rate_limit", "服务限流或额度不足，请检查服务商账户")
        if not 200 <= status < 300:
            raise AIError("http", f"服务请求失败（HTTP {status}）")
        try:
            data: Any = response.json()
            choice = data["choices"][0]
            message = choice["message"]
            if message.get("refusal"):
                raise AIError("refusal", "模型拒绝了请求")
            if choice.get("finish_reason") == "length":
                raise AIError("truncated", "模型回复被截断")
            text = message["content"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty content")
            usage = data.get("usage")
            usage = ({k: v for k, v in usage.items()
                      if k in {"prompt_tokens", "completion_tokens", "total_tokens"}
                      and isinstance(v, int) and not isinstance(v, bool)}
                     if isinstance(usage, dict) else None)
            model = data.get("model")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AIError("response", "服务未返回有效的文本回复，请确认支持 Chat Completions") from None
        return AIReply(text.strip(), model if isinstance(model, str) else self.settings.model,
                       time.monotonic() - started, usage)

    async def test_connection(self) -> AIReply:
        return await self.complete([{"role": "user", "content": "Reply only with OK."}])
