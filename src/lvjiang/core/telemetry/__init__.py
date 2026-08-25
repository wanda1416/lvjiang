"""匿名使用统计与调律数据回收（客户端）。

分层：本包只提供通用机制（同意/身份/白名单/缓冲/传输），**不认识任何
燕云词汇**——具体事件的字段声明在 ``lvjiang.apps.yysls.telemetry``，经
``AppHooks.telemetry_modules`` 注册进来，同 ``builtin_modules`` 的
「import 即注册」语义。

对外只暴露这几个函数，其余子模块请按需 import：
"""
from __future__ import annotations

from .consent import ConsentState, NetFeature, is_network_feature_enabled, needs_prompt
from .schema import EventSchema, FieldSpec, TelemetrySchemaError, ValidatedEvent

__all__ = [
    "ConsentState",
    "NetFeature",
    "is_network_feature_enabled",
    "needs_prompt",
    "EventSchema",
    "FieldSpec",
    "TelemetrySchemaError",
    "ValidatedEvent",
]
