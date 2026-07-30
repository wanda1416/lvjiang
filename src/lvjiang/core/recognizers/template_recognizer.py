"""模板匹配识别器 - 基于参考图库的通用模板匹配

使用 ReferenceMatcher 实现，基于 ORB 特征匹配。
"""
from __future__ import annotations

from typing import Any

import numpy as np

from ._registry import register_recognizer
from .reference_matcher import ReferenceMatcher, MatchResult


@register_recognizer
class TemplateRecognizer:
    """模板匹配识别器。

    名字：``template``
    
    基于 ReferenceMatcher 实现，需要预先配置参考图库。
    """

    name = "template"

    def __init__(
        self,
        matcher: ReferenceMatcher | None = None,
        threshold: float = 0.05,
    ) -> None:
        self._matcher = matcher
        self._threshold = threshold

    def recognize(self, image: np.ndarray, **kwargs: Any) -> MatchResult | dict:
        """识别图像
        
        Args:
            image: 查询图像（BGR numpy 数组）
            **kwargs: 可选参数
                - group: 限定匹配分组
        
        Returns:
            MatchResult 或 dict（如果没有配置 matcher）
        """
        if self._matcher is None:
            raise NotImplementedError(
                "TemplateRecognizer 需要配置 ReferenceMatcher。"
                "请通过构造函数传入 matcher 参数。"
            )
        
        group = kwargs.get("group")
        return self._matcher.match(image, group=group)
