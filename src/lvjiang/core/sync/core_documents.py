"""core 固有的账户级同步文档登记（core/sync 包导入即注册）。

core 自己拥有的两种会话文档，与 ``notes``/``avatar`` 同级，不走插件的
``AppHooks.sync_document_modules``：

- ``workflow``：全局任务参数，``workflow.json``，一级 key 只有
  ``workflow_params``（``{wf_id: 参数}``），由 core/config/wf_configs.py 读写；
- ``batch``：批量配置组定义，``batch.json``（lvjiang.batch v2，只含定义，
  不含运行草稿），由 core/batch/config.py 读写。

两者冲突输方都不留副本（``conflict_copy=False``）：参数与配置可丢、
按云端最新版直接覆盖。
"""
from __future__ import annotations

from .registry import GLOBAL_LABEL, register_session_document

register_session_document("workflow", "workflow.json",
                          conflict_copy=False, core=True)
register_session_document("batch", "batch.json", conflict_copy=False, core=True)

__all__ = ["GLOBAL_LABEL"]
