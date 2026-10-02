"""把本地 APK 临时挂到局域网，供手机扫码安装。

手机能上网时直接扫 GitHub 直链就行，这条路是给**手机上不了网**的情况：PC 挂代理、
手机没流量、或者内网环境。所以它是可选项而不是默认路径。

只服务一个文件、只在对话框打开期间存活、不列目录——临时开放一个端口就该把暴露面
压到最小。
"""
from __future__ import annotations

import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from loguru import logger

_CHUNK = 256 * 1024


class _SingleFileHandler(BaseHTTPRequestHandler):
    """只认一个路径，其余一律 404。"""

    server_version = "lvjiang-apk/1.0"

    def __init__(self, *args, file_path: Path, route: str, **kwargs):
        self._file_path = file_path
        self._route = route
        super().__init__(*args, **kwargs)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        logger.debug(f"[APK 服务] {self.address_string()} {fmt % args}")

    def do_HEAD(self) -> None:  # noqa: N802
        self._respond(body=False)

    def do_GET(self) -> None:  # noqa: N802
        self._respond(body=True)

    def _respond(self, *, body: bool) -> None:
        if self.path != self._route:
            self.send_error(404)
            return
        try:
            size = self._file_path.stat().st_size
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        # 用 vnd.android.package-archive 才能让手机浏览器按安装包处理
        self.send_header("Content-Type", "application/vnd.android.package-archive")
        self.send_header("Content-Length", str(size))
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{self._file_path.name}"')
        self.end_headers()
        if not body:
            return
        with open(self._file_path, "rb") as stream:
            while True:
                block = stream.read(_CHUNK)
                if not block:
                    break
                try:
                    self.wfile.write(block)
                except (BrokenPipeError, ConnectionResetError):
                    # 手机取消下载是常事，不值得当错误记
                    logger.debug("[APK 服务] 客户端提前断开")
                    return


class ApkLanServer:
    """把一个 APK 文件挂在局域网上，`stop()` 或退出上下文即关闭。"""

    def __init__(self, file_path: Path, port: int = 0):
        self._file_path = Path(file_path)
        self._route = f"/{self._file_path.name}"
        handler = partial(
            _SingleFileHandler, file_path=self._file_path, route=self._route)
        # 绑 0.0.0.0：手机要从另一台设备连进来；端口交给系统挑，避免撞占用
        self._httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="apk-lan-server",
            daemon=True)

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def route(self) -> str:
        return self._route

    def url_for(self, host: str) -> str:
        """给定本机地址拼出手机该访问的 URL。

        服务绑在 0.0.0.0，所以同一个服务在本机**任何**地址上都可达——换地址
        只是换一个展示用的 host，不需要重启服务。
        """
        return f"http://{host}:{self.port}{self._route}"

    def start(self) -> "ApkLanServer":
        self._thread.start()
        logger.info(f"[APK 服务] 已启动: 端口 {self.port} 路径 {self._route}")
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        logger.info("[APK 服务] 已停止")

    def __enter__(self) -> "ApkLanServer":
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()


def lan_addresses() -> list[str]:
    """可供手机访问的本机局域网地址，按接口顺序。"""
    from .wireless import list_ipv4_interfaces

    return [iface.ip for iface in list_ipv4_interfaces()]
