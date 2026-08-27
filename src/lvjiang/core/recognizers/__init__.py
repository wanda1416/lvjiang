"""通用识别器插件包。

引擎启动时自动注册内置识别器（OCR / 模板 / 颜色）。业务识别器由插件通过
``AppHooks.recognizer_classes`` 注入。

对外暴露：

- ``Recognizer`` 协议
- ``register_recognizer`` / ``get_recognizer`` / ``list_recognizers``
- 内置识别器类：``OCRRecognizer`` / ``TemplateRecognizer`` / ``ColorRecognizer``
- 参考图匹配器：``ReferenceMatcher`` / ``MatchResult``
"""
from __future__ import annotations

from ._registry import (
    Recognizer,
    clear_recognizers,
    get_recognizer,
    list_recognizers,
    register_recognizer,
)
from .color_recognizer import ColorRecognizer
from .ocr_recognizer import OCRRecognizer
from .reference_matcher import MatchResult, ReferenceMatcher
from .reference_recognizer import (
    RICH_RESERVED_KEYS,
    ReferenceInfo,
    ReferenceRecognizer,
)
from .template_recognizer import TemplateRecognizer

__all__ = [
    "Recognizer",
    "register_recognizer",
    "get_recognizer",
    "list_recognizers",
    "clear_recognizers",
    "OCRRecognizer",
    "TemplateRecognizer",
    "ColorRecognizer",
    "ReferenceMatcher",
    "MatchResult",
    "ReferenceInfo",
    "ReferenceRecognizer",
    "RICH_RESERVED_KEYS",
]


def _register_builtins() -> None:
    """确保内置识别器已注册。

    由于各识别器模块在 import 时已通过 ``@register_recognizer`` 装饰器完成注册，
    本函数仅作为显式的初始化入口供测试使用。
    """
    assert OCRRecognizer.name == "ocr"
    assert TemplateRecognizer.name == "template"
    assert ColorRecognizer.name == "color"


_register_builtins()
