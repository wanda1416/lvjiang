"""FastAPI 入口 + ``stats-client`` console-script。

默认只监听 127.0.0.1，不做鉴权——单机单用户工具，见
``ops/stats-client/README.md`` 的隐私与安全边界一节。
"""
from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

_BASE_DIR = Path(__file__).resolve().parents[2]  # ops/stats-client/


def create_app() -> FastAPI:
    app = FastAPI(title="律匠统计控制台")
    static_dir = str(_BASE_DIR / "static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    from .routes import overview, rolls, setup, sync_page
    app.include_router(setup.router)
    app.include_router(overview.router)
    app.include_router(rolls.router)
    app.include_router(sync_page.router)
    return app


app = create_app()


def run() -> None:
    ap = argparse.ArgumentParser(description="律匠统计控制台——仅本机运行")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址（改前先看 README 隐私边界），默认 127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = ap.parse_args()

    if not args.no_browser:
        url = f"http://{args.host}:{args.port}/"
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    run()
