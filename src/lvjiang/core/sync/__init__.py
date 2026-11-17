"""数据同步包：文档通道引擎（账本通道属后续阶段）。"""
from . import core_documents  # noqa: F401  # import 即注册 core 固有会话文档
from .engine import SyncReport, iter_local_documents, sync_documents

__all__ = ["SyncReport", "iter_local_documents", "sync_documents"]
