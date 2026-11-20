"""把清单的已得数量写进 Profile 账本，以及「同步记账」开关。

三条硬规则：

- **默认不记账。** 勾选只写记录文件；记账是用户在页面上显式打开的开关，且按用户
  分别记。误点一下就把账本改了的代价太大，默认必须是「只记录」。
- **库存目标入账必须 ``trigger_sync=False``。** ``stock:niaoniao`` 挂着八音窍的账
  （``byq_countdown`` 倍率 −1、``byq_jindu`` 负向），走同步目标会让每勾一枚都
  平白扣掉八音窍累计；清单这里只记「又拿到一枚」。
- **先记录、后入账。** 记录写成功才写账本，账本失败就留在「待入账」，下次改动
  该条目时再补——反过来会出现「账本有、记录没有」的对不上账。

入账目标由每个条目自己声明（``target``），不写死在代码里：库存类目标按累加记账，
配额类目标本身按周期重置，条目就是「周期型」。
"""
from __future__ import annotations

from loguru import logger

from .....core.config import get_interface_store
from .....core.profile.schema import get_profile_config
from .....core.profile.service import profile_action, profile_read
from .board import LINGER_BOARD

#: 清单条目的默认入账目标：库存里的袅袅之音
TARGET_KEY = LINGER_BOARD.default_target
#: 开关存在 ``interface.json`` 的 ``ui_state`` 下，按用户名记（本机界面状态）
SWITCH_NODE = "ui_state"
SWITCH_KEY = LINGER_BOARD.switch_key

MODEL_STOCK = "stock"
MODEL_QUOTA = "quota"


def split_target(target: str) -> tuple[str, str]:
    """``stock:niaoniao`` → ``("stock", "niaoniao")``；不带模型名时按库存处理。"""
    model, _, key = str(target or "").partition(":")
    if not key:
        return MODEL_STOCK, model
    return model, key


def target_available(target: str) -> bool:
    """目标 key 是否还在定义里；用户删掉这个 key 时返回 False。"""
    model, key = split_target(target)
    if not key:
        return False
    return get_profile_config().get_key(key, model_type=model) is not None


def is_periodic(target: str) -> bool:
    """配额类目标按周期重置，条目就是「周期型」；库存类是固定来源。"""
    model, _ = split_target(target)
    return model == MODEL_QUOTA


def periodic_state(username: str, target: str) -> tuple[int, int]:
    """周期型条目的（本周期当前值, 上限）；上限缺失时返回 0。

    当前值走 ``profile_read``：配额跨周期自动读作 0，不需要本地再存一份。
    """
    model, key = split_target(target)
    if model != MODEL_QUOTA or not key:
        return 0, 0
    definition = get_profile_config().get_key(key, model_type=MODEL_QUOTA)
    if definition is None:
        return 0, 0
    value = profile_read(username, key)
    current = int(value) if isinstance(value, (int, float)) else 0
    raw_cap = getattr(definition, "cap", None)
    cap = int(raw_cap) if isinstance(raw_cap, (int, float)) else 0
    return current, cap


def _board_or_default(board):
    return LINGER_BOARD if board is None else board


def booking_enabled(username: str, board=None) -> bool:
    """该用户是否打开了「同步记账」（默认关闭）。"""
    board = _board_or_default(board)
    node = get_interface_store().get_node(SWITCH_NODE, {})
    if not isinstance(node, dict):
        return False
    block = node.get(board.switch_key)
    if not isinstance(block, dict):
        return False
    return bool(block.get(username))


def set_booking_enabled(username: str, enabled: bool, board=None) -> None:
    """切换「同步记账」；打开不会补写以前的记录，只有之后的改动才入账。"""
    board = _board_or_default(board)
    store = get_interface_store()

    def mutate(node) -> dict:
        data = dict(node) if isinstance(node, dict) else {}
        block = dict(data.get(board.switch_key) or {})
        if enabled:
            block[username] = True
        else:
            block.pop(username, None)
        if block:
            data[board.switch_key] = block
        else:
            data.pop(board.switch_key, None)
        return data

    store.mutate_node(SWITCH_NODE, mutate)


def write_delta(username: str, target: str, delta: int, source: str,
                board=None) -> str | None:
    """往账本写一条增量；成功返回 ``None``，失败返回可展示的原因。"""
    board = _board_or_default(board)
    if delta == 0:
        return None
    model, key = split_target(target)
    if not key:
        return "no target"
    try:
        # 哪些模型触发同步目标由这块板声明。袅袅只放行配额。
        profile_action(
            username, key, delta=delta, source=source,
            trigger_sync=board.triggers_sync(model),
        )
    except Exception as exc:  # 锁定、CAS 冲突、未定义都归于「这次没入账」
        logger.warning(f"{board.title}入账失败 {username}.{target} Δ{delta:+d}：{exc}")
        return str(exc) or "failed"
    return None
