"""资源清单板：身份声明。条目模型与落盘仍在各块板自己的模块里。

目前只有袅袅之音（``core.linger``）。公共身份放在这里，是为了下一块板复用
同一套 kind、后缀、默认目标和同步策略，而不是先搬空目录。
"""
from __future__ import annotations

from .board import ChecklistBoard

__all__ = ["ChecklistBoard"]
