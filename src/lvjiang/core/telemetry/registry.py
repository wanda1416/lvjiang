"""事件 schema 注册表。

插件通过 ``AppHooks.telemetry_modules``（与既有 ``builtin_modules`` 同款
「import 即注册」语义）在自己的模块里调用 :func:`register_schema`。core
层只提供注册/查询能力，不预置任何燕云领域词汇。
"""
from __future__ import annotations

from loguru import logger

from .schema import EventSchema

_REGISTRY: dict[str, EventSchema] = {}


def register_schema(schema: EventSchema) -> None:
    """注册一个事件 schema；同名重复注册视为配置错误，直接抛错。"""
    if schema.name in _REGISTRY and _REGISTRY[schema.name] is not schema:
        raise ValueError(f"事件 schema 重复注册: {schema.name!r}")
    _REGISTRY[schema.name] = schema
    logger.debug(f"[telemetry] 注册事件 schema: {schema.name} v{schema.version}")


def get_schema(name: str) -> EventSchema:
    schema = _REGISTRY.get(name)
    if schema is None:
        raise KeyError(f"未注册的事件 schema: {name!r}")
    return schema


def all_schemas() -> tuple[EventSchema, ...]:
    return tuple(_REGISTRY.values())


def reset_registry() -> None:
    """测试隔离用：清空注册表。"""
    _REGISTRY.clear()
