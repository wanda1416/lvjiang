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
class ListSpec:
    """列表字段：每个元素是一个由 ``item_fields`` 声明的对象。

    嵌套一层**不等于**放宽白名单：元素里的 str 字段同样受
    :class:`FieldSpec` 那条「必须有 choices/pattern」的硬约束（由 FieldSpec
    自己的 ``__post_init__`` 保证），否则整条防线会从嵌套结构这里漏掉。

    ``max_items`` 是必须的：列表长度直接决定 payload 体积，不设上限时
    一次异常长的会话就能把单条事件撑到几百 KB，撑爆传输层的体积闸门。
    """

    name: str
    item_fields: tuple[FieldSpec, ...]
    required: bool = True
    max_items: int = 64

    def __post_init__(self) -> None:
        if not self.item_fields:
            raise TelemetrySchemaError(f"列表字段 {self.name!r} 未声明 item_fields")
        if self.max_items <= 0:
            raise TelemetrySchemaError(f"列表字段 {self.name!r} 的 max_items 必须为正")

    def validate_value(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, (list, tuple)):
            raise TelemetrySchemaError(f"字段 {self.name!r} 应为 list")
        if len(value) > self.max_items:
            raise TelemetrySchemaError(
                f"字段 {self.name!r} 元素数 {len(value)} 超出上限 {self.max_items}")
        known = frozenset(f.name for f in self.item_fields)
        out: list[dict[str, Any]] = []
        for i, item in enumerate(value):
            if not isinstance(item, Mapping):
                raise TelemetrySchemaError(f"字段 {self.name!r}[{i}] 应为对象")
            extra = set(item) - known
            if extra:
                raise TelemetrySchemaError(
                    f"字段 {self.name!r}[{i}] 含未声明的键: {sorted(extra)}")
            row: dict[str, Any] = {}
            for spec in self.item_fields:
                present = spec.name in item and item[spec.name] is not None
                if not present:
                    if spec.required:
                        raise TelemetrySchemaError(
                            f"字段 {self.name!r}[{i}] 缺少必填键 {spec.name!r}")
                    continue
                row[spec.name] = spec.validate_value(item[spec.name])
            out.append(row)
        return out


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
    fields: tuple[FieldSpec | ListSpec, ...] = field(default_factory=tuple)

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
            if isinstance(spec, ListSpec):
                # 给一个元素而不是空列表：同意弹窗拿这份样例告诉用户「实际会
                # 发送的数据长这样」，空列表等于把嵌套字段藏起来不给用户看。
                out[spec.name] = [
                    {f.name: self._example_field(f) for f in spec.item_fields}
                ]
            else:
                out[spec.name] = self._example_field(spec)
        return out

    @staticmethod
    def _example_field(spec: FieldSpec) -> Any:
        if spec.example is not None:
            return spec.example
        if spec.kind is bool:
            return False
        if spec.kind is int:
            return int(spec.minimum if spec.minimum is not None else 0)
        if spec.kind is float:
            return float(spec.minimum if spec.minimum is not None else 0.0)
        allowed = spec._allowed_choices()
        return allowed[0] if allowed else ""
