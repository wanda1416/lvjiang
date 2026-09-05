"""背包滚动遍历策略 —— 供 auto_tuning 按配置切换

两种策略（TRAVERSALS 注册，默认 DEFAULT_TRAVERSAL）：

- DedupTraversal（新，默认，dedup.py）：滚动后逐行读首列，用「上一轮
  窗口」的指纹集合去重：重复行跳过、新行处理。指纹只作去重依据、不做
  位置对齐，OCR 漂移的代价从错位/崩溃降为有界的重复处理（重复判定对
  已处理装备无副作用）。
- PositionalTraversal（旧，positional.py）：位置对齐 + 三向指纹校验 +
  小步补滚/回滚纠偏，自 auto_tuning 原样迁入，逻辑保持不变，供回切。

回切方式：wf_configs["auto_tuning"] 的 scroll_strategy 配
"scroll_strategy": "positional"（或给工作流注入 ctx.scroll_strategy）。
"""
from lvjiang.apps.yysls.workflows.implementations.bag_traversal.base import (
    BagTraversal,
)
from lvjiang.apps.yysls.workflows.implementations.bag_traversal.dedup import (
    DedupTraversal,
)
from lvjiang.apps.yysls.workflows.implementations.bag_traversal.positional import (
    PositionalTraversal,
    ScrollState,
)
from lvjiang.apps.yysls.workflows.implementations.bag_traversal.scrolling import (
    BagScrollStrategy,
    DragBagScroll,
    WheelBagScroll,
    create_bag_scroll_strategy,
)

TRAVERSALS: dict[str, type[BagTraversal]] = {
    "dedup": DedupTraversal,
    "positional": PositionalTraversal,
}
DEFAULT_TRAVERSAL = "dedup"

__all__ = [
    "BagTraversal",
    "DedupTraversal",
    "PositionalTraversal",
    "ScrollState",
    "BagScrollStrategy",
    "DragBagScroll",
    "WheelBagScroll",
    "create_bag_scroll_strategy",
    "TRAVERSALS",
    "DEFAULT_TRAVERSAL",
]
