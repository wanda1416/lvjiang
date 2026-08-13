"""内置函数 - 背包窗口级去重游标。

页面导航、网格遍历、OCR 与拖拽由 DSL 工作流负责；本模块只保存
已见行首锚点指纹和窗口状态，并在每个完整窗口结束后判断继续滚动或到底。
"""

from loguru import logger

from lvjiang.workflows.builtins._registry import builtin_func

_MAX_IDLE_ROUNDS = 2
_MAX_SCROLL_ROUNDS = 200


@builtin_func("bag_cursor_init")
def _bag_cursor_init(_engine, *args) -> str:
    """初始化一个部位的背包扫描游标。"""
    _engine.context["_bag_cursor"] = {
        "seen": set(),       # 已见物理行的首列指纹
        "window": [],        # 当前窗口各物理行的首列指纹
        "new_count": 0,      # 当前窗口新增物理行数
        "idle": 0,
        "rounds": 0,
    }
    logger.info("bag_cursor_init: 窗口级游标已初始化")
    return ""


@builtin_func("bag_cursor_visit")
def _bag_cursor_visit(_engine, fingerprint: str | None, *args) -> str:
    """登记当前物理行的首列锚点，返回 ``new`` / ``skip`` / ``end``。

    ``end`` 表示读到空格；背包装备按行优先连续排列，因此当前部位
    后续格位也为空。``skip`` 表示整行已经完整扫描过，WF 不应再读
    该行第 2～6 列。指纹按业务约定可视为装备唯一标识。
    """
    cursor = _engine.context.get("_bag_cursor")
    if cursor is None:
        logger.warning("bag_cursor_visit: 未初始化，请先调用 bag_cursor_init()")
        return "end"
    if not fingerprint:
        logger.info("bag_cursor_visit: 空指纹 → end")
        return "end"

    cursor["window"].append(fingerprint)
    if fingerprint in cursor["seen"]:
        logger.debug(f"bag_cursor_visit: 行锚点 fp={fingerprint} 已见过 → skip整行")
        return "skip"

    cursor["seen"].add(fingerprint)
    cursor["new_count"] += 1
    logger.debug(
        f"bag_cursor_visit: 行锚点 fp={fingerprint} → new "
        f"(window_new_rows={cursor['new_count']}, total_rows={len(cursor['seen'])})"
    )
    return "new"


@builtin_func("bag_cursor_finish_window")
def _bag_cursor_finish_window(
    _engine, visible_rows: int, expected_rows: int = 3, *args
) -> str:
    """提交一个完整窗口，返回 ``scroll`` 或 ``end``。

    到底规则与自动调律 dedup 策略一致：非满窗且零新增立即结束；
    满窗连续两轮零新增结束；总滚动轮数另有保险丝。
    """
    cursor = _engine.context.get("_bag_cursor")
    if cursor is None:
        logger.warning(
            "bag_cursor_finish_window: 未初始化，请先调用 bag_cursor_init()"
        )
        return "end"

    visible_rows = int(visible_rows or 0)
    expected_rows = int(expected_rows or 0)
    new_count = cursor["new_count"]
    if not cursor["window"]:
        logger.info("bag_cursor_finish_window: 空窗口 → end")
        return "end"

    if new_count == 0:
        if visible_rows < expected_rows:
            logger.info(
                "bag_cursor_finish_window: "
                f"非满窗({visible_rows}/{expected_rows})且零新增 → end"
            )
            return "end"
        cursor["idle"] += 1
        if cursor["idle"] >= _MAX_IDLE_ROUNDS:
            logger.info(
                "bag_cursor_finish_window: "
                f"连续 {cursor['idle']} 轮零新增 → end"
            )
            return "end"
    else:
        cursor["idle"] = 0

    cursor["rounds"] += 1
    if cursor["rounds"] > _MAX_SCROLL_ROUNDS:
        logger.error(
            f"bag_cursor_finish_window: 滚动超过 {_MAX_SCROLL_ROUNDS} 轮 → end"
        )
        return "end"

    cursor["window"] = []
    cursor["new_count"] = 0
    logger.info(
        "bag_cursor_finish_window: scroll "
        f"(idle={cursor['idle']}, total_rows={len(cursor['seen'])})"
    )
    return "scroll"
