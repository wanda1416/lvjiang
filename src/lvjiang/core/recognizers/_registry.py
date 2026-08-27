"""识别器注册表。

提供 ``Recognizer`` 协议以及注册 / 查询接口。可选识别能力由插件通过
``AppHooks.recognizer_classes`` 声明，引擎加载插件时调用
``register_recognizer`` 注入。参考图匹配是引擎自身服务，不经过此注册表。

识别器使用场景：
- DSL ``recognize`` 指令通过 ``get_recognizer(name)`` 获取识别器实例
- 通用识别测试对话框通过 ``list_recognizers()`` 列出可用识别器
"""
from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

import numpy as np

logger = logging.getLogger(__name__)


@runtime_checkable
class Recognizer(Protocol):
    """识别器协议。

    所有识别器必须实现 ``name`` 属性与 ``recognize`` 方法。
    """

    name: str

    def recognize(
        self,
        image: np.ndarray,
        **kwargs: Any,
    ) -> Any:
        """对输入图像执行识别，返回该识别器约定的结果。"""
        ...


# ── 注册表 ─────────────────────────────────────────────────────────────
_RECOGNIZERS: dict[str, type] = {}


def register_recognizer(cls: type) -> type:
    """注册一个识别器类（装饰器或直接调用）。"""
    name = getattr(cls, "name", None)
    if not name:
        raise ValueError(f"识别器类 {cls} 缺少 name 属性")
    if name in _RECOGNIZERS:
        logger.warning("识别器 %s 重复注册，旧实现将被覆盖", name)
    _RECOGNIZERS[name] = cls
    logger.info("[recognizer] 注册: %s -> %s", name, cls.__name__)
    return cls


def get_recognizer(name: str, **init_kwargs: Any) -> Recognizer:
    """按名字获取识别器实例。"""
    cls = _RECOGNIZERS.get(name)
    if cls is None:
        raise KeyError(
            f"未注册的识别器: {name!r}。可用: {list(_RECOGNIZERS)}"
        )
    return cls(**init_kwargs)


def list_recognizers() -> list[str]:
    """返回所有已注册识别器名字。"""
    return list(_RECOGNIZERS.keys())


def clear_recognizers() -> None:
    """清空注册表（测试用）。"""
    _RECOGNIZERS.clear()
