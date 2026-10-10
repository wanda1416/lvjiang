"""Local MCP transport. Domain tools and authorization belong to the plugin."""
from __future__ import annotations

import asyncio
import functools
import inspect
import json
import secrets
import socket
import threading
from collections.abc import Callable
from typing import Any

LV1_REQUIRED_MESSAGE = "智能调律需要激活 Lv1，请在设置的「功能激活」中激活"
DEFAULT_MCP_PORT = 18765


def has_agent_access() -> bool:
    """Use the application's authoritative current entitlement."""
    from .license import has_feature

    return has_feature("lv1")


class LocalMCPServer:
    """Explicitly enabled loopback server, owned by the main instance."""

    def __init__(self, tools: dict[str, Callable], *, instructions: str,
                 lv1_check: Callable[[], bool] | None = None,
                 port: int = DEFAULT_MCP_PORT, token: str | None = None):
        from mcp.server.fastmcp import FastMCP

        self.mcp = FastMCP(
            "lvjiang", instructions=instructions, host="127.0.0.1",
            stateless_http=True, json_response=True,
        )
        for name, function in tools.items():
            self.mcp.add_tool(self._async_tool(function), name=name)
        if not 1 <= port <= 65535:
            raise ValueError("MCP 端口需在 1–65535 之间")
        self.token = token or secrets.token_urlsafe(32)
        self.port = port
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._accepting = False
        self._lv1_check = lv1_check or has_agent_access

    @staticmethod
    def _async_tool(function: Callable) -> Callable:
        @functools.wraps(function)
        async def invoke(*args, **kwargs):
            return await asyncio.to_thread(function, *args, **kwargs)

        invoke.__signature__ = inspect.signature(function)  # type: ignore[attr-defined]
        return invoke

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        import uvicorn

        from .access import is_readonly

        if is_readonly():
            raise PermissionError("只有主实例可以启动 MCP 服务")

        if self.running:
            return
        token = self.token
        self._accepting = True
        app = self.mcp.streamable_http_app()

        async def authenticated(scope, receive, send):
            if scope["type"] != "http":
                await app(scope, receive, send)
                return
            headers = dict(scope.get("headers", []))
            expected = f"Bearer {token}".encode("ascii")
            if not self._accepting or not secrets.compare_digest(headers.get(b"authorization", b""), expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
            # Gate the whole protocol, including initialization, discovery and resources.
            # Check every request so an existing connection cannot outlive its entitlement.
            if not self._lv1_check():
                from starlette.requests import Request

                denial: dict = {"error": {"code": "lv1_required", "message": LV1_REQUIRED_MESSAGE}}
                status = 403
                if scope["method"] == "POST":
                    try:
                        request = await Request(scope, receive).json()
                    except ValueError:
                        request = None
                    if isinstance(request, dict) and request.get("jsonrpc") == "2.0" and "id" in request:
                        # Protocol errors let MCP clients display the activation hint,
                        # rather than reducing it to a generic HTTP status failure.
                        denial = {"jsonrpc": "2.0", "id": request["id"], "error": {
                            "code": -32000, "message": LV1_REQUIRED_MESSAGE,
                            "data": {"code": "lv1_required"},
                        }}
                        status = 200
                body = json.dumps(denial, ensure_ascii=False).encode("utf-8")
                await send({"type": "http.response.start", "status": status,
                            "headers": [(b"content-type", b"application/json; charset=utf-8")]})
                await send({"type": "http.response.body", "body": body})
                return
            await app(scope, receive, send)

        sock = socket.socket()
        try:
            sock.bind(("127.0.0.1", self.port))
        except OSError:
            sock.close()
            self._accepting = False
            raise OSError(f"MCP 端口 {self.port} 无法使用，请检查占用并手动修改端口") from None
        sock.listen(32)
        self.port = sock.getsockname()[1]
        self._socket = sock
        self._server = uvicorn.Server(uvicorn.Config(
            authenticated, log_level="error", access_log=False, lifespan="on",
            timeout_graceful_shutdown=2,
        ))
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [sock]}, daemon=True,
            name="lvjiang-mcp",
        )
        self._thread.start()

    def stop(self) -> None:
        self._accepting = False
        if self._server is not None:
            self._server.should_exit = True

    def connection_config(self) -> dict:
        if not self.running or not self._accepting:
            raise ValueError("请先开启 MCP 接入")
        return {"mcpServers": {"lvjiang": {
            "type": "streamableHttp",
            "url": f"http://127.0.0.1:{self.port}/mcp",
            "headers": {"Authorization": f"Bearer {self.token}"},
        }}}
