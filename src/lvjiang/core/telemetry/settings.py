"""三条网络行为开关与本地统计数据清理的读写入口。

``save_settings()``（core/config/session.py）只在 ``settings`` 节点做
**一级浅合并**——直接传 ``{"network": {...}}`` 会整个替换掉
``settings.network`` 子节点，所以这里每次都先取完整的当前配置再改动
需要的字段，整份传回去，不能只传增量。
"""
from __future__ import annotations

from dataclasses import asdict

from loguru import logger


def _current_network_dict() -> dict:
    from ..config import load_user_config
    return asdict(load_user_config().network)


def _save_network(network: dict) -> None:
    from ..config import save_settings
    save_settings({"network": network})


def set_network_feature(feature: str, value: bool) -> None:
    """设置「离线模式/公告/更新」三个非统计类开关（不涉及本地数据清理）。"""
    network = _current_network_dict()
    network[feature] = bool(value)
    _save_network(network)


def set_telemetry_enabled(value: bool) -> None:
    """开关统计功能：关闭时同步清除本地缓冲与标识，开启时补生成标识。

    必须同步执行——用户点关闭的瞬间，本地就不该再有可上报的数据。
    """
    from . import identity as identity_mod
    from . import spool

    network = _current_network_dict()
    network["telemetry"] = bool(value)
    _save_network(network)

    if value:
        identity_mod.get_identity()
    else:
        dropped = spool.pending_event_count()
        identity_mod.purge_identity()
        if dropped:
            logger.info(f"[telemetry] 已删除本地 {dropped} 条未上报数据")
