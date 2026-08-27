"""统计上报的同意状态与三条网络行为的统一开关。

同意模型（最终定论，经历过 opt-in → 默认开启 → 又改回 opt-in 的反复）：
新版本首次启动弹一次性提示询问是否同意收集匿名调律报告；无论选哪个都
不再重复弹，此后只能去设置里改主意。``consent`` 三态记录「弹窗问过没
问过、答了什么」，``settings.network.telemetry`` 记录「当前实际开关」——
两者分开是因为用户可以在设置里把已经拒绝过的开关重新打开，而不需要
弹窗问第二次。

三条网络行为（公告/更新/统计）共用一个总闸 + 各自的分项开关：公告与
更新是「给用户的服务」，统计是「用户给项目的贡献」，绑成一个开关会
逼用户为了关统计而放弃安全公告。
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from loguru import logger

from ..config.session import get_session_store


class ConsentState(str, Enum):
    UNKNOWN = ""
    GRANTED = "granted"
    DENIED = "denied"


class NetFeature(str, Enum):
    ANNOUNCEMENT = "announcement"
    UPDATE = "update"
    TELEMETRY = "telemetry"


def _telemetry_state_node() -> dict:
    server = get_session_store().get_node("server_config", {})
    if not isinstance(server, dict):
        return {}
    node = server.get("telemetry")
    return node if isinstance(node, dict) else {}


def get_consent_state() -> ConsentState:
    raw = _telemetry_state_node().get("consent", "")
    try:
        return ConsentState(raw)
    except ValueError:
        return ConsentState.UNKNOWN


def _is_dev_build() -> bool:
    from ..config.resolver import get_resolver
    return get_resolver().is_dev_mode()


def needs_prompt() -> bool:
    """是否应该在本次启动弹一次性同意提示。

    只有「从未问过」才弹；dev 模式（仓库带 .git 或 LVJIANG_DEV_MODE=1）
    永远不弹，避免自己的调试环境污染统计，也避免开发时反复弹窗。
    """
    if _is_dev_build():
        return False
    return get_consent_state() is ConsentState.UNKNOWN


def record_consent_choice(granted: bool) -> None:
    """记录首启弹窗的选择：写三态 bookkeeping，并同步落地为实际开关。

    必须在主线程调用（写 SessionStore）。
    """
    from .settings import set_telemetry_enabled

    def _merge(existing):
        existing = existing if isinstance(existing, dict) else {}
        server = existing if isinstance(existing, dict) else {}
        node = dict(server.get("telemetry") or {})
        node["consent"] = ConsentState.GRANTED.value if granted else ConsentState.DENIED.value
        node["consent_at"] = datetime.now(timezone.utc).isoformat()
        server["telemetry"] = node
        return server

    get_session_store().mutate_node("server_config", _merge)
    # set_telemetry_enabled 负责：写 settings.network.telemetry + 生成/清除本地标识与缓冲
    set_telemetry_enabled(granted)
    logger.info(
        "[telemetry] 用户" + ("同意" if granted else "拒绝") + "参与匿名数据收集")


def is_network_feature_enabled(feature: NetFeature) -> bool:
    """三条网络行为的统一闸门：离线模式总闸 → 分项开关。"""
    import os
    if os.environ.get("LVJIANG_OFFLINE") == "1":
        return False
    from ..config import load_user_config
    network = load_user_config().network
    if network.offline:
        return False
    if feature is NetFeature.ANNOUNCEMENT:
        return network.announcement
    if feature is NetFeature.UPDATE:
        return network.update
    if feature is NetFeature.TELEMETRY:
        return network.telemetry
    return False
