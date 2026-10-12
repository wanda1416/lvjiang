"""统计模块的本地路径解析。

统一使用公共目录入口，延迟读取本地配置目录。

安全边界（不是实现细节，是设计约束）：这里落盘的 install_id/缓冲事件
必须放在 ``config/local/`` 下，绝不能放进 ``config/session/``。用户指南
明确要求用户打包 ``config/session/`` 发给作者排查问题（见
docs/60-userguide/08-feedback-and-issues.md），install_id 落在那里会让
作者能把「某个 issue 提交者」和「服务端的一条匿名记录」对上，匿名性
当场失效。
"""
from __future__ import annotations

from pathlib import Path


def telemetry_dir() -> Path:
    from ..paths import get_config_local_dir
    return get_config_local_dir() / "telemetry"


def identity_path() -> Path:
    return telemetry_dir() / "identity.json"


def spool_dir() -> Path:
    return telemetry_dir() / "spool"
