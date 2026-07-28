"""内置函数 - 背包遍历与滚动校验"""

from loguru import logger

from src.workflows.builtins._registry import builtin_func


@builtin_func("check_scroll")
def _check_scroll(_engine, fingerprint: str, *args) -> str:
    """滚动校验：比对 grid[1][1] 指纹与滚动管理器预期

    返回偏移量字符串：
        "0"  — 正常（指纹在已知序列中，偏移量符合预期）
        "1"  — 没有滚动（指纹仍在行 1 位置）
        "-1" — 滚动过头（指纹在行 3 位置）

    .wf 用法:
        eval $offset = check_scroll($fp)
    """
    manager = _engine.context.get("_scroll_manager", {})
    row_fps = manager.get("row_fps", [])
    fingerprints = manager.get("fingerprints", {})

    if not row_fps:
        logger.debug("check_scroll: 无快照数据，视为正常")
        return "0"

    # 全不匹配 = 全部被回收或新内容，视为正常
    if fingerprint not in fingerprints:
        logger.debug(f"check_scroll: 指纹 {fingerprint} 不在已知集合中，视为正常")
        return "0"

    # 找到指纹在原序列中的位置
    for i, fp in enumerate(row_fps):
        if fp == fingerprint:
            # 滚动 1 步后，行 1 应该是原行 2（i=1）
            # offset = i - 1:  0=正常, -1=过头, +1=没滚
            offset = i - 1
            logger.debug(f"check_scroll: 指纹 {fingerprint} 在 row_fps[{i}]，偏移={offset}")
            return str(offset)

    return "0"


@builtin_func("notify_scroll")
def _notify_scroll(_engine, col, row, fingerprint: str, *args) -> str:
    """记录已处理装备的指纹到滚动管理器

    每行第一列（col=1）的指纹记录为行指纹，用于后续滚动校验。

    .wf 用法:
        eval notify_scroll($col, $row, $fp)
    """
    manager = _engine.context.setdefault("_scroll_manager", {
        "row_fps": [],
        "fingerprints": {},
        "scroll_count": 0,
    })
    manager["fingerprints"][fingerprint] = True
    # 每行第一列（col=1）记录为行指纹
    if str(col) == "1":
        manager["row_fps"].append(fingerprint)
        logger.debug(f"notify_scroll: 记录行指纹 row={row} fp={fingerprint}")
    return ""


@builtin_func("scroll_advance")
def _scroll_advance(_engine, *args) -> str:
    """滚动校验通过后，推进状态：移除已滚出的行指纹

    .wf 用法:
        eval scroll_advance()
    """
    manager = _engine.context.get("_scroll_manager", {})
    row_fps = manager.get("row_fps", [])
    if row_fps:
        removed = row_fps.pop(0)
        logger.debug(f"scroll_advance: 移除已滚出指纹 {removed}，剩余 {len(row_fps)} 行")
    manager["scroll_count"] = manager.get("scroll_count", 0) + 1
    return ""
