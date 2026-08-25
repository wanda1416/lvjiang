"""统计模块的本地路径解析。

统一走这一层而不是散落各处拼路径，且必须动态读取 ``constants.CONFIG_DIR``
（照抄 ``SessionStore.path`` 的写法，见 core/config/session.py:79-87）
——延迟 import + 每次访问重新取值，测试才能 monkeypatch。

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
    from lvjiang import constants
    return constants.CONFIG_DIR / "local" / "telemetry"


def identity_path() -> Path:
    return telemetry_dir() / "identity.json"


def spool_dir() -> Path:
    return telemetry_dir() / "spool"
