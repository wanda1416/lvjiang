"""yysls 领域文档声明——经 ``AppHooks.sync_document_modules`` import 时注册。

core.sync 不认识插件领域词汇；出装（备战方案 + 装备池）是 yysls 的领域
文件，kind 与文件后缀由本模块声明，声明后账号服务文档通道按整文档覆盖
同步（见 core/sync/engine.py）。
"""
from __future__ import annotations

from ...core.sync.registry import register_user_document_kind

register_user_document_kind("loadouts", ".loadouts.json")
