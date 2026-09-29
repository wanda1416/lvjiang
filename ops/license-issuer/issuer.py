#!/usr/bin/env python3
"""激活码签发器入口

用仓库根目录的虚拟环境直接跑，不需要单独建环境：

    .venv\\Scripts\\python.exe ops\\license-issuer\\issuer.py     # Windows
    .venv/bin/python ops/license-issuer/issuer.py                 # macOS / Linux

依赖（PyQt6、cryptography）主项目本来就有；签发用的正文格式直接复用
src/lvjiang/core/license，不另维护一份。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from license_issuer.app import run  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run())
