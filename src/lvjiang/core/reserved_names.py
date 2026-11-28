"""保留名：被固定目录或命名空间占着、不允许用户拿来当 key 的名字。

只有一个真实来源，布局 key 与环境 key 都在这里查，避免两处各写一份名单。

为什么需要这条规则：``config/session/screenshots/`` 下按布局 key 建子目录，
而独立任务包的标定底图固定落在 ``screenshots/packages/<任务包 id>/`` 里。布局
key 的字符集恰好允许 ``packages`` 这种纯小写单词，于是一个叫 ``packages`` 的
用户布局会和所有任务包的截图共用一个目录树——两边互相覆盖、互相读到对方的
底图，而报错只会以"截图不见了"这种形式出现。
"""
from __future__ import annotations

#: 与 ``screenshots/`` 下固定子目录重名、禁止用户用作 key 的名字。
RESERVED_NAMES: frozenset[str] = frozenset({"packages"})


def ensure_name_not_reserved(value: str, *, kind: str) -> str:
    """校验名字未被保留；被保留时抛 ``ValueError``。

    Args:
        value: 待校验的 key（大小写不敏感：``Packages`` 一样会撞目录）。
        kind: 给用户看的名字种类，如 "布局" / "环境"。

    Returns:
        去空白后的 key 原文（不做大小写转换，调用方各自决定是否规范化）。
    """
    key = str(value).strip()
    if key.casefold() in RESERVED_NAMES:
        raise ValueError(
            f"{kind} key「{key}」是保留名：screenshots/packages/ 已被独立任务包的"
            f"截图目录占用，请换一个名字"
        )
    return key
