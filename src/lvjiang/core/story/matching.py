"""剧情选项匹配：OCR 文本归一化 + 相似度查表。

只做纯文本运算，不依赖 OCR / 布局，便于单测。匹配分两步：

1. 按「选项集合」找最像的 entry（双向平均相似度，避免 OCR 只认出一项时
   反而拿到高分）；
2. 在该 entry 的答案与当前画面实际选项之间再取最像的一项，返回**画面上
   真实存在的文本**——调用方拿它去 ``find ... by contains`` 才能点中。
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Sequence

from .models import StoryDoc, StoryEntry

_PUNCTUATION = set(
    "，。！？、；：\"'“”‘’（）【】《》…—·,.!?;:\"'()[]<>~`|\\/+-=*&^%$#@"
)


def normalize(text: str) -> str:
    """去空白与标点、全角转半角、统一小写。"""
    result: list[str] = []
    for char in text:
        if char.isspace() or char in _PUNCTUATION:
            continue
        code = ord(char)
        if 0xFF01 <= code <= 0xFF5E:  # 全角 ASCII → 半角
            char = chr(code - 0xFEE0)
        result.append(char.lower())
    return "".join(result)


def similarity(left: str, right: str) -> float:
    """两个文本的相似度（0~1），基于归一化后的编辑相似性。"""
    a, b = normalize(left), normalize(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _set_similarity(left: Sequence[str], right: Sequence[str]) -> float:
    """两个选项集合的相似度：双向平均，缺项会被惩罚。"""
    if not left or not right:
        return 0.0

    def one_way(source: Sequence[str], target: Sequence[str]) -> float:
        return sum(
            max(similarity(item, other) for other in target) for item in source
        ) / len(source)

    return (one_way(left, right) + one_way(right, left)) / 2


def pick_answer(
    entry: StoryEntry, current_options: Sequence[str], threshold: float
) -> str | None:
    """在画面实际选项里找与 entry.answer 最像的一项。"""
    best: str | None = None
    best_score = 0.0
    for option in current_options:
        score = similarity(entry.answer, option)
        if score > best_score:
            best_score = score
            best = option
    return best if best is not None and best_score >= threshold else None


@dataclass(frozen=True)
class MatchResult:
    entry: StoryEntry
    #: 当前画面上与正确答案对应的实际文本，可直接用于 find 定位。
    answer_text: str
    score: float


def match(doc: StoryDoc, current_options: Sequence[str]) -> MatchResult | None:
    """按当前画面选项匹配文案库；无可靠命中返回 None。"""
    options = [item for item in current_options if item.strip()]
    if not options or not doc.entries:
        return None
    threshold = doc.settings.match_threshold

    best_entry: StoryEntry | None = None
    best_score = 0.0
    for entry in doc.entries:
        score = _set_similarity(entry.options, options)
        if score > best_score:
            best_score = score
            best_entry = entry
    if best_entry is None or best_score < threshold:
        return None

    answer = pick_answer(best_entry, options, threshold)
    if answer is None:
        return None
    return MatchResult(entry=best_entry, answer_text=answer, score=best_score)
