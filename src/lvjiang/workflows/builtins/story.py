"""内置函数 - 自动剧情（通用工具）

剧情跳过的工作流只做弱调度（推进对话、弹窗提示），所有「该选哪一项」的
判断都交给这里查文案库，避免把大量文案硬编码进 .wf。

| 函数 | 用途 |
|------|------|
| ``story_options(scene, area)`` | 取当前对话的全部选项文本 |
| ``story_pick(scene, area)`` | 查全部配置组并返回应点击选项的坐标 |
| ``story_learn(...)`` | 把一次「选项 + 正确答案」写回指定配置组 |

未收录/匹配不可靠时 ``story_pick`` 返回 ``""``，调用方据此 ``pause`` 让人工
处理，而不是猜一个可能不可逆的选项。
"""

from __future__ import annotations

import uuid

from loguru import logger

from lvjiang.core.config.resolver import get_resolver
from lvjiang.core.layout_models import FoundRegion
from lvjiang.core.story import (
    StoryEntry,
    load_all,
    match,
    similarity,
    upsert_entry,
)
from lvjiang.workflows.builtins._registry import builtin_func


def _blocks(_wf, scene: str, area: str) -> list[dict]:
    """取区域内所有文字块；OCR 能力缺失时返回空列表而非抛错。"""
    try:
        return _wf.ocr_region_blocks(scene, area)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"story: 读取区域 [{scene}].[{area}] 失败: {exc}")
        return []


@builtin_func("story_options")
def story_options(
    _wf,
    scene: str = "general_chat",
    area: str = "dialogue_options",
) -> list[str]:
    """返回当前对话的全部选项文本（按画面从上到下）。

    .wf 用法:
        eval $opts = story_options("general_chat", "dialogue_options")
        if $opts
            ...
        end
    """
    return [block["text"] for block in _blocks(_wf, scene, area) if block.get("text")]


@builtin_func("story_pick")
def story_pick(
    _wf,
    scene: str = "general_chat",
    area: str = "dialogue_options",
) -> FoundRegion | str:
    """查全部配置组，返回当前画面中正确答案选项的坐标（``FoundRegion``）。

    未收录或匹配分数低于阈值时返回 ``""``——调用方应 ``pause`` 让人工选择，
    避免误点不可逆的剧情选项。

    .wf 用法:
        $pick = story_pick("general_chat", "dialogue_options")
        if $pick
            click $pick
        else
            pause("遇到未收录选项，请手动选择后在「工具 → 自动剧情」补充")
        end
    """
    blocks = _blocks(_wf, scene, area)
    options = [block["text"] for block in blocks if block.get("text")]
    if not options:
        logger.debug("story_pick: 当前画面没有识别到选项")
        return ""

    doc = load_all()
    result = match(doc, options)
    if result is None:
        logger.info(f"story_pick: 未匹配文案库，选项={options!r}")
        return ""

    block = next((item for item in blocks if item["text"] == result.answer_text), None)
    if block is None:
        block = max(
            blocks, key=lambda item: similarity(item["text"], result.answer_text)
        )

    logger.info(
        f"story_pick: 命中 entry={result.entry.id!r} "
        f"score={result.score:.2f} answer={block['text']!r}"
    )
    return FoundRegion(
        x_ratio=block["x_ratio"],
        y_ratio=block["y_ratio"],
        w_ratio=block["w_ratio"],
        h_ratio=block["h_ratio"],
        text=block["text"],
    )


@builtin_func("story_learn")
def story_learn(
    options: list,
    answer: str,
    question: str = "",
    scene: str = "",
    group: str = "未分类",
    layer: str = "local",
) -> bool:
    """把一次「选项集合 + 正确答案」写回指定配置组。

    普通用户只能写 ``local``；``layer="system"`` 仅在开发者模式生效，
    否则自动降级为 local。

    .wf 用法:
        story_learn($opts, $answer, "", "主线-第三章", "主线")
    """
    clean = [str(item).strip() for item in options if str(item).strip()]
    answer = str(answer).strip()
    if not clean or not answer:
        logger.warning("story_learn: 选项或答案为空，跳过")
        return False

    target_layer = layer
    if target_layer == "system" and not get_resolver().is_dev_mode():
        logger.warning("story_learn: 普通用户不能写 system，已降级为 local")
        target_layer = "local"

    entry = StoryEntry(
        id=uuid.uuid4().hex[:12],
        options=tuple(clean),
        answer=answer,
        question=str(question).strip(),
        scene=str(scene).strip(),
    )
    upsert_entry(entry, group=str(group).strip() or "未分类", layer=target_layer)
    logger.info(
        f"story_learn: 新增文案 entry={entry.id!r} group={group!r} layer={target_layer}"
    )
    return True


__all__ = ["story_options", "story_pick", "story_learn"]
