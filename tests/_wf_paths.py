"""按脚本 id 解析随包工作流的真实路径。

任务以 ``#% id`` 被引用，**目录只是组织方式**。测试里写死
``config/system/workflows/xxx.wf`` 会让每次目录调整都变成一批机械改动，
所以统一走这里：给 id、文件名或相对路径都行，解析规则只有一份
（``lvjiang.workflows.discovery.resolve_workflow_reference``）。
"""

from __future__ import annotations

from pathlib import Path

from lvjiang.workflows.discovery import resolve_workflow_reference


def wf_path(reference: str) -> Path:
    """解析随包工作流路径；找不到直接抛 ``FileNotFoundError``。

    找不到就该红：调用方要么写错了 id，要么脚本被删了——两种情况都不该
    被 skip 或空值掩盖。
    """
    path = resolve_workflow_reference(reference)
    if path is None:
        raise FileNotFoundError(f"找不到工作流: {reference}")
    return path
