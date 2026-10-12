"""高级功能授权：旧码离线验签，一次性码首次在线绑定后验证签名记录。"""

from __future__ import annotations

import time
from datetime import date

from .code import Entitlement, License, LicenseError, evaluate, verify_code
from .hardware import current_serial, serial_is_wellformed
from .levels import Level, level_label, load_levels
from .store import clear_code, license_path, load_code, save_code

#: 授权按**等级**划分，等级表见 ``levels.json``（``load_levels()`` 读取）。
#: 激活码正文里写的是 ``lv1`` 这样的等级名，门禁处问的也是等级：功能会增删改名，
#: 等级不会——将来某个功能划进 Lv1，已签发的码自动覆盖它，不用给老用户重发。
#:
#: 多执行目标并发是首个 Lv1 能力：第一个任务不需要授权，同时启动第二个
#: 执行目标时由运行管理器校验 ``has_feature("lv1")``。
#: PC 向手机下发离线配置也属于 Lv1；手机已有配置的离线执行不受此门禁限制。
#: 外部 Agent 智能调律属于 Lv1，所有 MCP 请求（含发现和文档读取）均需激活。

__all__ = [
    "Entitlement",
    "Level",
    "License",
    "LicenseError",
    "clear_code",
    "current_entitlement",
    "current_serial",
    "has_feature",
    "level_label",
    "license_path",
    "load_levels",
    "load_code",
    "refresh_entitlement",
    "save_code",
    "serial_is_wellformed",
    "try_activate",
    "verify_code",
]

#: 进程级缓存：验签和读硬件都不是每次调用都要重做的事
_cached: Entitlement | None = None


def current_entitlement() -> Entitlement:
    """当前授权结论，进程内缓存。"""
    global _cached
    if _cached is None or _cached.expires_at is not None and time.time() >= _cached.expires_at:
        from .activation import stored_entitlement
        _cached = stored_entitlement(current_serial())
        issued = _cached.license
        if issued is not None:
            feats = "、".join(sorted(_cached.features)) or "无"
            from loguru import logger
            logger.info(f"高级功能已激活（{issued.code_id}）：{feats}")
    return _cached


def refresh_entitlement() -> Entitlement:
    """丢弃缓存重新判定（填写或清除激活码后调用）"""
    global _cached
    _cached = None
    return current_entitlement()


def has_feature(feature: str) -> bool:
    """该高级功能当前是否可用。未激活、过期、不匹配一律返回 ``False``。"""
    return current_entitlement().has(feature)


def try_activate(code: str, today: date | None = None) -> Entitlement:
    """校验并保存一张激活码；校验不通过不写盘，返回结论供 UI 提示原因。"""
    try:
        issued = verify_code(code)
    except LicenseError as error:
        return Entitlement(reason=str(error))
    serial = current_serial()
    if issued.bind == "claim":
        from .activation import activate_online
        result = activate_online(code, serial)
    else:
        result = evaluate(code, serial, today)
        if result.active and not save_code(code):
            return Entitlement(reason="保存激活码失败，原认证保留")
    if result.active:
        global _cached
        _cached = result
    return result
