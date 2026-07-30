"""Chaquopy 冒烟测试模块 — 验证 Python 运行时可用。

Phase 2 起此目录将挂接仓库 src/ 核心逻辑。
"""

import platform
import sys


def smoke_test() -> str:
    """返回 Python 版本与平台信息，供 MainActivity 自检展示"""
    return (
        f"Python {sys.version.split()[0]} "
        f"({platform.machine()}) OK"
    )
