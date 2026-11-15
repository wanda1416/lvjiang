"""本机作者标记：账本行 ``instance_id`` 的来源。

同一台设备的标识只取决于本机身份文件，与业务无关，但被账本写入、快照上行和
失败归因三处共用——分散成三份缓存会让它们在不同时刻取到不同的值。
"""

from __future__ import annotations

_author_id_cache: str | None = None


def author_stamp() -> str:
    """本机作者标记（模块级缓存：Android 路径涉及文件读写）。"""
    global _author_id_cache
    if _author_id_cache is None:
        from ..account.identity import device_author_id

        _author_id_cache = device_author_id()
    return _author_id_cache
