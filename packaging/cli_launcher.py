"""薄壳 lvjiang-cli 专用 PyInstaller 启动入口（仅依赖 Python 标准库）。

严禁 import lvjiang 包根（避免把 PyQt6 / OCR / 业务代码拉进 cli Analysis）。
"""
from __future__ import annotations

import sys

import cli_client

if __name__ == "__main__":
    raise SystemExit(cli_client.main(sys.argv[1:]))
