"""上报事件的字段白名单机制。

核心不变量：能被发出去的 dict 只可能由 ``EventSchema.validate()`` 生产，
不给任何人手搓 dict 直接扔进传输层的机会。

设计动机：这是本模块唯一的 PII 防线。上报字段来自 OCR、用户配置、系统
接口等多个来源，任何一处手滑都可能把不该发的内容塞进 payload。把「什么
能发」收拢成一份声明式白名单，未声明的键、超范围的值一律在校验时抛错，
而不是静默丢弃或静默放行——静默处理会让手滑的人以为改动生效了，抛错才会
在测试与联调时立刻暴露。

``ValidatedEvent`` 是唯一允许进入缓冲与传输层的类型，两处入口都做
``isinstance`` 检查（见 :mod:`lvjiang.core.telemetry.spool` /
:mod:`lvjiang.core.telemetry.transport`）。
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


class TelemetrySchemaError(ValueError):
    """payload 不满足声明的字段白名单。"""


@dataclass(frozen=True)
class FieldSpec:
    """单个字段的声明式约束。

    ``str`` 类型字段必须提供 ``choices`` 或 ``pattern`` 之一（或
    ``choices_fn`` 动态枚举），否则视为「自由文本」，由
    :meth:`EventSchema.__post_init__` 拒绝注册——这条硬约束就是防止
    OCR 原文/用户输入直接出站的结构性保障。
    """

    name: str
    kind: type
    required: bool = True
    choices: tuple[str, ...] = ()
    choices_fn: Callable[[], Sequence[str]] | None = None
    pattern: str = ""
    max_length: int = 64
    minimum: float | None = None
    maximum: float | None = None
    #: pattern 校验的字段没有可枚举的候选值，example() 生成不出满足
    #: 正则的样例——需要时显式提供一个，否则「查看示例数据」会拿一份
    #: 自相矛盾（validate() 会拒绝）的样例给用户看。
    example: object | None = None

    def __post_init__(self) -> None:
        if self.kind is str and not (self.choices or self.choices_fn or self.pattern):
            raise TelemetrySchemaError(
                f"字段 {self.name!r} 是 str 类型但未声明 choices/choices_fn/pattern，"
                "禁止注册自由文本字段")
        if self.pattern:
            re.compile(self.pattern)  # 提前校验正则本身合法

    def _allowed_choices(self) -> tuple[str, ...]:
        if self.choices_fn is not None:
            return tuple(str(c) for c in self.choices_fn())
        return self.choices

    def validate_value(self, value: Any) -> Any:
        if self.kind is bool:
            if not isinstance(value, bool):
                raise TelemetrySchemaError(f"字段 {self.name!r} 应为 bool")
            return value
        if self.kind is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise TelemetrySchemaError(f"字段 {self.name!r} 应为 int")
            if self.minimum is not None and value < self.minimum:
                raise TelemetrySchemaError(f"字段 {self.name!r} 低于下限 {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise TelemetrySchemaError(f"字段 {self.name!r} 超出上限 {self.maximum}")
            return value
        if self.kind is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TelemetrySchemaError(f"字段 {self.name!r} 应为 float")
            value = float(value)
            if self.minimum is not None and value < self.minimum:
                raise TelemetrySchemaError(f"字段 {self.name!r} 低于下限 {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise TelemetrySchemaError(f"字段 {self.name!r} 超出上限 {self.maximum}")
            return value
        if self.kind is str:
            if not isinstance(value, str):
                raise TelemetrySchemaError(f"字段 {self.name!r} 应为 str")
            if len(value) > self.max_length:
                raise TelemetrySchemaError(
                    f"字段 {self.name!r} 超出最大长度 {self.max_length}")
            allowed = self._allowed_choices()
            if allowed and value not in allowed:
                raise TelemetrySchemaError(
                    f"字段 {self.name!r} 取值 {value!r} 不在允许范围内")
            if self.pattern and not re.fullmatch(self.pattern, value):
                raise TelemetrySchemaError(
                    f"字段 {self.name!r} 取值 {value!r} 不匹配 {self.pattern!r}")
            return value
        raise TelemetrySchemaError(f"字段 {self.name!r} 声明了不支持的类型 {self.kind!r}")


@dataclass(frozen=True)
class ValidatedEvent:
    """只能由 :meth:`EventSchema.validate` 构造。缓冲与传输层只接受这个类型。"""

    schema_name: str
    schema_version: int
    values: Mapping[str, Any]


@dataclass(frozen=True)
class EventSchema:
    """一类上报事件的完整字段声明。"""

    name: str
    version: int
    fields: tuple[FieldSpec, ...] = field(default_factory=tuple)

    def field_names(self) -> frozenset[str]:
        return frozenset(f.name for f in self.fields)

    def validate(self, values: Mapping[str, Any]) -> ValidatedEvent:
        """校验并重建一份新 dict；任何一条不满足即抛
        :class:`TelemetrySchemaError`，绝不静默丢弃或静默放行。
        """
        known = self.field_names()
        extra = set(values) - known
        if extra:
            raise TelemetrySchemaError(
                f"schema {self.name!r} 未声明的字段: {sorted(extra)}")

        result: dict[str, Any] = {}
        for spec in self.fields:
            present = spec.name in values and values[spec.name] is not None
            if not present:
                if spec.required:
                    raise TelemetrySchemaError(
                        f"schema {self.name!r} 缺少必填字段 {spec.name!r}")
                continue
            result[spec.name] = spec.validate_value(values[spec.name])
        return ValidatedEvent(
            schema_name=self.name, schema_version=self.version,
            values=MappingProxyType(result))

    def example(self) -> dict[str, Any]:
        """生成一份满足本 schema 的示例 payload，供同意弹窗/设置页
        「查看示例数据」使用——保证展示的就是真会发的字段集合。

        产出必须能自己通过 :meth:`validate`（不能给出一份自相矛盾的样例），
        这也是本方法存在的测试价值：schema 定义有误时这里会先炸。
        """
        out: dict[str, Any] = {}
        for spec in self.fields:
            if spec.example is not None:
                out[spec.name] = spec.example
            elif spec.kind is bool:
                out[spec.name] = False
            elif spec.kind is int:
                out[spec.name] = int(spec.minimum if spec.minimum is not None else 0)
            elif spec.kind is float:
                out[spec.name] = float(spec.minimum if spec.minimum is not None else 0.0)
            elif spec.kind is str:
                allowed = spec._allowed_choices()
                out[spec.name] = allowed[0] if allowed else ""
        return out
